"""Round 7 Worker B slice — manual catalogue mapping + audited suggestion apply.

Pinned behavior (route functions against a live migrated scratch DB, the
test_review_actions idiom):
  * the happy-path map appends a mapped BoqItem to the run's DRAFT BOQ
    priced through the money kernel, resolves exactly THIS measurement's
    unmapped_measurement blocker (sibling blockers stay open), and writes a
    project-scoped MAP_CATALOGUE audit row carrying the caller's reason,
  * a corrected measurement bills the corrected value through the mapping
    (quantity = the effective value — the R6 doctrine),
  * unit_mismatch / missing_rate / state_not_mappable / missing_evidence /
    no_draft_boq / not_draft / already_mapped refusals leave NOTHING written,
  * apply performs the EXACT T073 semantics: the human word lands,
    type_source=human_set, the AI voice is stamped from the suggestion when
    the row has none and PRESERVED when it has one, accepted=True, audited
    with applied_suggestion provenance,
  * THE QUANTITY GUARD (the red line): a rogue suggestion kind
    (quantity_estimate) crafted straight into the table refuses not_applyable
    before any row is touched — no suggestion kind can ever write a quantity,
  * exception_explanation is informational_only; a second apply is
    already_applied; strangers and malformed ids are honest 404s, never 500s.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.review import (
    ApplySuggestionBody,
    MapCatalogueBody,
    MeasurementReviewBody,
    apply_suggestion,
    map_measurement_to_catalogue,
    review_measurement,
)
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AiSuggestion,
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    Element,
    ExceptionModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import ai_service, boq_service, run_service
from backend.app.storage.base import MemoryStorage
from core.domain.enums import BoqStatus, MeasurementState
from core.units.money import multiply_rate

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


# ---------------------------------------------------------------------------
# Setup helpers (the test_review_actions idiom, copied not imported)
# ---------------------------------------------------------------------------


async def _setup(migrated_db: str) -> tuple[AsyncEngine, AsyncSession, User,
                                            Project, DrawingFile, DrawingSheet,
                                            MemoryStorage]:
    """User + project + parsed drawing + modelspace sheet + PROPOSED cal."""
    engine = make_async_engine(migrated_db)
    session = await make_sessionmaker(engine)().__aenter__()
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t", role="owner")
    session.add(user)
    await session.flush()
    project = Project(id=str(uuid.uuid4()), name="P", region_code="IN",
                      currency="INR", created_by=user.id)
    session.add(project)
    await session.flush()
    storage = MemoryStorage()
    data = FIXTURE.read_bytes()
    key = f"uploads/dxf/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}"
    storage.put(key, __import__("io").BytesIO(data), length=len(data))
    drawing = DrawingFile(id=str(uuid.uuid4()), project_id=project.id,
                          filename="wall_plan.dxf", format="dxf",
                          size_bytes=len(data), storage_key=key,
                          sha256=uuid.uuid4().hex, uploaded_by=user.id,
                          parse_status="parsed")
    session.add(drawing)
    await session.flush()
    sheet = DrawingSheet(id=str(uuid.uuid4()), drawing_file_id=drawing.id,
                         page_number=0, title="Model", sheet_ref="modelspace",
                         sheet_type="plan")
    session.add(sheet)
    await session.flush()
    cal = ScaleCalibrationModel(id=str(uuid.uuid4()), sheet_id=sheet.id,
                                status="proposed",
                                method="detected_from_dxf_units")
    session.add(cal)
    await session.flush()
    return engine, session, user, project, drawing, sheet, storage


async def _close(engine: AsyncEngine, session: AsyncSession) -> None:
    await session.rollback()
    await session.close()
    await engine.dispose()


async def _confirmed_run(session: AsyncSession, user: User, project: Project,
                         drawing: DrawingFile, sheet: DrawingSheet,
                         storage: MemoryStorage) -> MeasurementRun:
    """Human-confirmed scale + a completed run (8 wall measurements)."""
    cal = (await session.execute(
        select(ScaleCalibrationModel)
        .where(ScaleCalibrationModel.sheet_id == sheet.id)
    )).scalar_one()
    cal.status = "confirmed"
    cal.units_per_drawing_unit = Decimal("1.0")
    cal.method = "user_two_point"
    cal.confirmed_by = user.id
    await session.flush()
    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                        status="queued",
                        params={"drawing_file_id": str(drawing.id),
                                "sheet_id": str(sheet.id),
                                "max_wall_thickness": 250.0})
    session.add(run)
    await session.flush()
    result = await run_service.execute_run(session, run_id=run.id,
                                          storage=storage, max_wall_thickness=250)
    assert result["ok"] is True
    return run


async def _seed_catalogue(
    session: AsyncSession,
) -> dict[str, CatalogueItem]:
    """One item per mapped unit group; returns code -> item for the tests.
    The single m2 item is the collision the build cannot settle (footprint
    vs net both claim it) — those m2 measurements are the unmapped blockers
    the mapping endpoint exists to resolve."""
    items: dict[str, CatalogueItem] = {}
    for code, description, unit, amount in (
        ("2.1.1", "Brick wall 230mm thick", "m", 85_000),
        ("2.1.2", "Brick wall (measured area)", "m2", 4_250),
        ("2.1.4", "Door/window opening count", "count", 1_200_000),
    ):
        item = CatalogueItem(id=str(uuid.uuid4()), region_code="IN", code=code,
                             description=description, unit=unit,
                             category_path="walls/brick", source="manual")
        session.add(item)
        await session.flush()
        session.add(RateModel(id=str(uuid.uuid4()),
                              catalogue_item_id=item.id, scope="default",
                              currency="INR", amount_minor=amount))
        await session.flush()
        items[code] = item
    return items


async def _run_measurements(
    session: AsyncSession, run: MeasurementRun
) -> list[MeasurementModel]:
    """Re-selected rows (the asyncpg str-vs-UUID identity-map trap)."""
    return list((await session.execute(
        select(MeasurementModel).where(MeasurementModel.run_id == run.id)
        .order_by(MeasurementModel.created_at, MeasurementModel.id)
    )).scalars().all())


async def _build_draft(
    session: AsyncSession, user: User, project: Project, run: MeasurementRun,
) -> BoqModel:
    """Seed the catalogue + build the run's DRAFT BOQ (m + count map; the m2
    collision rows persist as unmapped blockers)."""
    await _seed_catalogue(session)
    built = await boq_service.build_boq_from_run(
        session, project_id=project.id, from_run_id=str(run.id),
        actor=user.id)
    return (await session.execute(
        select(BoqModel).where(BoqModel.id == built["boq_id"])
    )).scalar_one()


async def _unmapped_m2_rows(
    session: AsyncSession, run: MeasurementRun,
) -> list[MeasurementModel]:
    """The run's MEASURED m2 rows — the build's collision blockers."""
    rows = [m for m in await _run_measurements(session, run)
            if m.unit == "m2"
            and m.state in (MeasurementState.MEASURED.value,
                            MeasurementState.MEASURED_ZERO.value)]
    assert rows, "wall_plan emits the m2 collision rows"
    return rows


def _blocker_message(m: MeasurementModel) -> str:
    """The exact message boq_service.build_boq_from_run writes for this
    measurement's unmapped_measurement blocker."""
    return (f"measurement not mapped to any catalogue item: "
            f"{m.label or m.measurement_id} ({m.rule_id} {m.unit})")


async def _map(session: AsyncSession, user: User, ref: str,
              catalogue_item_id: str,
              reason: str = "human mapping decision") -> dict[str, Any]:
    return await map_measurement_to_catalogue(
        ref, MapCatalogueBody(catalogue_item_id=catalogue_item_id,
                              reason=reason), user, session)


async def _apply(session: AsyncSession, user: User, suggestion_id: str,
                reason: str = "human accepts the AI proposal") -> dict[str, Any]:
    return await apply_suggestion(
        suggestion_id, ApplySuggestionBody(reason=reason), user, session)


async def _seed_suggestion(
    session: AsyncSession, run: MeasurementRun, *,
    suggestion_type: str, payload: dict[str, Any],
    subject_type: str = "element", subject_id: Any | None = None,
    confidence: float = 0.05, model: str = "rogue-1",
) -> AiSuggestion:
    """A suggestion row crafted straight into the table — the rogue-provider
    simulation (sanitize never ran on it; that is the point)."""
    row = AiSuggestion(
        id=str(uuid.uuid4()), run_id=run.id,
        subject_type=subject_type,
        subject_id=uuid.UUID(str(subject_id if subject_id is not None
                                 else uuid.uuid4())),
        suggestion_type=suggestion_type, payload=payload,
        confidence=confidence, model=model, prompt_log_id=None)
    session.add(row)
    await session.flush()
    return row


def _http_status(exc: BaseException) -> int:
    assert isinstance(exc, HTTPException)
    return exc.status_code


def _http_code(exc: BaseException) -> str:
    assert isinstance(exc, HTTPException)
    detail: Any = exc.detail
    assert isinstance(detail, list) and detail and isinstance(detail[0], dict)
    code = detail[0].get("code")
    assert isinstance(code, str)
    return code


def _http_message(exc: BaseException) -> str:
    assert isinstance(exc, HTTPException)
    detail: Any = exc.detail
    assert isinstance(detail, list) and detail
    message = detail[0].get("message")
    assert isinstance(message, str)
    return message


async def _stranger(session: AsyncSession) -> User:
    stranger = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                    password_hash=uuid.uuid4().hex, display_name="t",
                    role="owner")
    session.add(stranger)
    await session.flush()
    return stranger


# ---------------------------------------------------------------------------
# T084 — manual mapping: happy path
# ---------------------------------------------------------------------------


class TestMapHappyPath:
    async def test_map_appends_priced_item_resolves_one_blocker_audits(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq = await _build_draft(session, user, project, run)
            rows = await _unmapped_m2_rows(session, run)
            target = rows[0]
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            assert items, "the m2 catalogue item exists"

            out = await _map(session, user, str(target.id),
                             str(items[0].id),
                             reason="gross area bills the footprint line")
            assert out["ok"] is True
            assert out["boq_id"] == str(boq.id)
            assert out["code"] == "2.1.2"
            assert out["resolved_blockers"] == 1

            # The appended item: origin mapped, effective value, the selected
            # rate, DURABLE identity provenance, money-kernel total.
            item = (await session.execute(
                select(BoqItem).where(BoqItem.id == out["item_id"])
            )).scalar_one()
            assert item.section_id is not None
            assert item.origin == "mapped"
            assert str(item.catalogue_item_id) == str(items[0].id)
            assert item.description == items[0].description
            assert item.unit == "m2"
            assert Decimal(str(item.quantity)) == Decimal(str(target.value))
            assert item.rate_minor == 4_250
            assert item.rate_scope == "default"
            assert item.markup_bp == 0
            assert item.measurement_ids == [target.measurement_id]
            expected = multiply_rate(
                Decimal(str(target.value)), 4_250, currency="INR").amount_minor
            assert item.total_minor == expected
            # sort_order = max existing + 1 (the build wrote items 0 and 1).
            assert item.sort_order == 2

            # Exactly THIS measurement's blocker resolved; siblings open.
            blocker = (await session.execute(
                select(ExceptionModel).where(
                    ExceptionModel.run_id == run.id,
                    ExceptionModel.code == "unmapped_measurement",
                    ExceptionModel.message == _blocker_message(target))
            )).scalar_one()
            assert blocker.resolved_at is not None
            assert blocker.resolution == "mapped to 2.1.2 by human"
            still_open = (await session.execute(
                select(ExceptionModel).where(
                    ExceptionModel.run_id == run.id,
                    ExceptionModel.code == "unmapped_measurement",
                    ExceptionModel.resolved_at.is_(None))
            )).scalars().all()
            assert len(still_open) == len(rows) - 1

            # The audit row: MAP_CATALOGUE, project-scoped, reason verbatim.
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "map_catalogue"
            assert audit.subject_type == "measurement"
            assert str(audit.subject_id) == str(target.id)
            assert str(audit.project_id) == str(project.id)
            assert audit.reason == "gross area bills the footprint line"
            assert audit.before == {"mapped": None}
            after: dict[str, Any] = audit.after or {}
            assert after["catalogue_item_id"] == str(items[0].id)
            assert after["code"] == "2.1.2"
        finally:
            await _close(engine, session)

    async def test_map_bills_the_corrected_value(self, migrated_db: str) -> None:
        """quantity = effective value: a corrected measurement bills the
        correction through the mapping (the R6 doctrine)."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            corrected = Decimal(str(target.value)) + Decimal("1.5")
            await review_measurement(
                target.measurement_id,
                MeasurementReviewBody(action="correct", value=corrected,
                                       reason="site measured more"),
                user, session)
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            out = await _map(session, user, str(target.id), str(items[0].id))
            item = (await session.execute(
                select(BoqItem).where(BoqItem.id == out["item_id"])
            )).scalar_one()
            assert Decimal(str(item.quantity)) == corrected
            expected = multiply_rate(
                corrected, 4_250, currency="INR").amount_minor
            assert item.total_minor == expected
        finally:
            await _close(engine, session)

    async def test_map_by_durable_identity_resolves_the_same_row(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            out = await _map(session, user, target.measurement_id,
                            str(items[0].id))
            assert out["ok"] is True
            item = (await session.execute(
                select(BoqItem).where(BoqItem.id == out["item_id"])
            )).scalar_one()
            assert item.measurement_ids == [target.measurement_id]
        finally:
            await _close(engine, session)

    async def test_two_runs_same_drawing_identity_is_ambiguous_409(
        self, migrated_db: str,
    ) -> None:
        """The durable identity is unique PER RUN — a second run of the same
        drawing makes the identity ambiguous and the mapping refuses (the
        row id is the disambiguator, the resolve_measurement doctrine)."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run1 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _confirmed_run(session, user, project, drawing,
                                 sheet, storage)
            await _seed_catalogue(session)
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            target = (await _unmapped_m2_rows(session, run1))[0]
            with pytest.raises(HTTPException) as err:
                await _map(session, user, target.measurement_id,
                          str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "ambiguous_measurement_identity"
            # No BOQ exists yet for either run — the refusal happened at
            # resolution, before any write.
            assert (await session.execute(
                select(BoqModel).where(BoqModel.project_id == project.id)
            )).scalars().all() == []
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# T084 — manual mapping: refusals
# ---------------------------------------------------------------------------


class TestMapRefusals:
    async def test_unit_mismatch_refused_nothing_written(self, migrated_db: str,
                                                         ) -> None:
        async def _project_item_count(session: AsyncSession) -> int:
            return len((await session.execute(
                select(BoqItem).join(BoqSection,
                                     BoqItem.section_id == BoqSection.id)
                .join(BoqModel, BoqSection.boq_id == BoqModel.id)
                .where(BoqModel.project_id == project.id)
            )).scalars().all())

        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            m_item = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.1")
            )).scalars().one()
            before_count = await _project_item_count(session)
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(m_item.id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "unit_mismatch"
            message = _http_message(err.value)
            assert "m2" in message and "'m'" in message
            # Refused = nothing moved: no item, no resolved blocker, no audit.
            assert await _project_item_count(session) == before_count
            blocker = (await session.execute(
                select(ExceptionModel).where(
                    ExceptionModel.run_id == run.id,
                    ExceptionModel.message == _blocker_message(target))
            )).scalar_one()
            assert blocker.resolved_at is None
            maps = (await session.execute(
                select(AuditEntry).where(
                    AuditEntry.action == "map_catalogue")
            )).scalars().all()
            assert maps == []
        finally:
            await _close(engine, session)

    async def test_missing_rate_refused(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            # A priced-less m2 item: exists, but no project/default rate.
            unrated = CatalogueItem(id=str(uuid.uuid4()), region_code="IN",
                                    code="9.9.9", description="no rate yet",
                                    unit="m2", category_path="walls",
                                    source="manual")
            session.add(unrated)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(unrated.id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "missing_rate"
        finally:
            await _close(engine, session)

    async def test_no_draft_boq_refused(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            items = await _seed_catalogue(session)
            target = (await _unmapped_m2_rows(session, run))[0]
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id),
                          str(items["2.1.2"].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "no_draft_boq"
        finally:
            await _close(engine, session)

    async def test_past_draft_boq_refused(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _build_draft(session, user, project, run)
            # Walk the honest path to REVIEWED (blockers resolved by hand,
            # submit, review) — the mapping must then refuse.
            blockers = (await session.execute(
                select(ExceptionModel).where(
                    ExceptionModel.run_id == run.id,
                    ExceptionModel.resolved_at.is_(None))
            )).scalars().all()
            for b in blockers:
                b.resolved_at = __import__("datetime").datetime.now(
                    __import__("datetime").UTC)
            await session.flush()
            await boq_service.submit_boq(session, project_id=project.id,
                                        boq_id=str(boq.id), actor=user.id)
            reviewed = await boq_service.review_boq(
                session, project_id=project.id, boq_id=str(boq.id),
                actor=user.id)
            assert reviewed["status"] == BoqStatus.REVIEWED.value
            rows = await _run_measurements(session, run)
            # An m2 row that was never mapped — the refusable candidate.
            target = next(m for m in rows if m.unit == "m2")
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "not_draft"
            assert "reviewed" in _http_message(err.value)
        finally:
            await _close(engine, session)

    async def test_already_mapped_refused_naming_existing_item(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            target = (await _unmapped_m2_rows(session, run))[0]
            first = await _map(session, user, str(target.id), str(items[0].id))
            assert first["ok"] is True
            # Same item again — do not double-bill.
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "already_mapped"
            # A DIFFERENT item too: remapping is remove-then-map (V1).
            other = CatalogueItem(id=str(uuid.uuid4()), region_code="IN",
                                 code="2.1.3", description="alt m2 item",
                                 unit="m2", category_path="walls",
                                 source="manual")
            session.add(other)
            await session.flush()
            session.add(RateModel(id=str(uuid.uuid4()),
                                  catalogue_item_id=other.id, scope="default",
                                  currency="INR", amount_minor=9_000))
            await session.flush()
            with pytest.raises(HTTPException) as err2:
                await _map(session, user, str(target.id), str(other.id))
            assert _http_status(err2.value) == 409
            assert _http_code(err2.value) == "already_mapped"
            # And a measurement the BUILD already mapped (the m line).
            m_item = (await session.execute(
                select(BoqItem, CatalogueItem.code)
                .join(BoqSection, BoqItem.section_id == BoqSection.id)
                .outerjoin(CatalogueItem,
                           BoqItem.catalogue_item_id == CatalogueItem.id)
                .where(BoqItem.unit == "m")
            )).first()
            assert m_item is not None and m_item[0].measurement_ids
            billed_identity = m_item[0].measurement_ids[0]
            billed_row = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id,
                    MeasurementModel.measurement_id == billed_identity)
            )).scalar_one()
            with pytest.raises(HTTPException) as err3:
                await _map(session, user, str(billed_row.id),
                          str(items[0].id))
            assert _http_status(err3.value) == 409
            assert _http_code(err3.value) == "already_mapped"
            # No third line appeared.
            mapped_count = (await session.execute(
                select(BoqItem).join(BoqSection,
                                     BoqItem.section_id == BoqSection.id)
                .where(BoqItem.origin == "mapped")
            )).scalars().all()
            assert len(mapped_count) == 3  # m + count (build) + m2 (map)
        finally:
            await _close(engine, session)

    async def test_state_not_mappable_refused(self, migrated_db: str) -> None:
        """blocked rows leave the queue through review or a new run — never
        a mapping (the assembly path's own gate)."""
        from core.domain.states import transition_measurement

        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            target.state = transition_measurement(
                MeasurementState(str(target.state)),
                MeasurementState.BLOCKED).value
            await session.flush()
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "state_not_mappable"
        finally:
            await _close(engine, session)

    async def test_missing_evidence_refused(self, migrated_db: str) -> None:
        """MEASURED rows bill only with evidence — the binding doctrine."""
        from backend.app.db.models import EvidenceLinkModel

        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            links = (await session.execute(
                select(EvidenceLinkModel).where(
                    EvidenceLinkModel.subject_type == "measurement",
                    EvidenceLinkModel.subject_id
                    == uuid.UUID(str(target.id)))
            )).scalars().all()
            assert links, "engine rows carry evidence"
            for link in links:
                await session.delete(link)
            await session.flush()
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "missing_evidence"
        finally:
            await _close(engine, session)

    async def test_stranger_gets_404(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            items = await _seed_catalogue(session)
            target = (await _unmapped_m2_rows(session, run))[0]
            stranger = await _stranger(session)
            with pytest.raises(HTTPException) as err:
                await _map(session, stranger, str(target.id),
                           str(items["2.1.2"].id))
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)

    async def test_malformed_refs_are_404_not_500(self, migrated_db: str) -> None:
        """The asyncpg UUID-cast trap: garbage input must 404 before SQL."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            items = await _seed_catalogue(session)
            target = (await _unmapped_m2_rows(session, run))[0]
            with pytest.raises(HTTPException) as err:
                await _map(session, user, "not-a-uuid",
                           str(items["2.1.2"].id))
            assert _http_status(err.value) == 404
            with pytest.raises(HTTPException) as err2:
                await _map(session, user, str(target.id), "not-a-uuid")
            assert _http_status(err2.value) == 404
            with pytest.raises(HTTPException) as err3:
                await _map(session, user, "../../etc/passwd",
                           str(items["2.1.2"].id))
            assert _http_status(err3.value) == 404
        finally:
            await _close(engine, session)

    async def test_missing_catalogue_item_404(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _seed_catalogue(session)
            target = (await _unmapped_m2_rows(session, run))[0]
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(uuid.uuid4()))
            assert _http_status(err.value) == 404
            assert _http_code(err.value) == "not_found"
        finally:
            await _close(engine, session)

    async def test_two_draft_boqs_is_ambiguous_409(self, migrated_db: str) -> None:
        """Two DRAFTs from one run is a genuinely ambiguous target — the
        endpoint takes no boq_id, so ambiguity is a blocker, never a pick."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            await _seed_catalogue(session)
            await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=str(run.id),
                actor=user.id)
            await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=str(run.id),
                actor=user.id)
            target = (await _unmapped_m2_rows(session, run))[0]
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            with pytest.raises(HTTPException) as err:
                await _map(session, user, str(target.id), str(items[0].id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "ambiguous_draft_boq"
        finally:
            await _close(engine, session)

    async def test_blank_reason_refused_by_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            MapCatalogueBody(catalogue_item_id=str(uuid.uuid4()),
                             reason="   ")
        with pytest.raises(ValidationError):
            ApplySuggestionBody(reason="   ")


# ---------------------------------------------------------------------------
# T084 — audited suggestion apply
# ---------------------------------------------------------------------------


class TestApplySuggestion:
    async def _analyzed_run(
        self, session: AsyncSession, user: User, project: Project,
        drawing: DrawingFile, sheet: DrawingSheet, storage: MemoryStorage,
    ) -> tuple[MeasurementRun, list[AiSuggestion], list[Element]]:
        """A completed run + the stub analyze pass (2 wall elements -> 2
        element_classification suggestions, confidence 0.05)."""
        run = await _confirmed_run(session, user, project, drawing, sheet,
                                    storage)
        result = await ai_service.execute_analyze(session, run_id=str(run.id))
        assert result["ok"] is True
        suggestions = (await session.execute(
            select(AiSuggestion).where(AiSuggestion.run_id == run.id)
            .order_by(AiSuggestion.created_at, AiSuggestion.id)
        )).scalars().all()
        elements = (await session.execute(
            select(Element).where(Element.run_id == run.id)
            .order_by(Element.id)
        )).scalars().all()
        return run, list(suggestions), list(elements)

    async def test_apply_happy_path_t073_semantics_and_provenance(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            _run, suggestions, elements = await self._analyzed_run(
                session, user, project, drawing, sheet, storage)
            assert len(suggestions) == 2
            suggestion = suggestions[0]
            # created_at is transaction-frozen (PG now()), so suggestion
            # order is uuid order — resolve the subject, never the index.
            element = next(e for e in elements
                           if str(e.id) == str(suggestion.subject_id))
            payload: dict[str, Any] = suggestion.payload
            original_type = element.element_type
            original_source = element.type_source
            # Engine rows carry no ai_* voice — the apply stamps it FROM the
            # suggestion (provenance, not fabrication).
            assert element.ai_confidence is None
            assert element.ai_model is None
            assert element.ai_explanation is None

            out = await _apply(session, user, str(suggestion.id),
                               reason="stub says other; reviewer agrees")
            assert out["ok"] is True
            assert out["suggestion_id"] == str(suggestion.id)

            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh.element_type == payload["element_type"]
            assert fresh.type_source == "human_set"
            assert fresh.ai_model == suggestion.model
            assert Decimal(str(fresh.ai_confidence)) == Decimal(
                str(suggestion.confidence))
            assert fresh.ai_explanation == payload["rationale"]

            fresh_suggestion = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.id == suggestion.id)
            )).scalar_one()
            assert fresh_suggestion.accepted is True

            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "override_element_type"
            assert audit.subject_type == "element"
            assert str(audit.subject_id) == str(element.id)
            assert str(audit.project_id) == str(project.id)
            assert audit.reason == "stub says other; reviewer agrees"
            before: dict[str, Any] = audit.before or {}
            after: dict[str, Any] = audit.after or {}
            assert before["element_type"] == original_type
            assert before["type_source"] == original_source
            assert after["element_type"] == payload["element_type"]
            assert after["type_source"] == "human_set"
            assert after["applied_suggestion"] == str(suggestion.id)

            # A second apply of the same suggestion refuses.
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id), reason="again")
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "already_applied"
        finally:
            await _close(engine, session)

    async def test_apply_preserves_existing_ai_provenance(
        self, migrated_db: str,
    ) -> None:
        """An element that ALREADY has an ai_* voice keeps it — the apply is
        a human decision informed by AI; it never overwrites the old voice."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            _run, suggestions, elements = await self._analyzed_run(
                session, user, project, drawing, sheet, storage)
            element = elements[1]
            suggestion = next(
                s for s in suggestions
                if str(s.subject_id) == str(element.id))
            element.ai_confidence = Decimal("0.870")  # type: ignore[assignment]
            element.ai_model = "gpt-sight-1"
            element.ai_explanation = "an earlier voice, still true"
            await session.flush()

            out = await _apply(session, user, str(suggestion.id))
            assert out["ok"] is True
            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert Decimal(str(fresh.ai_confidence)) == Decimal("0.870")
            assert fresh.ai_model == "gpt-sight-1"
            assert fresh.ai_explanation == "an earlier voice, still true"
            assert fresh.type_source == "human_set"
        finally:
            await _close(engine, session)

    async def test_exception_explanation_is_informational_only(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            # A real unresolved blocker gives the informational suggestion an
            # honest subject.
            await _build_draft(session, user, project, run)
            blocker = (await session.execute(
                select(ExceptionModel).where(
                    ExceptionModel.run_id == run.id,
                    ExceptionModel.resolved_at.is_(None))
            )).scalars().first()
            assert blocker is not None
            suggestion = await _seed_suggestion(
                session, run, suggestion_type="exception_explanation",
                subject_type="exception", subject_id=str(blocker.id),
                payload={"explanation": "the m2 collision is ambiguous",
                         "suggested_action": "pick who bills"},
                confidence=0.10, model="stub")
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id))
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "informational_only"
            fresh = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.id == suggestion.id)
            )).scalar_one()
            assert fresh.accepted is None
        finally:
            await _close(engine, session)

    async def test_quantity_guard_rogue_kind_refused_nothing_written(
        self, migrated_db: str,
    ) -> None:
        """THE red line: a suggestion kind the allowlist never heard of — a
        rogue provider's quantity_estimate crafted straight into the table —
        cannot apply. The refusal names the kind and writes NOTHING."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            element = (await session.execute(
                select(Element).where(Element.run_id == run.id)
                .order_by(Element.id)
            )).scalars().first()
            assert element is not None
            measurements_before = await _run_measurements(session, run)
            suggestion = await _seed_suggestion(
                session, run, suggestion_type="quantity_estimate",
                payload={"quantity": "999", "rejected_fields": []},
                subject_id=str(element.id), confidence=0.99,
                model="rogue-provider")
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id),
                             reason="tries to smuggle a quantity")
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "not_applyable"
            assert "quantity_estimate" in _http_message(err.value)

            # Structurally nothing moved: element, measurements, suggestions,
            # BOQ rows, audit — the apply path has no write for this kind.
            fresh_element = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh_element.element_type == element.element_type
            assert fresh_element.type_source == element.type_source
            assert fresh_element.ai_model is None
            measurements_after = await _run_measurements(session, run)
            assert [(str(m.id), str(m.value), str(m.corrected_value),
                     m.state)
                    for m in measurements_after] == [
                (str(m.id), str(m.value), str(m.corrected_value), m.state)
                for m in measurements_before]
            fresh_suggestion = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.id == suggestion.id)
            )).scalar_one()
            assert fresh_suggestion.accepted is None
            assert (await session.execute(
                select(AuditEntry).where(
                    AuditEntry.subject_id == uuid.UUID(str(element.id)))
            )).scalars().all() == []
            assert (await session.execute(
                select(BoqModel).where(BoqModel.project_id == project.id)
            )).scalars().all() == []
        finally:
            await _close(engine, session)

    async def test_invalid_element_type_in_payload_422(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            element = (await session.execute(
                select(Element).where(Element.run_id == run.id)
                .order_by(Element.id)
            )).scalars().first()
            assert element is not None
            suggestion = await _seed_suggestion(
                session, run, suggestion_type="element_classification",
                payload={"element_type": "spaceship"}, subject_id=element.id)
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id))
            assert _http_status(err.value) == 422
            assert _http_code(err.value) == "invalid_element_type"
            assert "wall" in _http_message(err.value)
            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh.element_type == element.element_type
        finally:
            await _close(engine, session)

    async def test_missing_element_type_in_payload_422(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            element = (await session.execute(
                select(Element).where(Element.run_id == run.id)
                .order_by(Element.id)
            )).scalars().first()
            assert element is not None
            suggestion = await _seed_suggestion(
                session, run, suggestion_type="element_classification",
                payload={"label": "no type here"}, subject_id=element.id)
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id))
            assert _http_status(err.value) == 422
            assert _http_code(err.value) == "invalid_element_type"
        finally:
            await _close(engine, session)

    async def test_subject_of_another_run_404(self, migrated_db: str) -> None:
        """The suggestion's subject must be an element of its own run — a
        mismatched subject is refused, never applied cross-run."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run1 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            run2 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            element_of_run2 = (await session.execute(
                select(Element).where(Element.run_id == run2.id)
                .order_by(Element.id)
            )).scalars().first()
            assert element_of_run2 is not None
            suggestion = await _seed_suggestion(
                session, run1, suggestion_type="element_classification",
                payload={"element_type": "room"},
                subject_id=element_of_run2.id)
            with pytest.raises(HTTPException) as err:
                await _apply(session, user, str(suggestion.id))
            assert _http_status(err.value) == 404
            fresh = (await session.execute(
                select(Element)
                .where(Element.id == element_of_run2.id)
            )).scalar_one()
            assert fresh.element_type == element_of_run2.element_type
            assert fresh.type_source == "geometry_deterministic"
        finally:
            await _close(engine, session)

    async def test_stranger_and_malformed_ids_are_404(self, migrated_db: str,
                                                      ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run, _suggestions, _elements = await self._analyzed_run(
                session, user, project, drawing, sheet, storage)
            stranger = await _stranger(session)
            real = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.run_id == run.id)
            )).scalars().first()
            assert real is not None
            with pytest.raises(HTTPException) as err:
                await _apply(session, stranger, str(real.id))
            assert _http_status(err.value) == 404
            with pytest.raises(HTTPException) as err2:
                await _apply(session, user, "not-a-uuid")
            assert _http_status(err2.value) == 404
            with pytest.raises(HTTPException) as err3:
                await _apply(session, user, str(uuid.uuid4()))
            assert _http_status(err3.value) == 404
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# Cross-surface: the mapping decision lands on the project trail
# ---------------------------------------------------------------------------


class TestMapAuditTrail:
    async def test_map_row_visible_on_project_audit_trail(
        self, migrated_db: str,
    ) -> None:
        from backend.app.api.review import get_project_audit

        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            await _build_draft(session, user, project, run)
            target = (await _unmapped_m2_rows(session, run))[0]
            items = (await session.execute(
                select(CatalogueItem).where(CatalogueItem.code == "2.1.2")
            )).scalars().all()
            out = await _map(session, user, str(target.id), str(items[0].id),
                             reason="mapping shows on the trail")
            trail = await get_project_audit(
                project.id, subject_type=None, actor=None, since=None,
                limit=100, before=None, user=user, session=session)
            assert any(i["id"] == out["audit_id"] for i in trail["items"])
            row = next(i for i in trail["items"]
                       if i["id"] == out["audit_id"])
            assert row["action"] == "map_catalogue"
            assert row["project_id"] == str(project.id)
        finally:
            await _close(engine, session)
