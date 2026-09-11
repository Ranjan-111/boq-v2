"""Runs API (Round 4) — create measurement runs, list results (api-contract.md).

POST /projects/{pid}/runs enqueues the measurement_run job; the worker
executes it against STORED drawing bytes with the persisted calibration.
Every read endpoint filters by state/quantity_type like the contract says.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
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
    Element,
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
    project_id: str
    status: str
    stats: dict[str, int] | None
    error: str | None
    engine_version: str | None

    model_config = {"from_attributes": True}


def _run_out(r: MeasurementRun) -> dict[str, Any]:
    return {
        "id": str(r.id), "project_id": str(r.project_id), "status": r.status,
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
    session: AsyncSession, run_id: str, user: User
) -> MeasurementRun:
    """Run readable only through its project's creator (contract: /runs/{id}).

    A malformed run_id is an honest 404, never a 500: the Uuid column cast
    would raise asyncpg's data error on non-UUID input before the not-found
    check can answer.
    """
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise problem_error(404, "not_found", "run not found") from exc
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        raise problem_error(404, "not_found", "run not found")
    project = (await session.execute(
        select(Project).where(Project.id == run.project_id,
                              Project.created_by == user.id,
                              Project.deleted_at.is_(None))
    )).scalar_one_or_none()
    if project is None:
        raise problem_error(404, "not_found", "run not found")
    return run


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    return _run_out(await _owned_run(session, run_id, user))


@router.get("/runs/{run_id}/measurements")
async def list_run_measurements(
    run_id: str,
    state: str | None = Query(default=None),
    quantity_type: str | None = Query(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user)
    q = select(MeasurementModel, Element).join(
        Element, MeasurementModel.element_id == Element.id).where(
        MeasurementModel.run_id == run.id)
    if state:
        q = q.where(MeasurementModel.state == state)
    if quantity_type:
        q = q.where(MeasurementModel.quantity_type == quantity_type)
    rows = (await session.execute(q.order_by(
        MeasurementModel.created_at, MeasurementModel.id))).all()
    from backend.app.db.models import EvidenceLinkModel
    ev_rows: Sequence[EvidenceLinkModel] = ()
    if rows:
        ev_rows = (await session.execute(
            select(EvidenceLinkModel)
            .where(EvidenceLinkModel.subject_type == "measurement",
                   EvidenceLinkModel.subject_id.in_(
                       [uuid.UUID(str(m.id)) for m, _e in rows]))
        )).scalars().all()
    ev_by_subject: dict[str, list[EvidenceLinkModel]] = {}
    for e in ev_rows:
        ev_by_subject.setdefault(str(e.subject_id), []).append(e)
    return {"items": [
        {
            "id": str(m.id), "measurement_id": m.measurement_id,
            "element_id": str(m.element_id), "quantity_type": m.quantity_type,
            "value": str(m.value) if m.value is not None else None,
            # The human correction, beside the immutable engine value
            # (api-contract rule 2: original always visible; corrected_value
            # is what a BOQ bills — round 6 review workspace).
            "corrected_value": (str(m.corrected_value)
                                if m.corrected_value is not None else None),
            "unit": m.unit, "rule_id": m.rule_id, "state": m.state,
            "label": m.label, "element_label": el.label,
            # Element classification context (round 6 override UI): the
            # measurement list is where elements surface in V1 — the review
            # workspace groups by element_id and offers the audited
            # POST /elements/{id}/classification with these as the context.
            "element_type": el.element_type, "type_source": el.type_source,
            "evidence_count": len(ev_by_subject.get(str(m.id), [])),
            "evidence": [
                {"kind": e.kind, "ref": e.ref, "note": e.note}
                for e in ev_by_subject.get(str(m.id), [])
            ],
            "inputs_digest": m.inputs_digest,
        }
        for m, el in rows
    ]}


@router.get("/runs/{run_id}/exceptions")
async def list_run_exceptions(
    run_id: str,
    severity: str | None = Query(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user)
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


@router.post("/runs/{run_id}/analyze", status_code=202)
async def start_analyze(
    run_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, str]:
    """Kick off the AI understanding pass (contract: separate job).

    The pass is advisory-only by construction: its handler writes prompt
    logs + ai_suggestions and nothing else (T060/T065 doctrine). The job
    result carries the honest outcome; an unconfigured/broken provider
    fails the job — never a faked suggestion.
    """
    run = await _owned_run(session, run_id, user)
    try:
        job_id = await submit(session, JobSpec(
            kind="ai_analyze",
            payload={"run_id": str(run.id)},
            idempotency_key=f"analyze:{run.id}",
        ))
    except DuplicateJob as exc:
        raise problem_error(409, "analyze_already_active", str(exc)) from exc
    return {"job_id": job_id}


@router.get("/runs/{run_id}/ai/insights")
async def get_ai_insights(
    run_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """Advisory rows for the run — read-only relative to deterministic data."""
    from backend.app.services.ai_service import AnalyzeError, load_insights

    run = await _owned_run(session, run_id, user)
    try:
        return await load_insights(session, run_id=str(run.id))
    except AnalyzeError as exc:
        raise problem_error(404, "not_found", "run not found") from exc
