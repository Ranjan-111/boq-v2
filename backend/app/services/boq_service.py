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

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    ExceptionModel,
    ExportArtifact,
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
from exports.csv_export import ExportApproval, csv_bytes, rows_digest

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
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.IN_REVIEW).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.SUBMIT.value if hasattr(AuditAction, "SUBMIT")
                 else AuditAction.UPDATE.value, boq)
    return {"status": boq.status}


async def review_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                    actor: str, note: str | None = None) -> dict[str, Any]:
    """IN_REVIEW -> REVIEWED: a human has completed their review pass.

    This is the hop the approval gate requires (approve only accepts
    REVIEWED); it is the reviewer's explicit statement, so it is audited
    like every other transition.
    """
    boq = await _owned_boq(session, boq_id, project_id)
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.REVIEWED).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.UPDATE.value, boq, note=note)
    return {"status": boq.status}


async def approve_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                      actor: str, note: str | None = None) -> dict[str, Any]:
    """APPROVE only when: legal transition, items exist, zero unresolved
    blockers on the source run (server-side check, never client-supplied)."""
    boq = await _owned_boq(session, boq_id, project_id)
    if boq.status not in (BoqStatus.REVIEWED.value, BoqStatus.DRAFT.value,
                          BoqStatus.IN_REVIEW.value):
        # DRAFT/IN_REVIEW/REVIEWED -> APPROVED must pass through REVIEWED per
        # the machine; be strict: only REVIEWED can approve.
        pass
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
    boq.approved_by = str(actor)
    boq.approved_at = datetime.now(UTC)
    await _audit(session, actor, AuditAction.APPROVE.value, boq, note=note)
    return {"status": boq.status}


async def reject_boq(session: AsyncSession, *, project_id: str, boq_id: str,
                     actor: str, note: str | None = None) -> dict[str, Any]:
    boq = await _owned_boq(session, boq_id, project_id)
    try:
        boq.status = transition_boq(BoqStatus(boq.status), BoqStatus.DRAFT).value
    except ValueError as exc:
        raise BoqServiceError("illegal_transition", str(exc)) from exc
    await _audit(session, actor, AuditAction.REJECT.value, boq, note=note)
    return {"status": boq.status}


async def mark_stale_if_approved(session: AsyncSession, boq: BoqModel) -> None:
    """Any post-approval mutation of a BOQ flips APPROVED -> STALE_APPROVED."""
    if boq.status == BoqStatus.APPROVED.value:
        boq.status = transition_boq(BoqStatus.APPROVED, BoqStatus.STALE_APPROVED).value


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Coerce an actor/subject id to a native uuid.

    ORM rows are typed Mapped[str] but asyncpg returns pgproto UUID objects
    for Uuid columns — so a caller-selected user/boq hands us a UUID here
    while hand-built test rows hand us str. Accept both (str round-trips
    through uuid.UUID; a UUID passes through).
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _audit(session: AsyncSession, actor: str, action: str, boq: BoqModel,
                 *, note: str | None = None) -> None:
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=_as_uuid(actor), action=action,
        subject_type="boq", subject_id=_as_uuid(boq.id),
        before={"status": boq.status}, after={"status": boq.status},
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
        return self._d["quantity"]

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
    never accepted from the client. csv_bytes re-validates every row and the
    digest binding; a mismatch (stale) or any blocker refuses the export.
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
        # Gate 3: the trusted approval context — built from persistence.
        approval = ExportApproval(
            boq_id=str(boq.id),
            approval_id=f"boq:{boq.id}:v{boq.version}:approved:{boq.approved_at}",
            rows_digest=rows_digest([_ExportRow(r) for r in rows]),
            status=BoqStatus(boq.status),
        )
        data = csv_bytes([_ExportRow(r) for r in rows], approval=approval)
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        key = f"exports/{boq.id}/{artifact.id}.csv"
        storage.put(key, BytesIO(data), length=len(data))
        artifact.storage_key = key
        artifact.sha256 = sha
        artifact.status = "succeeded"
        artifact.manifest = {
            "boq_id": str(boq.id), "boq_version": boq.version,
            "format": "csv", "row_count": len(rows),
            "rows_sha256": approval.rows_digest,
            "run_ids": [str(boq.from_run_id)] if boq.from_run_id else [],
            "engine_version": None,
        }
        # APPROVED -> EXPORTED is the machine's terminal-export step.
        if boq.status == BoqStatus.APPROVED.value:
            boq.status = transition_boq(
                BoqStatus.APPROVED, BoqStatus.EXPORTED).value
        session.add(AuditEntry(
            id=str(uuid.uuid4()), actor=_as_uuid(actor),
            action=AuditAction.EXPORT.value, subject_type="export",
            subject_id=_as_uuid(artifact.id),
            after={"sha256": sha, "boq_status": boq.status},
        ))
        await session.flush()
        return {"ok": True, "status": "succeeded", "sha256": sha}
    except (BoqServiceError, ValueError) as exc:
        artifact.status = "failed"
        artifact.manifest = {"error": str(exc)}
        await session.flush()
        return {"ok": False, "status": "failed", "error": str(exc)}
