"""Review service (Round 6) — audited human decisions over deterministic output.

The trust doctrine this module enforces (docs/domain-model.md §Audit trail,
§Invariants — "Human quantity corrections never mutate the original
Measurement row"):
  * ``MeasurementModel.value`` is the engine's deterministic output and is
    IMMUTABLE after the run. A human correction writes ``corrected_value`` on
    the SAME row (the original stays visible forever) and appends an audit
    row carrying before/after — the correction is never a new engine claim,
  * every state change goes through core.domain.states.transition_measurement
    (never a direct column assignment). Where the parity flip has no direct
    edge (MEASURED_ZERO -> MEASURED and back) the machine's own review pivot
    is walked: MEASURED_ZERO -> NEEDS_REVIEW -> MEASURED, every hop validated.
    An illegal machine move surfaces as a 409 carrying the machine's message,
  * correcting a measurement that feeds a BOQ past DRAFT is refused with 409
    "re-approve first" — never a silent invalidation of a reviewed/approved
    document. DRAFT BOQs pick corrections up later (boq_service.recompute_boq
    consumes corrected values),
  * element classification overrides set element_type + type_source=human_set
    and PRESERVE ai_confidence/ai_model/ai_explanation — the AI's provenance
    is never erased; the human column records the override, the audit row
    records both sides.

Correction units: the request carries no unit, so a corrected value is
dimensionally bound to the row's own unit (stored beside it, audited with it).
There is nothing to convert and no unit field to lie with.

BOQ containment scan: BoqItem.measurement_ids is a JSONB list — a Python-side
scan over the project's non-draft BOQs' items is fine at V1 scale (tens of
BOQs); a containment query is the post-V1 optimization, documented here.

Layering (import-linter "Domain service layering"): this module imports
core domain types + backend models only — no FastAPI. Routers translate
ReviewServiceError into problem+json.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    Element,
    EvidenceLinkModel,
    MeasurementModel,
    MeasurementRun,
    Project,
)
from core.domain.enums import (
    AuditAction,
    BoqStatus,
    ElementType,
    ElementTypeSource,
    MeasurementState,
)
from core.domain.states import transition_measurement

# BOQ statuses past DRAFT: a measurement feeding any of these cannot be
# corrected — the approval chain must be walked back first (reject -> DRAFT,
# recompute, re-review). Never a silent invalidation.
FROZEN_BOQ_STATUSES: tuple[str, ...] = (
    BoqStatus.IN_REVIEW.value,
    BoqStatus.REVIEWED.value,
    BoqStatus.APPROVED.value,
    BoqStatus.EXPORTED.value,
    BoqStatus.STALE_APPROVED.value,
)

# Only these states may be accepted/corrected. blocked/not_measurable rows
# leave the queue through exception resolution or a new run — a review action
# on them would fake authority over refused data.
REVIEWABLE_STATES: tuple[MeasurementState, ...] = (
    MeasurementState.MEASURED,
    MeasurementState.MEASURED_ZERO,
    MeasurementState.NEEDS_REVIEW,
)

AUDIT_PAGE_MAX = 500


class ReviewServiceError(RuntimeError):
    """Refusal with a machine-readable code for the API layer."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Coerce an id to a native uuid.

    ORM rows are typed Mapped[str] but asyncpg returns pgproto UUID objects
    for Uuid columns — a selected row hands us a UUID while hand-built rows
    hand us str. Accept both.
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True


def _effective_value(row: MeasurementModel) -> Decimal:
    """The number this measurement currently bills: the human's correction
    when present, otherwise the engine's deterministic output."""
    raw = row.corrected_value if row.corrected_value is not None else row.value
    return Decimal(str(raw)) if raw is not None else Decimal(0)


def _state_hops(current: MeasurementState, target: MeasurementState) -> list[MeasurementState]:
    """Legal path from ``current`` to ``target`` through the state machine.

    Direct edge when the machine has one (or none at all for a same-state
    confirmation); otherwise the machine's review pivot (NEEDS_REVIEW) is
    walked in two validated hops — the parity flips (MEASURED_ZERO -> MEASURED
    and back) have no direct edge BY DESIGN ("a confirmed zero is never
    silently converted"; the correction IS the review record justifying the
    path). Anything still illegal raises the machine's own IllegalTransition
    message so the API refuses with it (409).
    """
    if target == current:
        return []
    try:
        transition_measurement(current, target)
        return [target]
    except ValueError as direct_exc:
        try:
            transition_measurement(current, MeasurementState.NEEDS_REVIEW)
            transition_measurement(MeasurementState.NEEDS_REVIEW, target)
        except ValueError:
            raise direct_exc from None
        return [MeasurementState.NEEDS_REVIEW, target]


async def _owned_project(
    session: AsyncSession, *, project_id: str, user_id: str
) -> Project:
    """Project scoped to the caller (creator scoping, the 404 pattern).

    The uuid pre-check keeps malformed ids an honest 404 — an invalid UUID
    compared against the Uuid column would raise asyncpg's data error first.
    """
    if not _is_uuid(project_id):
        raise ReviewServiceError("not_found", "project not found", 404)
    project = (await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.created_by == user_id,
            Project.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if project is None:
        raise ReviewServiceError("not_found", "project not found", 404)
    return project


async def owned_project_or_404(
    session: AsyncSession, *, project_id: str, user_id: str
) -> Project:
    """Public ownership resolver (routers translate the 404 into problem+json)."""
    return await _owned_project(session, project_id=project_id, user_id=user_id)


async def resolve_measurement(
    session: AsyncSession, *, measurement_ref: str, user_id: str
) -> tuple[MeasurementModel, MeasurementRun, Project]:
    """Resolve a measurement by row id or durable identity, ownership-scoped.

    The durable measurement_id is unique PER RUN, never globally — two runs
    of the same drawing share identities (the replay digest is content-
    bound). A global lookup by identity therefore returns MULTIPLE rows the
    moment a user has run the same drawing twice (the dev DB does exactly
    that across E2E journeys). The identity branch is scoped to the
    caller's projects; a genuine same-user ambiguity is an honest 409
    naming the disambiguator (the row id), never a 500 and never a
    first-row pick.

    The uuid pre-check is not optional: a non-UUID ref compared against the
    Uuid id column would raise asyncpg's data error (a 500) before any
    not-found check could answer — the documented cast trap.
    """
    is_uuid = _is_uuid(measurement_ref)
    if is_uuid:
        # Row id: globally unique PK — resolve directly, ownership below.
        m = (await session.execute(
            select(MeasurementModel).where(
                MeasurementModel.id == measurement_ref)
        )).scalar_one_or_none()
        if m is not None:
            run = (await session.execute(
                select(MeasurementRun).where(MeasurementRun.id == m.run_id)
            )).scalar_one()
            project = await _owned_project(
                session, project_id=str(run.project_id), user_id=user_id)
            return m, run, project
        # Not a row id — fall through to the durable-identity branch.
    # Durable identity: scoped to the caller's runs (per-run uniqueness).
    rows = (await session.execute(
        select(MeasurementModel, MeasurementRun)
        .join(MeasurementRun, MeasurementModel.run_id == MeasurementRun.id)
        .join(Project, MeasurementRun.project_id == Project.id)
        .where(
            MeasurementModel.measurement_id == measurement_ref,
            Project.created_by == user_id,
            Project.deleted_at.is_(None),
        )
        .order_by(MeasurementRun.created_at, MeasurementModel.created_at,
                  MeasurementModel.id)
    )).all()
    if not rows:
        raise ReviewServiceError("not_found", "measurement not found", 404)
    if len(rows) > 1:
        # Same user, more than one run of the same drawing: the identity
        # alone cannot pick a row. Name the disambiguator — never 500,
        # never first-by-order.
        raise ReviewServiceError(
            "ambiguous_measurement_identity",
            f"durable measurement identity {measurement_ref} matches "
            f"{len(rows)} runs of yours — address the row by its id",
            409)
    m, run = rows[0]
    project = await _owned_project(
        session, project_id=str(run.project_id), user_id=user_id)
    return m, run, project


async def _boq_requiring_reapproval(
    session: AsyncSession, *, project_id: str, measurement: MeasurementModel
) -> tuple[str, str] | None:
    """(boq_id, boq_status) of the first BOQ past DRAFT whose items reference
    this measurement, or None. V1 scan documented in the module docstring."""
    items = (await session.execute(
        select(BoqItem, BoqModel.id, BoqModel.status)
        .join(BoqSection, BoqItem.section_id == BoqSection.id)
        .join(BoqModel, BoqSection.boq_id == BoqModel.id)
        .where(
            BoqModel.project_id == project_id,
            BoqModel.status.in_(FROZEN_BOQ_STATUSES),
        )
    )).all()
    refs = {str(measurement.measurement_id), str(measurement.id)}
    for item, boq_id, boq_status in items:
        if item.measurement_ids and refs.intersection(set(item.measurement_ids)):
            return str(boq_id), str(boq_status)
    return None


def _value_out(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


async def _evidence_out(
    session: AsyncSession, measurement: MeasurementModel
) -> list[dict[str, str | None]]:
    rows = (await session.execute(
        select(EvidenceLinkModel).where(
            EvidenceLinkModel.subject_type == "measurement",
            EvidenceLinkModel.subject_id == _as_uuid(measurement.id),
        )
    )).scalars().all()
    return [
        {"kind": e.kind, "ref": e.ref, "note": e.note}
        for e in rows
    ]


def _measurement_out(
    measurement: MeasurementModel,
    evidence: list[dict[str, str | None]],
) -> dict[str, Any]:
    """Quantity-bearing response: original value AND corrected_value AND
    state AND provenance refs (api-contract rule 2)."""
    return {
        "id": str(measurement.id),
        "measurement_id": measurement.measurement_id,
        "run_id": str(measurement.run_id),
        "element_id": str(measurement.element_id),
        "quantity_type": measurement.quantity_type,
        "value": _value_out(measurement.value),
        "corrected_value": _value_out(measurement.corrected_value),
        "unit": measurement.unit,
        "rule_id": measurement.rule_id,
        "engine_version": measurement.engine_version,
        "inputs": measurement.inputs or [],
        "inputs_digest": measurement.inputs_digest,
        "state": measurement.state,
        "label": measurement.label,
        "evidence": evidence,
        "evidence_count": len(evidence),
    }


async def review_measurement(
    session: AsyncSession,
    *,
    measurement_ref: str,
    action: str,
    value: Decimal | None,
    reason: str,
    actor: str | uuid.UUID,
) -> dict[str, Any]:
    """POST /measurements/{id}/review — the ONLY way a quantity may change.

    accept: a human confirms the engine's output; the row transitions to the
    state matching its effective value (no value is written).
    correct: the engine's value column is NEVER touched; the human's number
    lands in corrected_value beside it, the state follows the corrected
    value through the machine, and the audit row carries before/after.
    """
    m, _run, project = await resolve_measurement(
        session, measurement_ref=measurement_ref, user_id=str(actor))
    if action not in ("accept", "correct"):
        raise ReviewServiceError(
            "invalid_action", f"unknown review action {action!r}", 422)

    current = MeasurementState(str(m.state))
    if current not in REVIEWABLE_STATES:
        raise ReviewServiceError(
            "state_not_reviewable",
            f"measurement is {current.value!r} — blocked/not_measurable rows "
            "change through exception resolution or a new run, not review",
            409)

    before_state = m.state
    before_value = _value_out(m.value)
    before_corrected = _value_out(m.corrected_value)

    if action == "accept":
        # Target from the row's effective value: zero bills as MEASURED_ZERO.
        effective = _effective_value(m)
        target = (MeasurementState.MEASURED_ZERO if effective == 0
                  else MeasurementState.MEASURED)
        audit_action = AuditAction.ACCEPT_MEASUREMENT.value
        after_value, after_corrected = before_value, before_corrected
    else:
        if value is None:
            raise ReviewServiceError(
                "value_required", "a corrected value is required", 422)
        if not value.is_finite():
            raise ReviewServiceError(
                "invalid_value", "corrected value must be finite", 422)
        # BOQ protection BEFORE any mutation: a correction under a reviewed/
        # approved BOQ would silently invalidate the approval — refused.
        claim = await _boq_requiring_reapproval(
            session, project_id=str(project.id), measurement=m)
        if claim is not None:
            raise ReviewServiceError(
                "reapprove_first",
                f"measurement feeds BOQ {claim[0]} in status {claim[1]!r} — "
                "re-approve first (reject to DRAFT, recompute, re-review)",
                409)
        # The correction: original value column untouched, always visible.
        m.corrected_value = value
        target = (MeasurementState.MEASURED_ZERO if value == 0
                  else MeasurementState.MEASURED)
        audit_action = AuditAction.CORRECT_QUANTITY.value
        after_value, after_corrected = before_value, _value_out(value)

    # Every state change walks the machine (ValueError -> 409 with its message).
    state = current
    for hop in _state_hops(current, target):
        state = transition_measurement(state, hop)
    m.state = state.value

    audit = AuditEntry(
        id=str(uuid.uuid4()),
        actor=_as_uuid(actor),
        action=audit_action,
        subject_type="measurement",
        subject_id=_as_uuid(m.id),
        project_id=_as_uuid(project.id),
        before={"state": before_state, "value": before_value,
                "corrected_value": before_corrected},
        after={"state": m.state, "value": after_value,
               "corrected_value": after_corrected},
        reason=reason,
    )
    session.add(audit)
    await session.flush()

    evidence = await _evidence_out(session, m)
    return {
        "ok": True,
        "audit_id": str(audit.id),
        "measurement": _measurement_out(m, evidence),
    }


# ---------------------------------------------------------------------------
# T073 — element classification override
# ---------------------------------------------------------------------------


async def resolve_element(
    session: AsyncSession, *, element_id: str, user_id: str
) -> tuple[Element, MeasurementRun, Project]:
    """Element + its run + the caller's project (ownership mirrors runs.py's
    _owned_run). The uuid pre-check keeps malformed ids an honest 404."""
    try:
        uuid.UUID(str(element_id))
    except (ValueError, TypeError) as exc:
        raise ReviewServiceError("not_found", "element not found", 404) from exc
    element = (await session.execute(
        select(Element).where(Element.id == element_id)
    )).scalar_one_or_none()
    if element is None:
        raise ReviewServiceError("not_found", "element not found", 404)
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == element.run_id)
    )).scalar_one()
    project = await _owned_project(
        session, project_id=str(run.project_id), user_id=user_id)
    return element, run, project


def element_out(element: Element) -> dict[str, Any]:
    """Element with the AI provenance visible (it is preserved on override)."""
    return {
        "id": str(element.id),
        "run_id": str(element.run_id),
        "sheet_id": str(element.sheet_id),
        "element_type": element.element_type,
        "type_source": element.type_source,
        "ai_confidence": (str(element.ai_confidence)
                          if element.ai_confidence is not None else None),
        "ai_model": element.ai_model,
        "ai_explanation": element.ai_explanation,
        "label": element.label,
    }


async def override_element_type(
    session: AsyncSession,
    *,
    element_id: str,
    element_type: str,
    reason: str,
    actor: str | uuid.UUID,
) -> dict[str, Any]:
    """POST /elements/{id}/classification — the human's classification word.

    The override is recorded as type_source=human_set; the AI's provenance
    (ai_confidence/ai_model/ai_explanation) is PRESERVED, never cleared —
    both voices stay on the row and both sides land in the audit entry.
    """
    try:
        parsed = ElementType(element_type)
    except ValueError as exc:
        legal = ", ".join(t.value for t in ElementType)
        raise ReviewServiceError(
            "invalid_element_type",
            f"unknown element_type {element_type!r}; legal values: {legal}",
            422,
        ) from exc

    element, _run, project = await resolve_element(
        session, element_id=element_id, user_id=str(actor))

    before = {
        "element_type": element.element_type,
        "type_source": element.type_source,
        "ai_confidence": (str(element.ai_confidence)
                          if element.ai_confidence is not None else None),
    }
    element.element_type = parsed.value
    element.type_source = ElementTypeSource.HUMAN_SET.value

    audit = AuditEntry(
        id=str(uuid.uuid4()),
        actor=_as_uuid(actor),
        action=AuditAction.OVERRIDE_ELEMENT_TYPE.value,
        subject_type="element",
        subject_id=_as_uuid(element.id),
        project_id=_as_uuid(project.id),
        before=before,
        after={"element_type": parsed.value,
               "type_source": ElementTypeSource.HUMAN_SET.value},
        reason=reason,
    )
    session.add(audit)
    await session.flush()
    return {"ok": True, "audit_id": str(audit.id), "element": element_out(element)}


# ---------------------------------------------------------------------------
# T075 — audit trail reads
# ---------------------------------------------------------------------------


def _audit_out(row: AuditEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "at": row.at.isoformat(),
        "action": row.action,
        "actor": str(row.actor),
        "subject_type": row.subject_type,
        "subject_id": str(row.subject_id),
        "project_id": str(row.project_id) if row.project_id is not None else None,
        "before": row.before,
        "after": row.after,
        "reason": row.reason,
    }


async def list_audit_entries(
    session: AsyncSession,
    *,
    project_id: str,
    subject_type: str | None = None,
    actor: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 100,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    """GET /projects/{pid}/audit — the project's review trail.

    Deterministic pagination: ordered (at DESC, id DESC); the before-cursor
    is the id of the last row seen and continues strictly after it in that
    order (row-value less-than on (at, id)). Filters compose with AND. All
    ids/timestamps are validated by pydantic Query types at the router
    BEFORE any SQL — the asyncpg UUID-cast trap never reaches the database.
    """
    limit = max(1, min(limit, AUDIT_PAGE_MAX))
    q = select(AuditEntry).where(AuditEntry.project_id == project_id)
    if subject_type is not None and subject_type != "":
        q = q.where(AuditEntry.subject_type == subject_type)
    if actor is not None:
        q = q.where(AuditEntry.actor == actor)
    if since is not None:
        # Normalize at the boundary: a naive ISO timestamp is UTC.
        since = since.replace(tzinfo=UTC) if since.tzinfo is None else since
        q = q.where(AuditEntry.at >= since)
    if before is not None:
        cursor = (await session.execute(
            select(AuditEntry.at).where(
                AuditEntry.id == before,
                AuditEntry.project_id == project_id,
            )
        )).scalar_one_or_none()
        if cursor is None:
            # Unknown/stale/foreign cursor: an honest empty page, not a guess.
            return {"items": [], "next_cursor": None}
        q = q.where(or_(
            AuditEntry.at < cursor,
            and_(AuditEntry.at == cursor, AuditEntry.id < before),
        ))
    rows = (await session.execute(
        q.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit)
    )).scalars().all()
    next_cursor = str(rows[-1].id) if len(rows) == limit else None
    return {"items": [_audit_out(r) for r in rows], "next_cursor": next_cursor}
