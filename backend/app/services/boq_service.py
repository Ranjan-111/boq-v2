"""BOQ service (Round 4) — persisted BOQ, approval gates, export artifacts.

The server-side trust boundary (docs/domain-model.md §Round 3 semantics):
  * BOQ items are assembled only from persisted MEASURED/MEASURED_ZERO
    measurements with evidence (boq.assembly refuses anything else),
  * approval blocks on unresolved BLOCKING/REVIEW exceptions (server-side,
    never client-supplied),
  * export loads a TRUSTED persisted approval scope (never client input),
    re-validates rows, and only then writes an immutable artifact + manifest,
  * any post-approval mutation of items/rates flips the BOQ to STALE_APPROVED.

Layering: imports boq/exports via their public API only (Protocol boundary).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    DrawingFile,
    Element,
    EvidenceLinkModel,
    ExceptionModel,
    ExportArtifact,
    GeometryModel,
    MeasurementModel,
    MeasurementRun,
    RateModel,
)
from backend.app.storage.base import Storage
from boq.assembly import BoqAssemblyError, CatalogueRate, assemble_item
from core.domain.enums import (
    AuditAction,
    BoqStatus,
    ExceptionSeverity,
    MeasurementState,
)
from core.domain.states import transition_boq
from core.provenance.records import EvidenceLink
from core.units.money import apply_markup, multiply_rate
from exports.csv_export import ExportApproval, csv_bytes, rows_digest
from exports.provenance_sidecar import provenance_sidecar_bytes

BLOCKING_SEVERITIES = (ExceptionSeverity.BLOCKING.value, ExceptionSeverity.REVIEW.value)


class BoqServiceError(RuntimeError):
    """Refusal with a machine-readable code for the API layer."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _PersistedMeasurement:
    """AssemblyMeasurement protocol adapter over a MeasurementModel row."""

    def __init__(self, row: MeasurementModel, evidence: tuple[EvidenceLink, ...]) -> None:
        self._row = row
        self._evidence = evidence

    @property
    def measurement_id(self) -> str:
        return str(self._row.measurement_id)

    @property
    def value(self) -> Decimal:
        """The billable value: a human correction replaces the engine value.

        docs/domain-model.md: "BOQ recomputes from corrected values with
        provenance human_correction — original always visible." The
        original stays on the row (value column, never mutated); the
        correction (corrected_value) is what a BOQ bills. An assembly
        building from m.value would silently bill the pre-correction
        number — the exact believable-but-wrong BOQ the doctrine forbids.
        """
        if self._row.corrected_value is not None:
            return Decimal(str(self._row.corrected_value))
        return Decimal(str(self._row.value)) if self._row.value is not None else Decimal(0)

    @property
    def unit(self) -> Any:
        from core.domain.enums import MeasurementUnit
        return MeasurementUnit(str(self._row.unit))

    @property
    def state(self) -> MeasurementState:
        return MeasurementState(str(self._row.state))

    @property
    def evidence(self) -> tuple[EvidenceLink, ...]:
        return self._evidence

    @property
    def label(self) -> str | None:
        return self._row.label


def _money_supported(currency: str) -> bool:
    return currency in {"INR", "USD", "EUR", "GBP"}


async def build_boq_from_run(
    session: AsyncSession, *, project_id: str, from_run_id: str, actor: str,
) -> dict[str, Any]:
    """Draft BOQ: one section, one item per (catalogue item) with a DEFAULT/PROJECT rate.

    V1 mapping rule (explicit, honest): every MEASURED length/area measurement
    of the run is offered; only those whose catalogue item exists AND has a
    rate AND unit-reconciles become items; the rest are reported as unmapped
    (blockers) — never silently dropped.
    """
    run = (await session.execute(
        select(MeasurementRun).where(
            MeasurementRun.id == from_run_id,
            MeasurementRun.project_id == project_id,
        )
    )).scalar_one_or_none()
    if run is None:
        raise BoqServiceError("run_not_found", "run not found in this project", 404)
    if run.status not in ("completed", "completed_with_exceptions"):
        raise BoqServiceError(
            "run_not_complete", f"run status is {run.status!r}; wait for completion")

    rows = (await session.execute(
        select(MeasurementModel).where(MeasurementModel.run_id == run.id)
        .order_by(MeasurementModel.created_at, MeasurementModel.id)
    )).scalars().all()
    measured = [r for r in rows
                if MeasurementState(str(r.state)) in
                (MeasurementState.MEASURED, MeasurementState.MEASURED_ZERO)]
    if not measured:
        raise BoqServiceError(
            "no_measured", "run has no measured quantities to build a BOQ from")

    boq = BoqModel(id=str(uuid.uuid4()), project_id=project_id, version=1,
                   status=BoqStatus.DRAFT.value, from_run_id=run.id)
    session.add(boq)
    await session.flush()
    section = BoqSection(id=str(uuid.uuid4()), boq_id=boq.id, code="A",
                         title="Measured works", sort_order=0)
    session.add(section)
    await session.flush()

    unmapped: list[str] = []
    item_count = 0
    # Round 5 mapping rule (deterministic, honest): group measurements by
    # (rule_id, unit) — NOT bare unit. Room m² and wall m² are different
    # quantities and must map to different catalogue items; a bare-unit
    # grouping would merge them into one line (a believable but wrong BOQ).
    # Within a group, MORE THAN ONE candidate catalogue item is a blocker
    # (ambiguous_catalog_mapping) — never first-by-order.
    candidates_by_group: dict[tuple[str, str], list[MeasurementModel]] = {}
    for m in measured:
        candidates_by_group.setdefault((str(m.rule_id), str(m.unit)), []).append(m)
    # Pass 1 (resolve): every group picks its would-be item, then collisions
    # are settled SYMMETRICALLY — one catalogue item claimed by 2+ rule groups
    # (e.g. wall footprint m2 AND wall net m2 finding the same m2 item) is
    # ambiguous for ALL claimants: auto-mapping cannot decide who bills and
    # who does not, so every claimant becomes a blocker for the human mapping
    # UI (post-V1, T084). An inline "first group wins" guard would be exactly
    # the first-by-order pick the doctrine forbids — footprint bills only
    # because 'f' sorts before 'n'.
    claim_map: dict[tuple[str, str], tuple[CatalogueItem, RateModel]] = {}
    blocked_groups: dict[tuple[str, str], str] = {}  # group -> reason
    for (rule_id, unit) in sorted(candidates_by_group):
        items = (await session.execute(
            select(CatalogueItem, RateModel)
            .join(RateModel, RateModel.catalogue_item_id == CatalogueItem.id)
            .where(CatalogueItem.unit == unit,
                   RateModel.scope.in_(("project", "default")))
            .order_by(RateModel.scope.desc())  # project beats default
        )).all()
        if not items:
            blocked_groups[(rule_id, unit)] = "no candidate"
            continue
        # Distinct catalogue items with rates for this unit group: a tie at
        # the top scope is ambiguous (the deterministic order cannot choose).
        distinct_items: list[tuple[CatalogueItem, RateModel]] = []
        seen_items: set[str] = set()
        for catalogue_item, rate_row in items:
            if str(catalogue_item.id) not in seen_items:
                seen_items.add(str(catalogue_item.id))
                distinct_items.append((catalogue_item, rate_row))
        top_scope = distinct_items[0][1].scope
        top_scope_items = [pair for pair in distinct_items
                           if pair[1].scope == top_scope]
        if len(top_scope_items) > 1:
            blocked_groups[(rule_id, unit)] = (
                "ambiguous catalog mapping: " + " vs ".join(
                    pair[0].code for pair in top_scope_items[:4]))
            continue
        candidate = top_scope_items[0]
        # Cross-group collision: track every claimant of this item.
        claim_map.setdefault((rule_id, unit), candidate)
    # Symmetric collision settle: an item with 2+ claimants is ambiguous for
    # ALL of them — no claimant bills, all surface as blockers.
    item_claimants: dict[str, list[tuple[str, str]]] = {}
    for group_key, (catalogue_item, _rate) in claim_map.items():
        item_claimants.setdefault(str(catalogue_item.id), []).append(group_key)
    for claimant_groups in item_claimants.values():
        if len(claimant_groups) > 1:
            item = claim_map[claimant_groups[0]][0]
            for group_key in claimant_groups:
                blocked_groups[group_key] = (
                    f"ambiguous catalog mapping: item {item.code} claimed by "
                    + " and ".join(f"{g[0]} ({g[1]})" for g in sorted(claimant_groups))
                )
            claim_map.pop(group_key, None)
    # Pass 2 (assemble): assemble the surviving, unambiguous groups.
    for (rule_id, unit), group in sorted(candidates_by_group.items()):
        if (rule_id, unit) in blocked_groups:
            unmapped.extend(f"{m.label or m.measurement_id} ({rule_id} {unit})"
                            for m in group)
            continue
        if (rule_id, unit) not in claim_map:
            continue
        catalogue_item, rate_row = claim_map[(rule_id, unit)]
        # Evidence for the protocol adapter, grouped by measurement row id.
        from backend.app.db.models import EvidenceLinkModel
        ev_rows = (await session.execute(
            select(EvidenceLinkModel).where(
                EvidenceLinkModel.subject_type == "measurement",
                EvidenceLinkModel.subject_id.in_(
                    [uuid.UUID(str(m.id)) for m in group]),
            )
        )).scalars().all()
        ev_lists: dict[str, list[EvidenceLink]] = {}
        for ev in ev_rows:
            ev_lists.setdefault(str(ev.subject_id), []).append(
                EvidenceLink(kind=ev.kind, ref=ev.ref, note=ev.note)
            )
        adapters = [_PersistedMeasurement(m, tuple(ev_lists.get(str(m.id), [])))
                    for m in group]
        rate = CatalogueRate(
            catalogue_item_id=str(catalogue_item.id), code=catalogue_item.code,
            description=catalogue_item.description, unit=catalogue_item.unit,
            rate_minor=rate_row.amount_minor, markup_bp=0,
        )
        try:
            row = assemble_item(adapters, rate, currency=rate_row.currency)
        except BoqAssemblyError as exc:
            unmapped.append(f"{catalogue_item.code}: {exc}")
            continue
        session.add(BoqItem(
            id=str(uuid.uuid4()), section_id=section.id, origin="mapped",
            catalogue_item_id=catalogue_item.id,
            description=row.description, unit=row.unit, quantity=row.quantity,
            rate_minor=row.rate_minor, rate_scope=rate_row.scope,
            markup_bp=row.markup_bp, total_minor=row.total_minor,
            measurement_ids=list(row.measurement_ids), sort_order=item_count,
        ))
        item_count += 1
        await session.flush()

    # Round 5 trust closure: unmapped measurements are not a response-only
    # note — they persist as BLOCKING exception rows on the run, so the
    # server-side approve/export gate refuses until they are resolved (the
    # R4 gap: unmapped surfaced in the build response but never blocked
    # approval). One row per unmapped measurement, message carries the
    # label + rule + unit for the review queue.
    if unmapped:
        from backend.app.db.models import ExceptionModel

        for label in unmapped:
            # One row per unmapped measurement, message carries the
            # label + rule + unit for the review queue. Flush per row
            # (codebase idiom): a batched insertmanyvalues flush would
            # sentinel-match str id params against asyncpg UUID returns.
            session.add(ExceptionModel(
                id=str(uuid.uuid4()), run_id=run.id, sheet_id=None,
                measurement_id=None, element_id=None,
                code="unmapped_measurement",
                severity=ExceptionSeverity.BLOCKING.value,
                message=f"measurement not mapped to any catalogue item: {label}",
                evidence=None,
            ))
            await session.flush()

    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.CREATE.value,
        subject_type="boq", subject_id=boq.id,
        project_id=boq.project_id,
        after={"from_run_id": str(run.id), "items": item_count,
               "unmapped": len(unmapped)},
    ))
    await session.flush()
    return {"boq_id": boq.id, "section_count": 1, "item_count": item_count,
            "unmapped": unmapped}


# ---------------------------------------------------------------------------
# Read path (routers)
# ---------------------------------------------------------------------------


async def load_boq_tree(session: AsyncSession, *, boq_id: str,
                        project_id: str) -> dict[str, Any] | None:
    """Full BOQ tree for GET /boqs/{id} (ownership by project)."""
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == boq_id,
                               BoqModel.project_id == project_id)
    )).scalar_one_or_none()
    if boq is None:
        return None
    sections = (await session.execute(
        select(BoqSection).where(BoqSection.boq_id == boq.id)
        .order_by(BoqSection.sort_order, BoqSection.id)
    )).scalars().all()
    items = (await session.execute(
        select(BoqItem).where(BoqItem.section_id.in_(
            [s.id for s in sections] or ["00000000-0000-0000-0000-000000000000"]))
        .order_by(BoqItem.sort_order, BoqItem.id)
    )).scalars().all() if sections else []
    by_section: dict[str, list[BoqItem]] = {}
    for it in items:
        by_section.setdefault(it.section_id, []).append(it)
    grand = sum((it.total_minor or 0) for it in items)
    return {
        "id": str(boq.id), "project_id": str(boq.project_id),
        "version": boq.version, "status": boq.status,
        "from_run_id": str(boq.from_run_id) if boq.from_run_id else None,
        "sections": [
            {"id": str(s.id), "code": s.code, "title": s.title,
             "items": [_item_out(i) for i in by_section.get(s.id, [])]}
            for s in sections
        ],
        "totals": {"grand_total_minor": grand},
    }


def _item_out(i: BoqItem) -> dict[str, Any]:
    return {
        "id": str(i.id), "origin": i.origin,
        "catalogue_item_id": str(i.catalogue_item_id) if i.catalogue_item_id else None,
        "code": "", "description": i.description, "unit": i.unit,
        "quantity": str(i.quantity) if i.quantity is not None else None,
        "rate_minor": i.rate_minor, "rate_scope": i.rate_scope,
        "markup_bp": i.markup_bp, "total_minor": i.total_minor,
        "measurement_ids": i.measurement_ids or [],
    }


async def _owned_boq(session: AsyncSession, boq_id: str,
                     project_id: str) -> BoqModel:
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == boq_id,
                               BoqModel.project_id == project_id)
    )).scalar_one_or_none()
    if boq is None:
        raise BoqServiceError("not_found", "BOQ not found", 404)
    return boq


async def _unresolved_blockers(session: AsyncSession, run_id: str) -> list[ExceptionModel]:
    """BLOCKING/REVIEW exceptions of the source run that are still unresolved."""
    return list((await session.execute(
        select(ExceptionModel).where(
            ExceptionModel.run_id == run_id,
            ExceptionModel.resolved_at.is_(None),
            ExceptionModel.severity.in_(BLOCKING_SEVERITIES),
        )
    )).scalars().all())


# ---------------------------------------------------------------------------
# Approval workflow (server-side gates; every transition audited)
# ---------------------------------------------------------------------------


async def submit_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                     actor: str) -> dict[str, Any]:
    boq = await _owned_boq(session, boq_id, project_id)
    was = boq.status
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.IN_REVIEW).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.SUBMIT.value if hasattr(AuditAction, "SUBMIT")
                 else AuditAction.UPDATE.value, boq, before_status=was)
    return {"status": boq.status}


async def review_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                    actor: str, note: str | None = None) -> dict[str, Any]:
    """IN_REVIEW -> REVIEWED: a human has completed their review pass.

    This is the hop the approval gate requires (approve only accepts
    REVIEWED); it is the reviewer's explicit statement, so it is audited
    like every other transition.
    """
    boq = await _owned_boq(session, boq_id, project_id)
    was = boq.status
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.REVIEWED).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.UPDATE.value, boq, note=note,
                 before_status=was)
    return {"status": boq.status}


async def approve_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                      actor: str, note: str | None = None) -> dict[str, Any]:
    """APPROVE only when: legal transition, items exist, zero unresolved
    blockers on the source run (server-side check, never client-supplied)."""
    boq = await _owned_boq(session, boq_id, project_id)
    # DRAFT/IN_REVIEW/REVIEWED -> APPROVED must pass through REVIEWED per
    # the machine; be strict: only REVIEWED can approve.
    if boq.status != BoqStatus.REVIEWED.value:
        raise BoqServiceError(
            "illegal_transition",
            f"BOQ is {boq.status!r}; submit then review before approving")
    item_count = len((await session.execute(
        select(BoqItem.id).join(BoqSection, BoqItem.section_id == BoqSection.id)
        .where(BoqSection.boq_id == boq.id)
    )).scalars().all())
    if item_count == 0:
        raise BoqServiceError("no_items", "cannot approve an empty BOQ")
    blockers = (await _unresolved_blockers(session, str(boq.from_run_id))
                if boq.from_run_id else [])
    if blockers:
        raise BoqServiceError(
            "blockers_present",
            "; ".join(f"{b.code}: {b.message}" for b in blockers))
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.APPROVED).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    # Uuid column (typed str in the ORM): asyncpg SELECTs hand back pgproto
    # UUIDs, hand-built rows hand back str — accept both.
    was = boq.status
    boq.approved_by = str(actor)
    boq.approved_at = datetime.now(UTC)
    await _audit(session, actor, AuditAction.APPROVE.value, boq, note=note,
                 before_status=was)
    return {"status": boq.status}


async def reject_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                     actor: str, note: str | None = None) -> dict[str, Any]:
    boq = await _owned_boq(session, boq_id, project_id)
    was = boq.status
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.DRAFT).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.REJECT.value, boq, note=note,
                 before_status=was)
    return {"status": boq.status}


async def mark_stale_if_approved(session: AsyncSession, boq: BoqModel) -> None:
    """Any post-approval mutation of a BOQ flips APPROVED -> STALE_APPROVED."""
    if boq.status == BoqStatus.APPROVED.value:
        boq.status = transition_boq(BoqStatus.APPROVED, BoqStatus.STALE_APPROVED).value


# ---------------------------------------------------------------------------
# BOQ editing (T086) — sections, items, recompute + diff, validation report.
# Mutations are DRAFT-only: once review starts the BOQ is a reviewed
# document; the only path back is REJECT -> DRAFT (audited) or, after
# approval, the STALE_APPROVED -> DRAFT re-entry. Never a silent edit of a
# document someone approved.
# ---------------------------------------------------------------------------


def _require_draft(boq: BoqModel) -> None:
    if boq.status != BoqStatus.DRAFT.value:
        raise BoqServiceError(
            "not_draft",
            f"BOQ is {boq.status!r} — only a DRAFT may be edited "
            "(reject it first to return to draft)")


async def _owned_section(session: AsyncSession, section_id: str,
                         project_id: str) -> tuple[BoqModel, BoqSection]:
    section = (await session.execute(
        select(BoqSection).where(BoqSection.id == section_id)
    )).scalar_one_or_none()
    if section is None:
        raise BoqServiceError("not_found", "section not found", 404)
    boq = await _owned_boq(session, str(section.boq_id), project_id)
    return boq, section


async def add_section(session: AsyncSession, *, project_id: str, boq_id: str,
                      code: str, title: str, sort_order: int, actor: str,
                      ) -> dict[str, Any]:
    """POST /boqs/{id}/sections — a human grouping decision, DRAFT-only."""
    boq = await _owned_boq(session, boq_id, project_id)
    _require_draft(boq)
    if not code.strip() or not title.strip():
        raise BoqServiceError("invalid_section", "code and title are required")
    section = BoqSection(id=str(uuid.uuid4()), boq_id=boq.id,
                         code=code.strip(), title=title.strip(),
                         sort_order=sort_order)
    session.add(section)
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.CREATE.value,
        subject_type="boq_section", subject_id=section.id,
        project_id=boq.project_id,
        after={"boq_id": str(boq.id), "code": section.code,
               "title": section.title, "sort_order": sort_order},
    ))
    await session.flush()
    return {"section_id": str(section.id), "code": section.code,
            "title": section.title, "sort_order": sort_order}


async def update_section(session: AsyncSession, *, project_id: str,
                         section_id: str, code: str | None, title: str | None,
                         sort_order: int | None, actor: str) -> dict[str, Any]:
    """PATCH /sections/{id} — DRAFT-only; before/after audited."""
    boq, section = await _owned_section(session, section_id, project_id)
    _require_draft(boq)
    before = {"code": section.code, "title": section.title,
              "sort_order": section.sort_order}
    if code is not None:
        if not code.strip():
            raise BoqServiceError("invalid_section", "code cannot be empty")
        section.code = code.strip()
    if title is not None:
        if not title.strip():
            raise BoqServiceError("invalid_section", "title cannot be empty")
        section.title = title.strip()
    if sort_order is not None:
        section.sort_order = sort_order
    await session.flush()
    after = {"code": section.code, "title": section.title,
             "sort_order": section.sort_order}
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.UPDATE.value,
        subject_type="boq_section", subject_id=section.id,
        project_id=boq.project_id, before=before, after=after,
    ))
    await session.flush()
    return after


async def delete_section(session: AsyncSession, *, project_id: str,
                         section_id: str, actor: str) -> dict[str, Any]:
    """DELETE /sections/{id} — DRAFT-only; items go with their section."""
    boq, section = await _owned_section(session, section_id, project_id)
    _require_draft(boq)
    items = (await session.execute(
        select(BoqItem).where(BoqItem.section_id == section.id)
    )).scalars().all()
    for item in items:
        await session.delete(item)
        await session.flush()
    await session.delete(section)
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.UPDATE.value,
        subject_type="boq_section", subject_id=section.id,
        project_id=boq.project_id,
        before={"code": section.code, "title": section.title,
                "items": len(items)},
        after={"deleted": True},
    ))
    await session.flush()
    return {"deleted": True, "items_removed": len(items)}


async def _owned_item(session: AsyncSession, item_id: str,
                      project_id: str) -> tuple[BoqModel, BoqItem]:
    item = (await session.execute(
        select(BoqItem).where(BoqItem.id == item_id)
    )).scalar_one_or_none()
    if item is None:
        raise BoqServiceError("not_found", "BOQ item not found", 404)
    section = (await session.execute(
        select(BoqSection).where(BoqSection.id == item.section_id)
    )).scalar_one()
    boq = await _owned_boq(session, str(section.boq_id), project_id)
    return boq, item


async def add_manual_item(session: AsyncSession, *, project_id: str,
                          boq_id: str, section_id: str | None,
                          description: str, unit: str | None,
                          quantity: Decimal, rate_minor: int | None,
                          markup_bp: int, sort_order: int, actor: str,
                          ) -> dict[str, Any]:
    """POST /boqs/{id}/items — a MANUAL line (T085's manual/PC-sum contract).

    The quantity is the human's own entry (provenance: manual, audited);
    it never masquerades as a mapped measurement. PC-sum lines arrive the
    same way with an explicit lump rate later.
    """
    boq = await _owned_boq(session, boq_id, project_id)
    _require_draft(boq)
    if section_id is None:
        section = (await session.execute(
            select(BoqSection).where(BoqSection.boq_id == boq.id)
            .order_by(BoqSection.sort_order, BoqSection.id)
        )).scalars().first()
        if section is None:
            raise BoqServiceError(
                "no_section", "BOQ has no section to add the item to")
    else:
        _b, section = await _owned_section(session, section_id, project_id)
        if str(section.boq_id) != str(boq.id):
            raise BoqServiceError("not_found", "section not in this BOQ", 404)
    if not description.strip():
        raise BoqServiceError("invalid_item", "description is required")
    if quantity < 0:
        raise BoqServiceError("invalid_item", "quantity must be >= 0")
    if markup_bp < 0:
        raise BoqServiceError("invalid_item", "markup must be >= 0")
    if rate_minor is not None and rate_minor < 0:
        raise BoqServiceError("invalid_item", "rate must be >= 0")
    # Manual lines are priced like any other: total recomputable from
    # (quantity, rate, markup) — invariant 5 applies to human rows too.
    currency = await _project_currency(session, project_id)
    total = _line_total(quantity, rate_minor, markup_bp, currency)
    item = BoqItem(
        id=str(uuid.uuid4()), section_id=section.id, origin="manual",
        catalogue_item_id=None, description=description.strip(),
        unit=unit, quantity=quantity, rate_minor=rate_minor,
        rate_scope=None, markup_bp=markup_bp, total_minor=total,
        measurement_ids=[], sort_order=sort_order,
    )
    session.add(item)
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.CREATE.value,
        subject_type="boq_item", subject_id=item.id,
        project_id=boq.project_id,
        after={"origin": "manual", "description": item.description,
               "quantity": str(quantity), "rate_minor": rate_minor,
               "markup_bp": markup_bp},
    ))
    await session.flush()
    return {"item_id": str(item.id), "total_minor": total}


async def update_item(session: AsyncSession, *, project_id: str, item_id: str,
                      description: str | None, rate_minor: int | None,
                      markup_bp: int | None,
                      quantity: Decimal | None, actor: str) -> dict[str, Any]:
    """PATCH /boq-items/{id} — DRAFT-only; rate override + markup + manual qty.

    A quantity PATCH on a MAPPED item is refused: mapped quantities come
    from measurements (correct them through the audited review path, or
    recompute after a correction — never a direct column edit). Manual
    items accept quantity edits (their provenance IS the human).
    """
    boq, item = await _owned_item(session, item_id, project_id)
    _require_draft(boq)
    before = {"description": item.description, "rate_minor": item.rate_minor,
              "markup_bp": item.markup_bp,
              "quantity": str(item.quantity) if item.quantity is not None else None,
              "total_minor": item.total_minor}
    if quantity is not None and item.origin == "mapped":
        raise BoqServiceError(
            "mapped_quantity_immutable",
            "mapped quantities come from measurements — correct the "
            "measurement (audited) or recompute; a direct edit would "
            "break provenance")
    if description is not None:
        if not description.strip():
            raise BoqServiceError("invalid_item", "description cannot be empty")
        item.description = description.strip()
    if rate_minor is not None:
        if rate_minor < 0:
            raise BoqServiceError("invalid_item", "rate must be >= 0")
        item.rate_minor = rate_minor
    if markup_bp is not None:
        if markup_bp < 0:
            raise BoqServiceError("invalid_item", "markup must be >= 0")
        item.markup_bp = markup_bp
    if quantity is not None:
        if quantity < 0:
            raise BoqServiceError("invalid_item", "quantity must be >= 0")
        item.quantity = quantity
    # Recompute the total from (quantity, rate, markup) — never a cached
    # number (invariant 5, enforced here so every edit leaves the row
    # recomputable).
    currency = await _project_currency(session, project_id)
    total = _line_total(item.quantity, item.rate_minor, item.markup_bp,
                        currency)
    item.total_minor = total
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.UPDATE.value,
        subject_type="boq_item", subject_id=item.id,
        project_id=boq.project_id, before=before,
        after={**before, "description": item.description,
               "rate_minor": item.rate_minor, "markup_bp": item.markup_bp,
               "quantity": (str(item.quantity)
                            if item.quantity is not None else None),
               "total_minor": total},
    ))
    await session.flush()
    return {"item_id": str(item.id), "total_minor": total}


async def delete_item(session: AsyncSession, *, project_id: str, item_id: str,
                      actor: str) -> dict[str, Any]:
    """DELETE /boq-items/{id} — DRAFT-only; before-snapshot audited."""
    boq, item = await _owned_item(session, item_id, project_id)
    _require_draft(boq)
    before = {"description": item.description, "origin": item.origin,
              "quantity": (str(item.quantity)
                           if item.quantity is not None else None)}
    await session.delete(item)
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=actor, action=AuditAction.UPDATE.value,
        subject_type="boq_item", subject_id=item.id,
        project_id=boq.project_id, before=before, after={"deleted": True},
    ))
    await session.flush()
    return {"deleted": True}


async def recompute_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                        actor: str) -> dict[str, Any]:
    """POST /boqs/{id}/recompute — pull upstream changes into a DRAFT BOQ.

    Upstream = measurement corrections (corrected_value) since the BOQ was
    built. Every MAPPED item re-reads its measurements: a corrected value
    replaces the engine value (the whole point of the audited correction —
    the BOQ recomputes from corrected values with provenance
    human_correction, docs/domain-model.md). Totals recompute from
    (quantity, rate, markup). Returns a per-item DIFF (before/after), so
    the caller sees exactly what an upstream change did — never a silent
    re-price. DRAFT-only: a reviewed/approved document does not move under
    its reader (that is the STALE path instead).

    T125 load discipline (§G target: 5k items < 2s): ALL of the run's
    measurements are batch-loaded in ONE query and the by-identity map is
    built once (the per-item SELECT was an N+1 — 5k items meant 5k+ round
    trips before any UPDATE was flushed); the project currency is fetched
    once before the loop (it cannot change mid-recompute); the per-item
    flush is collapsed to one flush — UPDATEs of independent rows batch
    into a single executemany either way, and the transaction makes
    partial-flush observability a non-contract.
    """
    boq = await _owned_boq(session, boq_id, project_id)
    _require_draft(boq)
    rows = (await session.execute(
        select(BoqItem).join(BoqSection, BoqItem.section_id == BoqSection.id)
        .where(BoqSection.boq_id == boq.id)
        .order_by(BoqSection.sort_order, BoqItem.sort_order, BoqItem.id)
    )).scalars().all()
    # Measurements are re-read through the DURABLE identities, scoped to
    # this BOQ's source run (measurement_id is unique per run, not
    # globally — two runs of the same drawing share identities). One load,
    # one map: the identities referenced by the items are a subset of the
    # run's rows, so a vanished identity still misses the map honestly.
    by_identity: dict[str, MeasurementModel] = {}
    if any(item.origin == "mapped" and item.measurement_ids for item in rows):
        ms = (await session.execute(
            select(MeasurementModel).where(
                MeasurementModel.run_id == boq.from_run_id)
        )).scalars().all()
        by_identity = {str(m.measurement_id): m for m in ms}
    # One currency lookup for the whole recompute (hoisted from the loop —
    # the project row does not change between items of one call).
    currency = await _project_currency(session, project_id)
    diff: list[dict[str, Any]] = []
    for item in rows:
        if item.origin != "mapped" or not item.measurement_ids:
            continue
        quantity = Decimal(0)
        for mid in item.measurement_ids:
            m = by_identity.get(str(mid))
            if m is None:
                continue  # identity vanished — recompute stays honest, skips
            value = m.corrected_value if m.corrected_value is not None else m.value
            quantity += Decimal(str(value)) if value is not None else Decimal(0)
        before_qty = item.quantity
        before_total = item.total_minor
        if before_qty is None or quantity != Decimal(str(before_qty)):
            item.quantity = quantity
            item.total_minor = _line_total(
                item.quantity, item.rate_minor, item.markup_bp, currency)
            diff.append({
                "item_id": str(item.id),
                "description": item.description,
                "before": {"quantity": (str(before_qty)
                                        if before_qty is not None else None),
                           "total_minor": before_total},
                "after": {"quantity": str(item.quantity),
                          "total_minor": item.total_minor},
            })
    if diff:
        # One flush persists the item UPDATEs (batched) before the audit row.
        await session.flush()
        session.add(AuditEntry(
            id=str(uuid.uuid4()), actor=actor,
            action=AuditAction.UPDATE.value,
            subject_type="boq", subject_id=boq.id,
            project_id=boq.project_id,
            before={"recompute": "pre-upstream-change"},
            after={"changed_items": len(diff),
                   "grand_total_minor": sum(i.total_minor or 0 for i in rows)},
            reason="recompute after measurement corrections",
        ))
        await session.flush()
    return {"changed_items": len(diff), "diff": diff}


async def validation_report(session: AsyncSession, *, project_id: str,
                            boq_id: str) -> dict[str, Any]:
    """GET /boqs/{id}/validation — the completeness/blocking report (T093).

    Server-side, export-gate-shaped: the same checks the approve/export
    gates run, reported as a list a human can work through. Never a client
    trust-me.
    """
    boq = await _owned_boq(session, boq_id, project_id)
    sections = (await session.execute(
        select(BoqSection).where(BoqSection.boq_id == boq.id)
        .order_by(BoqSection.sort_order, BoqSection.id)
    )).scalars().all()
    items = (await session.execute(
        select(BoqItem).where(BoqItem.section_id.in_(
            [s.id for s in sections]
            or ["00000000-0000-0000-0000-000000000000"]))
    )).scalars().all() if sections else []
    problems: list[dict[str, str]] = []
    if not items:
        problems.append({"code": "no_items",
                         "message": "BOQ has no items"})
    for item in items:
        if item.rate_minor is None:
            problems.append({
                "code": "unpriced_item",
                "message": f"{item.description[:60]}: no rate"})
        elif item.total_minor is None:
            problems.append({
                "code": "unpriced_item",
                "message": f"{item.description[:60]}: no total (recompute)"})
        if item.origin == "mapped" and not item.measurement_ids:
            problems.append({
                "code": "broken_mapping",
                "message": f"{item.description[:60]}: no measurement identity"})
    blockers: list[ExceptionModel] = []
    if boq.from_run_id is not None:
        blockers = await _unresolved_blockers(session, str(boq.from_run_id))
        for b in blockers:
            problems.append({"code": b.code,
                             "message": f"run blocker: {b.message[:200]}"})
    return {
        "boq_id": str(boq.id), "status": boq.status,
        "item_count": len(items),
        "unresolved_blockers": len(blockers),
        "problems": problems,
        "ready_for_approval": (not problems and boq.status in
                              (BoqStatus.DRAFT.value, BoqStatus.IN_REVIEW.value,
                               BoqStatus.REVIEWED.value)),
    }


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Coerce an actor/subject id to a native uuid.

    ORM rows are typed Mapped[str] but asyncpg returns pgproto UUID objects
    for Uuid columns — so a caller-selected user/boq hands us a UUID here
    while hand-built test rows hand us str. Accept both (str round-trips
    through uuid.UUID; a UUID passes through).
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _audit(session: AsyncSession, actor: str, action: str, boq: BoqModel,
                 *, note: str | None = None,
                 before_status: str | None = None) -> None:
    """Audited BOQ transition. before_status is the status BEFORE the
    transition — call sites capture it first so the row records what
    actually changed, never the post-state twice. project_id scopes the
    row to the project's audit trail (T075)."""
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=_as_uuid(actor), action=action,
        subject_type="boq", subject_id=_as_uuid(boq.id),
        project_id=boq.project_id,
        before={"status": before_status if before_status is not None
                else boq.status},
        after={"status": boq.status},
        reason=note,
    ))
    await session.flush()


# ---------------------------------------------------------------------------
# Export — trusted approval scope only, immutable artifact + manifest
# ---------------------------------------------------------------------------


async def _boq_rows_for_export(
    session: AsyncSession, boq: BoqModel,
) -> list[dict[str, Any]]:
    sections = (await session.execute(
        select(BoqSection).where(BoqSection.boq_id == boq.id)
        .order_by(BoqSection.sort_order, BoqSection.id)
    )).scalars().all()
    rows: list[dict[str, Any]] = []
    for s in sections:
        items = (await session.execute(
            select(BoqItem).where(BoqItem.section_id == s.id)
            .order_by(BoqItem.sort_order, BoqItem.id)
        )).scalars().all()
        for i in items:
            rows.append({
                "code": i.description[:40] or s.code,
                "description": i.description,
                "unit": i.unit or "",
                "quantity": Decimal(str(i.quantity)) if i.quantity is not None else Decimal(0),
                "rate_minor": i.rate_minor or 0,
                "markup_bp": i.markup_bp or 0,
                "total_minor": i.total_minor or 0,
                "currency": await _project_currency(session, str(boq.project_id)),
                "measurement_ids": tuple(i.measurement_ids or ()),
            })
    return rows


async def _project_currency(session: AsyncSession, project_id: str) -> str:
    from backend.app.db.models import Project
    row = (await session.execute(
        select(Project.currency).where(Project.id == project_id)
    )).scalar_one_or_none()
    return row or "INR"


async def _provenance_records_for_export(
    session: AsyncSession,
    boq: BoqModel,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load the durable chain required by the export sidecar.

    Measurement identities are scoped to the BOQ's source run.  A missing
    row or evidence link is a trust failure, even if the rendered row itself
    otherwise passes the format writer's checks.
    """
    if not boq.from_run_id:
        raise BoqServiceError("provenance_missing", "BOQ has no source run")
    identities = [
        identity
        for row in rows
        for identity in row["measurement_ids"]
    ]
    if not identities:
        raise BoqServiceError("provenance_missing", "export rows have no measurements")
    measurements = (await session.execute(
        select(MeasurementModel).where(
            MeasurementModel.run_id == boq.from_run_id,
            MeasurementModel.measurement_id.in_(identities),
        ).order_by(MeasurementModel.created_at, MeasurementModel.id)
    )).scalars().all()
    by_identity = {str(m.measurement_id): m for m in measurements}
    if set(by_identity) != set(identities):
        missing = sorted(set(identities) - set(by_identity))
        raise BoqServiceError(
            "provenance_missing",
            "measurement provenance missing for: " + ", ".join(missing),
        )
    evidence_rows = (await session.execute(
        select(EvidenceLinkModel).where(
            EvidenceLinkModel.subject_type == "measurement",
            EvidenceLinkModel.subject_id.in_([uuid.UUID(str(m.id)) for m in measurements]),
        ).order_by(EvidenceLinkModel.subject_id, EvidenceLinkModel.id)
    )).scalars().all()
    evidence_by_measurement: dict[str, list[dict[str, Any]]] = {}
    for evidence in evidence_rows:
        evidence_by_measurement.setdefault(str(evidence.subject_id), []).append({
            "kind": evidence.kind, "ref": evidence.ref, "note": evidence.note,
        })
    if any(not evidence_by_measurement.get(str(m.id)) for m in measurements):
        missing = sorted(str(m.measurement_id) for m in measurements
                         if not evidence_by_measurement.get(str(m.id)))
        raise BoqServiceError(
            "provenance_missing",
            "measurement evidence missing for: " + ", ".join(missing),
        )
    element_ids = {m.element_id for m in measurements}
    geometry_rows = (await session.execute(
        select(GeometryModel).where(GeometryModel.element_id.in_(element_ids))
        .order_by(GeometryModel.element_id, GeometryModel.id)
    )).scalars().all()
    handles_by_element: dict[str, list[dict[str, Any]]] = {}
    for geometry in geometry_rows:
        handles_by_element.setdefault(str(geometry.element_id), []).extend(
            sorted((dict(handle) for handle in geometry.source_handles),
                   key=lambda handle: json.dumps(handle, sort_keys=True))
        )
    if any(not handles_by_element.get(str(m.element_id)) for m in measurements):
        missing = sorted(str(m.measurement_id) for m in measurements
                         if not handles_by_element.get(str(m.element_id)))
        raise BoqServiceError(
            "provenance_missing",
            "source handles missing for: " + ", ".join(missing),
        )
    elements = (await session.execute(
        select(Element).where(Element.id.in_(element_ids))
    )).scalars().all()
    labels = {str(element.id): element.label for element in elements}
    drawing_id = (await session.execute(
        select(MeasurementRun.params).where(MeasurementRun.id == boq.from_run_id)
    )).scalar_one_or_none() or {}
    drawing_file_id = drawing_id.get("drawing_file_id")
    drawing = None
    if drawing_file_id:
        drawing = (await session.execute(
            select(DrawingFile).where(DrawingFile.id == drawing_file_id)
        )).scalar_one_or_none()
    if not drawing_file_id or drawing is None:
        raise BoqServiceError(
            "provenance_missing", "source drawing identity is missing")
    source = {
        "drawing_file_id": str(drawing.id) if drawing else drawing_file_id,
        "drawing_sha256": drawing.sha256 if drawing else None,
        "drawing_format": drawing.format if drawing else None,
    }
    records: list[dict[str, Any]] = []
    for identity in identities:
        measurement = by_identity[identity]
        records.append({
            "measurement_id": identity,
            "measurement_row_id": str(measurement.id),
            "run_id": str(measurement.run_id),
            "element_id": str(measurement.element_id),
            "element_label": labels.get(str(measurement.element_id)),
            "quantity_type": measurement.quantity_type,
            "value": str(measurement.value) if measurement.value is not None else None,
            "corrected_value": (
                str(measurement.corrected_value)
                if measurement.corrected_value is not None else None
            ),
            "unit": measurement.unit,
            "state": measurement.state,
            "rule_id": measurement.rule_id,
            "engine_version": measurement.engine_version,
            "inputs": measurement.inputs,
            "inputs_digest": measurement.inputs_digest,
            "source": source,
            "source_handles": handles_by_element[str(measurement.element_id)],
            "evidence": evidence_by_measurement[str(measurement.id)],
        })
    return records


def _line_total(quantity: Decimal | None, rate_minor: int | None,
               markup_bp: int, currency: str) -> int:
    """Line total from (quantity, rate, markup) via the pricing kernel.

    THE invariant-5 helper for edited lines: every human edit re-derives
    the total through core.units.money (banker's rounding, integer minor
    units) — never a cached or hand-computed number. A line without rate
    or without quantity totals to 0 (unpriced lines surface in the
    validation report instead of pretending).
    """
    if rate_minor is None or quantity is None:
        return 0
    base = multiply_rate(quantity, rate_minor, currency=currency)
    if markup_bp:
        return base.amount_minor + apply_markup(base, markup_bp).amount_minor
    return base.amount_minor


class _ExportRow:
    """Structural CsvExportRow over a dict (exports Protocol boundary)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._d = data

    @property
    def code(self) -> str:
        return str(self._d["code"])

    @property
    def description(self) -> str:
        return str(self._d["description"])

    @property
    def unit(self) -> str:
        return str(self._d["unit"])

    @property
    def quantity(self) -> Decimal:
        return cast(Decimal, self._d["quantity"])

    @property
    def rate_minor(self) -> int:
        return int(self._d["rate_minor"])

    @property
    def markup_bp(self) -> int:
        return int(self._d["markup_bp"])

    @property
    def total_minor(self) -> int:
        return int(self._d["total_minor"])

    @property
    def currency(self) -> str:
        return str(self._d["currency"])

    @property
    def measurement_ids(self) -> tuple[str, ...]:
        return tuple(self._d["measurement_ids"])


async def execute_export(
    session: AsyncSession, *, export_id: str, storage: Storage, actor: str,
) -> dict[str, Any]:
    """Export job body: load trusted approval scope, re-validate, store artifact.

    The ExportApproval context is constructed HERE from persisted rows —
    never accepted from the client. Every writer (csv/xlsx/pdf) re-validates
    every row and the digest binding; a mismatch (stale) or any blocker
    refuses the export regardless of format.
    """
    artifact = (await session.execute(
        select(ExportArtifact).where(ExportArtifact.id == export_id)
    )).scalar_one_or_none()
    if artifact is None:
        raise BoqServiceError("not_found", "export not found", 404)
    if artifact.status != "pending":
        return {"ok": artifact.status == "succeeded", "status": artifact.status}

    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == artifact.boq_id)
    )).scalar_one_or_none()
    if boq is None:
        artifact.status = "failed"
        await session.flush()
        return {"ok": False, "status": "failed", "error": "boq_missing"}

    try:
        # Server-side gate 1: approval state.
        if boq.status not in (BoqStatus.APPROVED.value, BoqStatus.EXPORTED.value):
            raise BoqServiceError(
                "not_approved", f"BOQ is {boq.status!r}, not APPROVED/EXPORTED")
        # Gate 2: unresolved blockers on the source run.
        blockers = (await _unresolved_blockers(session, str(boq.from_run_id))
                    if boq.from_run_id else [])
        if blockers:
            raise BoqServiceError(
                "blockers_present",
                "; ".join(f"{b.code}: {b.message}" for b in blockers))
        rows = await _boq_rows_for_export(session, boq)
        if not rows:
            raise BoqServiceError("no_items", "no priced rows to export")
        provenance_records = await _provenance_records_for_export(session, boq, rows)
        # Gate 3: the trusted approval context — built from persistence.
        approval = ExportApproval(
            boq_id=str(boq.id),
            approval_id=f"boq:{boq.id}:v{boq.version}:approved:{boq.approved_at}",
            rows_digest=rows_digest([_ExportRow(r) for r in rows]),
            status=BoqStatus(boq.status),
        )
        # Format dispatch — every format runs the SAME gates above; the
        # writers each re-validate the rows + digest again inside.
        if artifact.format == "csv":
            data = csv_bytes([_ExportRow(r) for r in rows], approval=approval)
        elif artifact.format == "xlsx":
            from exports.xlsx_export import xlsx_bytes

            data = xlsx_bytes([_ExportRow(r) for r in rows], approval=approval)
        elif artifact.format == "pdf":
            from exports.pdf_export import pdf_bytes

            data = pdf_bytes([_ExportRow(r) for r in rows], approval=approval)
        else:
            raise BoqServiceError("bad_format",
                                  f"unsupported export format {artifact.format!r}")
        sha = hashlib.sha256(data).hexdigest()
        key = f"exports/{boq.id}/{artifact.id}.{artifact.format}"
        storage.put(key, BytesIO(data), length=len(data))
        sidecar_rows = [
            {
                "code": row["code"],
                "description": row["description"],
                "unit": row["unit"],
                "quantity": str(row["quantity"]),
                "rate_minor": row["rate_minor"],
                "markup_bp": row["markup_bp"],
                "total_minor": row["total_minor"],
                "currency": row["currency"],
                "measurement_ids": list(row["measurement_ids"]),
            }
            for row in rows
        ]
        sidecar = provenance_sidecar_bytes(
            boq={
                "id": str(boq.id),
                "version": boq.version,
                "status": boq.status,
                "source_run_id": str(boq.from_run_id) if boq.from_run_id else None,
            },
            rows=sidecar_rows,
            measurements=provenance_records,
        )
        sidecar_sha = hashlib.sha256(sidecar).hexdigest()
        sidecar_key = f"exports/{boq.id}/{artifact.id}.provenance.json"
        storage.put(sidecar_key, BytesIO(sidecar), length=len(sidecar))
        artifact.storage_key = key
        artifact.sha256 = sha
        artifact.status = "succeeded"
        artifact.manifest = {
            "boq_id": str(boq.id), "boq_version": boq.version,
            "format": artifact.format, "row_count": len(rows),
            "rows_sha256": approval.rows_digest,
            "run_ids": [str(boq.from_run_id)] if boq.from_run_id else [],
            "engine_version": None,
            "provenance": {
                "schema_version": "boq-provenance-v1",
                "storage_key": sidecar_key,
                "sha256": sidecar_sha,
                "measurement_count": len(provenance_records),
            },
        }
        # APPROVED -> EXPORTED is the machine's terminal-export step.
        if boq.status == BoqStatus.APPROVED.value:
            boq.status = transition_boq(
                BoqStatus.APPROVED, BoqStatus.EXPORTED).value
        session.add(AuditEntry(
            id=str(uuid.uuid4()), actor=_as_uuid(actor),
            action=AuditAction.EXPORT.value, subject_type="export",
            subject_id=_as_uuid(artifact.id),
            project_id=boq.project_id,
            after={"sha256": sha, "boq_status": boq.status},
        ))
        await session.flush()
        return {"ok": True, "status": "succeeded", "sha256": sha}
    except (BoqServiceError, ValueError) as exc:
        artifact.status = "failed"
        artifact.manifest = {"error": str(exc)}
        await session.flush()
        return {"ok": False, "status": "failed", "error": str(exc)}
