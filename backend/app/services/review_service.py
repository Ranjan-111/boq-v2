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

Round 7 (T084 manual mapping + suggestion apply) extends the same doctrine:
  * map_measurement_to_catalogue is the human resolution for the build's
    unmapped_measurement blockers: one measurement -> one catalogue item ->
    one mapped BoqItem in the run's DRAFT BOQ, priced through the same money
    kernel as every other line. A mismatched unit, a measurement outside
    MEASURED/MEASURED_ZERO, or a measurement without evidence is refused —
    the assembly path's own gates, never bypassed because a human clicked.
    Remapping is remove-then-map: a measurement already billed anywhere in
    the project's BOQs refuses with already_mapped naming the existing item,
  * apply_suggestion is the ONLY way an ai_suggestions row changes state,
    and it is structurally quantity-proof: a closed allowlist of applyable
    kinds (element_classification only), a payload guard against quantity
    aliases, and an apply path that writes ONLY element_type/type_source —
    no suggestion kind can ever reach a quantity, a state, or a BOQ row.
    The apply performs the EXACT T073 override semantics (human word lands,
    AI provenance preserved, audit row records both sides).

BOQ containment scan: BoqItem.measurement_ids is a JSONB list — a Python-side
scan over the project's non-draft BOQs' items is fine at V1 scale (tens of
BOQs); a containment query is the post-V1 optimization, documented here.

Layering (import-linter "Domain service layering"): this module imports
core domain types + backend models only — no FastAPI. Routers translate
ReviewServiceError into problem+json. The one service-to-service import is
boq_service's invariant-5 pricing helper _line_total (imported mid-module,
after the service constants, where the mapping section begins): boq_service
imports no review module, so the direction creates no cycle and every priced
line keeps ONE pricing path.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AiSuggestion,
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    Element,
    EvidenceLinkModel,
    ExceptionModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
)
from backend.app.services.boq_service import _line_total
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

# T084 mapping: only authoritative states may be billed by a mapping (the
# assembly path's own gate — build_boq_from_run filters to exactly these).
MAPPABLE_STATES: tuple[MeasurementState, ...] = (
    MeasurementState.MEASURED,
    MeasurementState.MEASURED_ZERO,
)

# The closed allowlist of suggestion kinds apply_suggestion will act on.
# Anything outside it is refused (not_applyable) BEFORE any row is touched —
# a rogue provider writing a novel kind (e.g. "quantity_estimate") can never
# apply it into the product. Adding a kind here is a deliberate policy change.
APPLYABLE_SUGGESTION_KINDS: frozenset[str] = frozenset({"element_classification"})

# Suggestion kinds that change nothing when "applied" — informational rows
# the API refuses politely instead of pretending to act on.
INFORMATIONAL_SUGGESTION_KINDS: frozenset[str] = frozenset(
    {"exception_explanation"})

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


# ---------------------------------------------------------------------------
# T084 — manual mapping to a catalogue item (the human resolution for the
# build's unmapped_measurement blockers)
# ---------------------------------------------------------------------------


async def _select_rate(
    session: AsyncSession, *, catalogue_item: CatalogueItem,
) -> RateModel | None:
    """The item's billable rate: project scope beats default (the same
    selection rule as boq_service.build_boq_from_run — scope desc, first
    row). Vendor rates never bill a mapped measurement."""
    return (await session.execute(
        select(RateModel).where(
            RateModel.catalogue_item_id == catalogue_item.id,
            RateModel.scope.in_(("project", "default")),
        ).order_by(RateModel.scope.desc())
    )).scalars().first()


async def _evidence_count(
    session: AsyncSession, measurement: MeasurementModel
) -> int:
    """Evidence links on the measurement row (MEASURED rows must have them —
    the doctrine's 'evidence mandatory on measured rows' gate)."""
    return len((await session.execute(
        select(EvidenceLinkModel.id).where(
            EvidenceLinkModel.subject_type == "measurement",
            EvidenceLinkModel.subject_id == _as_uuid(measurement.id),
        )
    )).scalars().all())


async def _draft_boq_for_run(
    session: AsyncSession, *, run: MeasurementRun
) -> BoqModel:
    """The run's DRAFT BOQ — the only BOQ a mapping may write into.

    No BOQ at all -> no_draft_boq (build one first); past DRAFT ->
    not_draft (reject it first — the same refusal T086 editing uses, never
    a silent edit of a reviewed/approved document).
    """
    boqs = (await session.execute(
        select(BoqModel).where(
            BoqModel.from_run_id == run.id,
            BoqModel.status == BoqStatus.DRAFT.value,
        ).order_by(BoqModel.created_at.desc(), BoqModel.id.desc())
    )).scalars().all()
    if len(boqs) > 1:
        # Two DRAFTs from one run is a genuinely ambiguous target — which
        # document gains the line? The endpoint takes no boq_id (V1 keeps
        # one BOQ per run by construction), so ambiguity is a blocker,
        # never a newest-first pick (the resolve_measurement doctrine).
        raise ReviewServiceError(
            "ambiguous_draft_boq",
            f"run {run.id} has {len(boqs)} DRAFT BOQs — the mapping target "
            "is ambiguous; remove one draft first", 409)
    if not boqs:
        frozen = (await session.execute(
            select(BoqModel).where(BoqModel.from_run_id == run.id)
            .order_by(BoqModel.created_at.desc(), BoqModel.id.desc())
        )).scalars().first()
        if frozen is not None:
            raise ReviewServiceError(
                "not_draft",
                f"the run's BOQ {frozen.id} is {frozen.status!r} — reject it "
                "first to return to draft (remap after re-approval)", 409)
        raise ReviewServiceError(
            "no_draft_boq",
            f"run {run.id} has no DRAFT BOQ — build one from the run first "
            "(POST /projects/{project_id}/boqs)", 409)
    return boqs[0]


async def _existing_mapped_item(
    session: AsyncSession, *, project: Project,
    measurement: MeasurementModel,
) -> BoqItem | None:
    """A BoqItem in any of the project's BOQs already billing this
    measurement (durable identity OR row id in measurement_ids — the same
    refs _boq_requiring_reapproval matches). Remapping is remove-then-map:
    finding one here refuses the second map."""
    items = (await session.execute(
        select(BoqItem, CatalogueItem.code)
        .join(BoqSection, BoqItem.section_id == BoqSection.id)
        .join(BoqModel, BoqSection.boq_id == BoqModel.id)
        .outerjoin(CatalogueItem, BoqItem.catalogue_item_id == CatalogueItem.id)
        .where(BoqModel.project_id == str(project.id))
    )).all()
    refs = {str(measurement.measurement_id), str(measurement.id)}
    for item, _code in items:
        if item.measurement_ids and refs.intersection(set(item.measurement_ids)):
            return cast(BoqItem, item)
    return None


async def _resolve_unmapped_blockers(
    session: AsyncSession, *, run: MeasurementRun,
    measurement: MeasurementModel, code: str,
) -> list[ExceptionModel]:
    """Mark the run's unresolved unmapped_measurement blockers for THIS
    measurement resolved. The build wrote one blocker row per measurement
    with message "measurement not mapped to any catalogue item: {label}
    ({rule_id} {unit})" — matched on the measurement's label (or durable
    identity when the label is null) so exactly the rows the new item now
    bills clear, never a sibling measurement's blockers."""
    label = measurement.label or str(measurement.measurement_id)
    rows = (await session.execute(
        select(ExceptionModel).where(
            ExceptionModel.run_id == run.id,
            ExceptionModel.code == "unmapped_measurement",
            ExceptionModel.resolved_at.is_(None),
        )
    )).scalars().all()
    resolved: list[ExceptionModel] = []
    for row in rows:
        if f": {label} (" not in row.message:
            continue  # a different measurement's blocker — stays open
        row.resolved_at = datetime.now(UTC)
        row.resolution = f"mapped to {code} by human"
        resolved.append(row)
    return resolved


async def map_measurement_to_catalogue(
    session: AsyncSession,
    *,
    measurement_ref: str,
    catalogue_item_id: str,
    reason: str,
    user_id: str,
) -> dict[str, Any]:
    """POST /measurements/{ref}/map — the human's mapping decision.

    One measurement -> one catalogue item -> one mapped BoqItem appended to
    the run's DRAFT BOQ, priced through the money kernel, with the run's
    unmapped_measurement blocker(s) for that measurement resolved and the
    decision audited (MAP_CATALOGUE, project-scoped).

    V1 semantics, deliberately narrow:
      * remapping is remove-then-map — a measurement already billed by any
        of the project's BOQ items refuses with already_mapped naming the
        existing item's code (delete the item, then map again),
      * the catalogue item must have a project/default rate and a unit that
        equals the measurement's unit — a mismatched mapping is a
        believable-but-wrong BOQ line, the exact thing the doctrine forbids,
      * only MEASURED/MEASURED_ZERO rows with evidence may be billed (the
        assembly path's own gates; blocked/not_measurable rows change state
        through review or a new run, never a mapping).
    """
    m, run, project = await resolve_measurement(
        session, measurement_ref=measurement_ref, user_id=user_id)

    if not _is_uuid(catalogue_item_id):
        raise ReviewServiceError(
            "not_found", "catalogue item not found", 404)
    catalogue_item = (await session.execute(
        select(CatalogueItem).where(CatalogueItem.id == catalogue_item_id)
    )).scalar_one_or_none()
    if catalogue_item is None:
        raise ReviewServiceError("not_found", "catalogue item not found", 404)

    state = MeasurementState(str(m.state))
    if state not in MAPPABLE_STATES:
        raise ReviewServiceError(
            "state_not_mappable",
            f"measurement is {state.value!r} — only measured rows may be "
            "mapped (blocked/not_measurable rows change through review or "
            "a new run)",
            409)

    # Idempotency guard BEFORE the BOQ-state work: a second map of the same
    # measurement double-bills, so it names the existing item and refuses.
    existing = await _existing_mapped_item(
        session, project=project, measurement=m)
    if existing is not None:
        raise ReviewServiceError(
            "already_mapped",
            f"measurement is already mapped to catalogue item "
            f"{existing.catalogue_item_id} — remove that BOQ item first to "
            "remap (V1: mapping is remove-then-map)",
            409)

    rate = await _select_rate(session, catalogue_item=catalogue_item)
    if rate is None:
        raise ReviewServiceError(
            "missing_rate",
            f"catalogue item {catalogue_item.code} has no project/default "
            "rate — set one first (PUT /catalog/items/{id}/rates/default)",
            409)

    if (m.unit or "") != catalogue_item.unit:
        raise ReviewServiceError(
            "unit_mismatch",
            f"catalogue item {catalogue_item.code} is priced per "
            f"{catalogue_item.unit!r} but the measurement is "
            f"{m.unit!r} — a mismatched mapping would bill the wrong unit",
            409)

    evidence_count = await _evidence_count(session, m)
    if evidence_count == 0:
        raise ReviewServiceError(
            "missing_evidence",
            "MEASURED rows require evidence before they may bill a BOQ line",
            409)

    boq = await _draft_boq_for_run(session, run=run)
    # The BOQ's first section is the mapped-works home; a mapping cannot
    # invent a section (a BOQ built from a run always has one).
    section = (await session.execute(
        select(BoqSection).where(BoqSection.boq_id == boq.id)
        .order_by(BoqSection.sort_order, BoqSection.id)
    )).scalars().first()
    if section is None:
        raise ReviewServiceError(
            "no_section", "the DRAFT BOQ has no section to add the item to",
            409)

    # Quantity = the measurement's effective value (corrected_value when
    # present, else the engine's — the R6 correction doctrine). The
    # measurement_ids list carries DURABLE identities (what assemble_item
    # writes), never row ids.
    quantity = _effective_value(m)
    # Money is integer minor units via the same kernel every priced line
    # uses (invariant 5); currency follows the project's rate currency.
    total = _line_total(quantity, rate.amount_minor, 0, rate.currency)

    max_order = (await session.execute(
        select(BoqItem.sort_order).join(
            BoqSection, BoqItem.section_id == BoqSection.id)
        .where(BoqSection.boq_id == boq.id)
        .order_by(BoqItem.sort_order.desc()).limit(1)
    )).scalars().first()
    sort_order = (max_order + 1) if max_order is not None else 0

    item = BoqItem(
        id=str(uuid.uuid4()), section_id=section.id, origin="mapped",
        catalogue_item_id=catalogue_item.id,
        description=catalogue_item.description, unit=catalogue_item.unit,
        quantity=quantity, rate_minor=rate.amount_minor,
        rate_scope=rate.scope, markup_bp=0, total_minor=total,
        measurement_ids=[str(m.measurement_id)], sort_order=sort_order,
    )
    session.add(item)
    await session.flush()

    resolved = await _resolve_unmapped_blockers(
        session, run=run, measurement=m, code=catalogue_item.code)
    await session.flush()

    audit = AuditEntry(
        id=str(uuid.uuid4()),
        actor=_as_uuid(user_id),
        action=AuditAction.MAP_CATALOGUE.value,
        subject_type="measurement",
        subject_id=_as_uuid(m.id),
        project_id=_as_uuid(project.id),
        before={"mapped": None},
        after={"catalogue_item_id": str(catalogue_item.id),
               "code": catalogue_item.code,
               "boq_item_id": str(item.id),
               "resolved_blockers": len(resolved)},
        reason=reason,
    )
    session.add(audit)
    await session.flush()
    return {
        "ok": True,
        "audit_id": str(audit.id),
        "item_id": str(item.id),
        "boq_id": str(boq.id),
        "catalogue_item_id": str(catalogue_item.id),
        "code": catalogue_item.code,
        "quantity": str(quantity),
        "unit": catalogue_item.unit,
        "rate_minor": rate.amount_minor,
        "rate_scope": rate.scope,
        "total_minor": total,
        "resolved_blockers": len(resolved),
    }


# ---------------------------------------------------------------------------
# T084 — audited suggestion apply (the advisory -> decision bridge)
# ---------------------------------------------------------------------------


async def apply_suggestion(
    session: AsyncSession,
    *,
    suggestion_id: str,
    reason: str,
    user_id: str,
) -> dict[str, Any]:
    """POST /ai/suggestions/{id}/apply — the human acting on an AI proposal.

    THE QUANTITY GUARD, structural: only the closed allowlist
    APPLYABLE_SUGGESTION_KINDS may apply; every other kind — including a
    rogue provider's novel kind written straight into the table — refuses
    with not_applyable BEFORE any row is read beyond the suggestion itself.
    No code path from here writes a value, a corrected_value, a state, a
    rate, or a BoqItem: the apply is the T073 classification override
    (element_type + type_source=human_set) and nothing else.

    element_classification performs the EXACT override_element_type
    semantics: the AI provenance on the element row is PRESERVED, and the
    suggestion's model/confidence/rationale become the element's ai_* voice
    only when the row has none (provenance, never fabrication — the
    suggestion IS the source of that voice).
    """
    if not _is_uuid(suggestion_id):
        raise ReviewServiceError("not_found", "suggestion not found", 404)
    suggestion = (await session.execute(
        select(AiSuggestion).where(AiSuggestion.id == suggestion_id)
    )).scalar_one_or_none()
    if suggestion is None:
        raise ReviewServiceError("not_found", "suggestion not found", 404)

    # Ownership: the suggestion's run -> project -> created_by == caller.
    # A stranger sees the same 404 as a missing suggestion.
    if suggestion.run_id is None:
        raise ReviewServiceError("not_found", "suggestion not found", 404)
    run = (await session.execute(
        select(MeasurementRun).where(
            MeasurementRun.id == suggestion.run_id)
    )).scalar_one_or_none()
    project = None
    if run is not None:
        project = (await session.execute(
            select(Project).where(
                Project.id == run.project_id,
                Project.created_by == user_id,
                Project.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
    if run is None or project is None:
        raise ReviewServiceError("not_found", "suggestion not found", 404)

    if suggestion.accepted is True:
        raise ReviewServiceError(
            "already_applied", "suggestion is already applied", 409)

    # THE allowlist gate. exception_explanation is informational — refusing
    # politely rather than pretending an apply changed something.
    if suggestion.suggestion_type in INFORMATIONAL_SUGGESTION_KINDS:
        raise ReviewServiceError(
            "informational_only",
            f"suggestion kind {suggestion.suggestion_type!r} is informational "
            "— nothing to apply (its explanation is on the exception)",
            409)
    if suggestion.suggestion_type not in APPLYABLE_SUGGESTION_KINDS:
        raise ReviewServiceError(
            "not_applyable",
            f"suggestion kind {suggestion.suggestion_type!r} cannot be "
            "applied — no suggestion kind may write a quantity",
            409)

    payload = suggestion.payload or {}
    element_type_raw = payload.get("element_type")
    if not isinstance(element_type_raw, str):
        raise ReviewServiceError(
            "invalid_element_type",
            "suggestion payload carries no element_type to apply",
            422)
    try:
        parsed = ElementType(element_type_raw)
    except ValueError as exc:
        legal = ", ".join(t.value for t in ElementType)
        raise ReviewServiceError(
            "invalid_element_type",
            f"suggestion's element_type {element_type_raw!r} is not in the "
            f"legal vocabulary: {legal}",
            422,
        ) from exc

    element = (await session.execute(
        select(Element).where(Element.id == suggestion.subject_id)
    )).scalar_one_or_none()
    if element is None:
        raise ReviewServiceError(
            "not_found", "the suggestion's element no longer exists", 404)
    if str(element.run_id) != str(run.id):
        raise ReviewServiceError(
            "not_found", "the suggestion's element no longer exists", 404)

    before = {
        "element_type": element.element_type,
        "type_source": element.type_source,
        "ai_confidence": (str(element.ai_confidence)
                          if element.ai_confidence is not None else None),
        "ai_model": element.ai_model,
    }
    # T073 semantics: the human word lands; the AI voice is preserved. When
    # the row has NO ai_* voice (geometry_deterministic rows never got one),
    # the suggestion's model/confidence/rationale become it — that IS the
    # provenance of this decision, recorded from the suggestion row.
    element.element_type = parsed.value
    element.type_source = ElementTypeSource.HUMAN_SET.value
    if element.ai_model is None:
        element.ai_model = suggestion.model
    if element.ai_confidence is None:
        # Numeric(4,3) column: the suggestion's confidence is the source.
        element.ai_confidence = Decimal(str(suggestion.confidence))  # type: ignore[assignment]
    if element.ai_explanation is None:
        rationale = payload.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            element.ai_explanation = rationale

    suggestion.accepted = True

    audit = AuditEntry(
        id=str(uuid.uuid4()),
        actor=_as_uuid(user_id),
        action=AuditAction.OVERRIDE_ELEMENT_TYPE.value,
        subject_type="element",
        subject_id=_as_uuid(element.id),
        project_id=_as_uuid(project.id),
        before=before,
        after={"element_type": parsed.value,
               "type_source": ElementTypeSource.HUMAN_SET.value,
               "applied_suggestion": str(suggestion.id)},
        reason=reason,
    )
    session.add(audit)
    await session.flush()
    return {
        "ok": True,
        "audit_id": str(audit.id),
        "suggestion_id": str(suggestion.id),
        "element": element_out(element),
    }
