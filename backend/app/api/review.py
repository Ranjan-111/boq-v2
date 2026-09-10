"""Measurements & exceptions API (Round 4) — evidence, review actions.

GET /measurements/{id}/evidence powers the viewer highlight: the persisted
geometry + source handles behind a measurement (docs/api-contract.md).
POST /exceptions/{id}/resolve records a human decision + audit row — the only
way an exception leaves the blocker queue.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import (
    AuditEntry,
    DrawingSheet,
    Element,
    EvidenceLinkModel,
    ExceptionModel,
    GeometryModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    User,
)
from core.domain.enums import AuditAction

router = APIRouter(tags=["review"])


async def _measurement_row(
    session: AsyncSession, measurement_ref: str, user: User
) -> tuple[MeasurementModel, MeasurementRun]:
    """Resolve a measurement by durable identity (measurement_id) or row id.

    The viewer passes the durable measurement_id from the list payload; the
    row id also resolves (both are honest references to the same row).
    """
    m = (await session.execute(
        select(MeasurementModel).where(
            (MeasurementModel.measurement_id == measurement_ref)
            | (MeasurementModel.id == measurement_ref))
    )).scalar_one_or_none()
    if m is None:
        raise problem_error(404, "not_found", "measurement not found")
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == m.run_id)
    )).scalar_one()
    # Ownership: the run's project must be the user's (creator scoping).
    project = (await session.execute(
        select(Project).where(Project.id == run.project_id,
                              Project.created_by == user.id,
                              Project.deleted_at.is_(None))
    )).scalar_one_or_none()
    if project is None:
        raise problem_error(404, "not_found", "measurement not found")
    return m, run


@router.get("/measurements/{measurement_ref}/evidence")
async def get_measurement_evidence(
    measurement_ref: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    m, run = await _measurement_row(session, measurement_ref, user)
    evidence_rows = (await session.execute(
        select(EvidenceLinkModel).where(
            EvidenceLinkModel.subject_type == "measurement",
            EvidenceLinkModel.subject_id == uuid.UUID(str(m.id)),
        )
    )).scalars().all()
    # Geometry of the element the measurement belongs to + the drawing's
    # source geometry re-derived from the evidence refs (handles). V1 keeps it
    # honest: return the element geometry rows + raw evidence links.
    element = (await session.execute(
        select(Element).where(Element.id == m.element_id)
    )).scalar_one_or_none()
    geometries: list[dict[str, Any]] = []
    if element is not None:
        geo_rows = (await session.execute(
            select(GeometryModel).where(GeometryModel.element_id == element.id)
        )).scalars().all()
        for g in geo_rows:
            geometries.append({
                "geom_type": g.geom_type, "coordinates": g.coordinates,
                "source_format": g.source_format,
                "source_handles": g.source_handles,
                # V1 geometry rows carry layer per source handle; expose the
                # first non-null one for viewer grouping.
                "layer": next((h.get("layer") for h in g.source_handles
                               if h.get("layer")), None),
            })
    sheet = (await session.execute(
        select(DrawingSheet).where(DrawingSheet.id == (
            select(Element.sheet_id).where(Element.id == m.element_id).scalar_subquery()
        ))
    )).scalar_one_or_none()
    return {
        "id": str(m.id), "measurement_id": m.measurement_id,
        "run_id": str(run.id), "state": m.state, "label": m.label,
        "quantity_type": m.quantity_type,
        "value": str(m.value) if m.value is not None else None, "unit": m.unit,
        "evidence": [
            {"id": str(e.id), "kind": e.kind, "ref": e.ref, "note": e.note}
            for e in evidence_rows
        ],
        "geometry": geometries,
        "sheet": ({"id": str(sheet.id), "sheet_ref": sheet.sheet_ref}
                  if sheet is not None else None),
    }


class ResolveBody(BaseModel):
    resolution: str = Field(min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/exceptions/{exception_id}/resolve")
async def resolve_exception(
    exception_id: str,
    body: ResolveBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    exc_row = (await session.execute(
        select(ExceptionModel).where(ExceptionModel.id == exception_id)
    )).scalar_one_or_none()
    if exc_row is None:
        raise problem_error(404, "not_found", "exception not found")
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == exc_row.run_id)
    )).scalar_one()
    project = (await session.execute(
        select(Project).where(Project.id == run.project_id,
                              Project.created_by == user.id,
                              Project.deleted_at.is_(None))
    )).scalar_one_or_none()
    if project is None:
        raise problem_error(404, "not_found", "exception not found")
    if exc_row.resolved_at is not None:
        raise problem_error(409, "already_resolved", "exception is already resolved")
    before = {"resolved_at": None, "resolution": exc_row.resolution}
    exc_row.resolved_at = datetime.now(UTC)
    exc_row.resolution = body.resolution
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=user.id,
        action=AuditAction.RESOLVE_EXCEPTION.value,
        subject_type="exception", subject_id=uuid.UUID(str(exc_row.id)),
        before=before,
        after={"resolution": body.resolution, "note": body.note},
        reason=body.note,
    ))
    await session.flush()
    return {"ok": True, "id": str(exc_row.id), "resolved_at": exc_row.resolved_at.isoformat()}
