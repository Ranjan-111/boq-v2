"""Measurements & exceptions API (Round 4/6) — evidence, review actions.

GET /measurements/{id}/evidence powers the viewer highlight: the persisted
geometry + source handles behind a measurement (docs/api-contract.md).
POST /exceptions/{id}/resolve records a human decision + audit row — the only
way an exception leaves the blocker queue.

Round 6 (T072/T073/T075):
POST /measurements/{id}/review        accept | correct — audited quantity
                                     review; the only way a quantity changes.
POST /elements/{id}/classification    human override of the element type.
GET  /projects/{pid}/audit            the project's review trail with
                                     composable filters + deterministic
                                     pagination.

Round 7 (T084):
POST /measurements/{ref}/map         human mapping of a measurement to a
                                     catalogue item — the unmapped blocker
                                     resolution; appends the mapped BoqItem
                                     to the run's DRAFT BOQ.
POST /ai/suggestions/{id}/apply       the human acting on an advisory
                                     proposal; structurally quantity-proof
                                     (closed allowlist of kinds).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
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
from backend.app.services import review_service
from backend.app.services.review_service import ReviewServiceError
from core.domain.enums import AuditAction

router = APIRouter(tags=["review"])


async def _measurement_row(
    session: AsyncSession, measurement_ref: str, user: User
) -> tuple[MeasurementModel, MeasurementRun]:
    """Resolve a measurement by durable identity (measurement_id) or row id.

    The viewer passes the durable measurement_id from the list payload; the
    row id also resolves (both are honest references to the same row). The
    resolution itself (incl. the asyncpg UUID-cast guard) lives in
    review_service.resolve_measurement — a non-UUID ref queries the
    measurement_id column only, so malformed refs stay an honest 404.
    """
    return (await review_service.resolve_measurement(
        session, measurement_ref=measurement_ref, user_id=str(user.id)))[:2]


@router.get("/measurements/{measurement_ref}/evidence")
async def get_measurement_evidence(
    measurement_ref: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    try:
        m, run = await _measurement_row(session, measurement_ref, user)
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc
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
        "value": str(m.value) if m.value is not None else None,
        "corrected_value": (str(m.corrected_value)
                            if m.corrected_value is not None else None),
        "unit": m.unit,
        "element": (review_service.element_out(element)
                    if element is not None else None),
        "evidence": [
            {"id": str(e.id), "kind": e.kind, "ref": e.ref, "note": e.note}
            for e in evidence_rows
        ],
        "geometry": geometries,
        "sheet": ({"id": str(sheet.id), "sheet_ref": sheet.sheet_ref}
                  if sheet is not None else None),
    }


# ---------------------------------------------------------------------------
# T072 — audited quantity review
# ---------------------------------------------------------------------------


class MeasurementReviewBody(BaseModel):
    """The human's review statement. Corrected values are finite, >= 0 and
    carry at most 6 decimal places (NUMERIC(18,6), api-contract "≤6 dp").
    The unit is the row's own unit — the request carries none, so a
    correction is dimensionally bound to the measurement it corrects."""

    action: Literal["accept", "correct"]
    value: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v

    @model_validator(mode="after")
    def _correct_requires_value(self) -> MeasurementReviewBody:
        if self.action == "correct" and self.value is None:
            raise ValueError("a corrected value is required for action='correct'")
        return self


@router.post("/measurements/{measurement_ref}/review")
async def review_measurement(
    measurement_ref: str,
    body: MeasurementReviewBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """Audited accept/correct — audit row + state transition (api-contract).

    The response carries the measurement (original value AND corrected_value
    AND state AND provenance refs) plus the audit row id.
    """
    try:
        return await review_service.review_measurement(
            session, measurement_ref=measurement_ref, action=body.action,
            value=body.value, reason=body.reason, actor=user.id)
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# T073 — element classification override
# ---------------------------------------------------------------------------


class ClassificationBody(BaseModel):
    element_type: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v


@router.post("/elements/{element_id}/classification")
async def override_element_classification(
    element_id: str,
    body: ClassificationBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """Human override of the element type (AI provenance preserved + audited)."""
    try:
        return await review_service.override_element_type(
            session, element_id=element_id, element_type=body.element_type,
            reason=body.reason, actor=user.id)
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# T084 — manual mapping + suggestion apply
# ---------------------------------------------------------------------------


class MapCatalogueBody(BaseModel):
    """The human's mapping statement: which catalogue item bills this
    measurement, and why (the audit row carries the reason verbatim)."""

    catalogue_item_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v


@router.post("/measurements/{measurement_ref}/map")
async def map_measurement_to_catalogue(
    measurement_ref: str,
    body: MapCatalogueBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """Map a measurement to a catalogue item — the unmapped blocker's human
    resolution (appends the priced BoqItem to the run's DRAFT BOQ)."""
    try:
        return await review_service.map_measurement_to_catalogue(
            session, measurement_ref=measurement_ref,
            catalogue_item_id=body.catalogue_item_id,
            reason=body.reason, user_id=str(user.id))
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


class ApplySuggestionBody(BaseModel):
    """The human's reason for acting on an advisory proposal (audited)."""

    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v


@router.post("/ai/suggestions/{suggestion_id}/apply")
async def apply_suggestion(
    suggestion_id: str,
    body: ApplySuggestionBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """Apply an advisory suggestion — quantity-proof by construction: only
    the element_classification kind applies (as the T073 override), anything
    else refuses without touching a row."""
    try:
        return await review_service.apply_suggestion(
            session, suggestion_id=suggestion_id, reason=body.reason,
            user_id=str(user.id))
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# T075 — audit trail reads
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/audit")
async def get_project_audit(
    project_id: str,
    subject_type: str | None = Query(default=None, max_length=30),
    actor: uuid.UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    before: uuid.UUID | None = Query(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """The project's review trail: (at DESC, id DESC) deterministic pagination,
    filters compose with AND. Query params are pydantic-typed so garbage is a
    422 at the boundary — never an asyncpg cast 500 from the database.
    Ownership resolves through the service (the _owned_run 404 pattern)."""
    try:
        project = await review_service.owned_project_or_404(
            session, project_id=project_id, user_id=str(user.id))
        return await review_service.list_audit_entries(
            session, project_id=str(project.id),
            subject_type=subject_type, actor=actor, since=since,
            limit=limit, before=before)
    except ReviewServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# Exception resolution (Round 4)
# ---------------------------------------------------------------------------


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
        # Project-scoped write (Round 6 audit trail): without project_id the
        # resolution is invisible in GET /projects/{pid}/audit.
        project_id=uuid.UUID(str(project.id)),
        before=before,
        after={"resolution": body.resolution, "note": body.note},
        reason=body.note,
    ))
    await session.flush()
    return {"ok": True, "id": str(exc_row.id), "resolved_at": exc_row.resolved_at.isoformat()}
