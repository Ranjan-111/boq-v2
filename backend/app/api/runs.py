"""Runs API (Round 4) — create measurement runs, list results (api-contract.md).

POST /projects/{pid}/runs enqueues the measurement_run job; the worker
executes it against STORED drawing bytes with the persisted calibration.
Every read endpoint filters by state/quantity_type like the contract says.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import owned_project, problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import (
    DrawingSheet,
    ExceptionModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    User,
)
from backend.app.jobs.queue import DuplicateJob, JobSpec, submit

router = APIRouter(tags=["runs"])


class RunCreate(BaseModel):
    drawing_file_id: str
    sheet_id: str
    options: RunOptions | None = None


class RunOptions(BaseModel):
    max_wall_thickness: float = Field(default=250.0, gt=0, le=10_000)


class RunOut(BaseModel):
    id: str
    status: str
    stats: dict[str, int] | None
    error: str | None
    engine_version: str | None

    model_config = {"from_attributes": True}


def _run_out(r: MeasurementRun) -> dict[str, Any]:
    return {
        "id": str(r.id), "status": r.status,
        "stats": r.stats, "error": r.error, "engine_version": r.engine_version,
    }


@router.post("/projects/{project_id}/runs", status_code=202)
async def create_run(
    project_id: str,
    body: RunCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, str]:
    # The sheet must belong to this project's drawing files (parse_status is
    # checked by the run service when the job executes — honest failure there).
    sheet = (await session.execute(
        select(DrawingSheet).where(DrawingSheet.id == body.sheet_id)
    )).scalar_one_or_none()
    if sheet is None:
        raise problem_error(404, "sheet_not_found", "sheet not found")
    run = MeasurementRun(
        id=str(uuid.uuid4()), project_id=project.id, status="queued",
        params={
            "drawing_file_id": body.drawing_file_id,
            "sheet_id": body.sheet_id,
            "max_wall_thickness": (body.options.max_wall_thickness
                                   if body.options else 250.0),
        },
    )
    session.add(run)
    await session.flush()
    try:
        job_id = await submit(session, JobSpec(
            kind="measurement_run",
            payload={"run_id": run.id,
                     "max_wall_thickness": (body.options.max_wall_thickness
                                            if body.options else 250.0)},
            idempotency_key=f"run:{run.id}",
        ))
    except DuplicateJob as exc:
        raise problem_error(409, "run_already_active", str(exc)) from exc
    return {"run_id": run.id, "job_id": job_id}


async def _owned_run(
    session: AsyncSession, run_id: str, project_id: str
) -> MeasurementRun:
    run = (await session.execute(
        select(MeasurementRun).where(
            MeasurementRun.id == run_id,
            MeasurementRun.project_id == project_id,
        )
    )).scalar_one_or_none()
    if run is None:
        raise problem_error(404, "not_found", "run not found")
    return run


@router.get("/projects/{project_id}/runs/{run_id}")
async def get_project_run(
    project_id: str,
    run_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, project.id)
    return _run_out(run)


@router.get("/projects/{project_id}/runs/{run_id}/measurements")
async def list_run_measurements(
    project_id: str,
    run_id: str,
    state: str | None = Query(default=None),
    quantity_type: str | None = Query(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, project.id)
    q = select(MeasurementModel).where(MeasurementModel.run_id == run.id)
    if state:
        q = q.where(MeasurementModel.state == state)
    if quantity_type:
        q = q.where(MeasurementModel.quantity_type == quantity_type)
    rows = (await session.execute(q.order_by(
        MeasurementModel.created_at, MeasurementModel.id))).scalars().all()
    from backend.app.db.models import EvidenceLinkModel
    ev_counts: dict[str, int] = {}
    if rows:
        ev_rows = (await session.execute(
            select(EvidenceLinkModel.subject_id)
            .where(EvidenceLinkModel.subject_type == "measurement",
                   EvidenceLinkModel.subject_id.in_(
                       [uuid.UUID(str(r.id)) for r in rows]))
        )).all()
        for (subject,) in ev_rows:
            ev_counts[str(subject)] = ev_counts.get(str(subject), 0) + 1
    return {"items": [
        {
            "id": str(r.id), "measurement_id": r.measurement_id,
            "element_id": str(r.element_id), "quantity_type": r.quantity_type,
            "value": str(r.value) if r.value is not None else None,
            "unit": r.unit, "rule_id": r.rule_id, "state": r.state,
            "label": r.label, "evidence_count": ev_counts.get(str(r.id), 0),
            "inputs_digest": r.inputs_digest,
        }
        for r in rows
    ]}


@router.get("/projects/{project_id}/runs/{run_id}/exceptions")
async def list_run_exceptions(
    project_id: str,
    run_id: str,
    severity: str | None = Query(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, project.id)
    q = select(ExceptionModel).where(ExceptionModel.run_id == run.id)
    if severity:
        q = q.where(ExceptionModel.severity == severity)
    rows = (await session.execute(q.order_by(
        ExceptionModel.created_at, ExceptionModel.id))).scalars().all()
    return {"items": [
        {
            "id": str(r.id), "code": r.code, "severity": r.severity,
            "message": r.message, "sheet_id": str(r.sheet_id) if r.sheet_id else None,
            "measurement_id": (str(r.measurement_id) if r.measurement_id else None),
            "resolved_at": (r.resolved_at.isoformat() if r.resolved_at else None),
            "resolution": r.resolution,
        }
        for r in rows
    ]}
