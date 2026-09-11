"""Round 6 Lead slice — BOQ editing (T086), validation report (T093),
and the advisory AI analyze pass wiring.

Pinned behavior (every case through the real services + live scratch DB):
  * section/item mutations are DRAFT-only — a submitted BOQ refuses every
    edit with the re-entry path named (reject -> draft),
  * a MANUAL item's total is recomputable from (quantity, rate, markup)
    through the pricing kernel — banker's rounding, integer minors,
  * PATCHing a mapped item's quantity is refused: mapped quantities come
    from measurements; correct the measurement (audited) or recompute —
    never a direct column edit,
  * recompute pulls a measurement correction into a DRAFT BOQ and returns
    a per-item diff (before/after) — never a silent re-price,
  * the validation report lists exactly the export-gate problems (no
    items, unpriced, broken mapping, run blockers),
  * the analyze pass writes ONLY prompt_logs + ai_suggestions (stub
    provider), replaces prior suggestions on re-run, and refuses
    out-of-range provider confidences before insert.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AiSuggestion,
    BoqItem,
    BoqModel,
    BoqSection,
    DrawingFile,
    DrawingSheet,
    MeasurementModel,
    MeasurementRun,
    Project,
    PromptLogModel,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import boq_service, run_service
from backend.app.storage.base import MemoryStorage

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


async def _setup(migrated_db: str) -> tuple[Any, AsyncSession, User, Project,
                                            DrawingFile, DrawingSheet,
                                            MemoryStorage]:
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


async def _confirmed_run(session: AsyncSession, user: User, project: Project,
                         drawing: DrawingFile, sheet: DrawingSheet,
                         storage: MemoryStorage) -> MeasurementRun:
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
    await run_service.execute_run(session, run_id=run.id, storage=storage,
                                  max_wall_thickness=250)
    return run


async def _draft_boq(session: AsyncSession, user: User, project: Project,
                     run: MeasurementRun) -> BoqModel:
    from backend.tests.test_boq_journey import _seed_catalogue

    await _seed_catalogue(session)
    built = await boq_service.build_boq_from_run(
        session, project_id=project.id, from_run_id=str(run.id),
        actor=user.id)
    return (await session.execute(
        select(BoqModel).where(BoqModel.id == built["boq_id"])
    )).scalar_one()


class TestSectionEditing:
    async def test_add_patch_delete_section_draft_only(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq = await _draft_boq(session, user, project, run)
            # Add
            added = await boq_service.add_section(
                session, project_id=project.id, boq_id=str(boq.id),
                code="B", title="Manual additions", sort_order=5,
                actor=user.id)
            assert added["code"] == "B"
            # Patch
            patched = await boq_service.update_section(
                session, project_id=project.id,
                section_id=added["section_id"], code="B", title="Extras",
                sort_order=6, actor=user.id)
            assert patched["title"] == "Extras"
            # Delete
            deleted = await boq_service.delete_section(
                session, project_id=project.id,
                section_id=added["section_id"], actor=user.id)
            assert deleted == {"deleted": True, "items_removed": 0}
            # A deleted section is gone — every later mutation 404s.
            with pytest.raises(boq_service.BoqServiceError, match="not found"):
                await boq_service.delete_section(
                    session, project_id=project.id,
                    section_id=added["section_id"], actor=user.id)
            # NOT DRAFT -> every mutation refuses on a live section.
            live = await boq_service.add_section(
                session, project_id=project.id, boq_id=str(boq.id),
                code="B2", title="Still here", sort_order=7, actor=user.id)
            await boq_service.submit_boq(session, project_id=project.id,
                                         boq_id=str(boq.id), actor=user.id)
            with pytest.raises(boq_service.BoqServiceError, match="only a DRAFT"):
                await boq_service.add_section(
                    session, project_id=project.id, boq_id=str(boq.id),
                    code="C", title="late", sort_order=9, actor=user.id)
            with pytest.raises(boq_service.BoqServiceError, match="only a DRAFT"):
                await boq_service.delete_section(
                    session, project_id=project.id,
                    section_id=live["section_id"], actor=user.id)
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestManualItems:
    async def test_manual_item_priced_and_recomputable(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq = await _draft_boq(session, user, project, run)
            # 10.5 units at 12.34 (1234 minor) with 7.5% markup:
            # base = 12957; markup = 972 (banker's) -> 13929.
            created = await boq_service.add_manual_item(
                session, project_id=project.id, boq_id=str(boq.id),
                section_id=None, description="Site clearance lump",
                unit="m2", quantity=Decimal("10.5"), rate_minor=1234,
                markup_bp=750, sort_order=99, actor=user.id)
            item = (await session.execute(
                select(BoqItem).where(BoqItem.id == created["item_id"])
            )).scalar_one()
            assert item.origin == "manual"
            assert item.total_minor == 13929
            # quantity edit recomputes the total (manual provenance = human)
            updated = await boq_service.update_item(
                session, project_id=project.id, item_id=str(item.id),
                description=None, rate_minor=None, markup_bp=None,
                quantity=Decimal("20"), actor=user.id)
            # 20 x 12.34 = 24680; markup 7.5% = 1851 -> 26531.
            assert updated["total_minor"] == 26531
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_mapped_quantity_edit_refused(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            _draft_boq_session = await _draft_boq(session, user, project, run)
            mapped = (await session.execute(
                select(BoqItem).where(BoqItem.origin == "mapped")
            )).scalars().first()
            assert mapped is not None
            with pytest.raises(boq_service.BoqServiceError,
                               match="mapped quantities come from measurements"):
                await boq_service.update_item(
                    session, project_id=project.id, item_id=str(mapped.id),
                    description=None, rate_minor=None, markup_bp=None,
                    quantity=Decimal("99"), actor=user.id)
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_fresh_build_bills_corrected_value(self, migrated_db: str) -> None:
        """The correction doctrine at BUILD time: a fresh BOQ built after a
        correction bills the corrected number, never the stale engine value
        (docs/domain-model.md — BOQ recomputes from corrected values)."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq1 = await _draft_boq(session, user, project, run)
            m_item1 = (await session.execute(
                select(BoqItem).join(BoqSection, BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == boq1.id, BoqItem.unit == "m")
            )).scalars().first()
            assert m_item1 is not None and m_item1.measurement_ids
            original = Decimal(str(m_item1.quantity))
            identity: str = str(m_item1.measurement_ids[0])
            # Simulate the audited correction on one length row.
            m_row = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id,
                    MeasurementModel.measurement_id == identity)
            )).scalar_one()
            m_row.corrected_value = Decimal(str(m_row.value)) + Decimal("2")
            await session.flush()
            # A FRESH build from the same run bills the corrected sum.
            built2 = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=str(run.id),
                actor=user.id)
            boq2 = (await session.execute(
                select(BoqModel).where(BoqModel.id == built2["boq_id"])
            )).scalar_one()
            m_item2 = (await session.execute(
                select(BoqItem).join(BoqSection, BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == boq2.id, BoqItem.unit == "m")
            )).scalars().first()
            assert m_item2 is not None
            assert Decimal(str(m_item2.quantity)) == original + 2
            # The original engine value is still visible on the row.
            fresh_m = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id,
                    MeasurementModel.measurement_id == identity)
            )).scalar_one()
            assert Decimal(str(fresh_m.value)) == Decimal(str(m_row.value))
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestRecompute:
    async def test_correction_flows_into_draft_boq_with_diff(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq = await _draft_boq(session, user, project, run)
            # The m-group item is the mapped line that carries wall lengths.
            m_item = (await session.execute(
                select(BoqItem).join(BoqSection, BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == boq.id, BoqItem.unit == "m")
            )).scalars().first()
            assert m_item is not None and m_item.measurement_ids
            before_qty = Decimal(str(m_item.quantity))
            # Simulate the (Worker B) audited correction on ONE length row:
            # corrected_value replaces value at recompute.
            m_row = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id,
                    MeasurementModel.measurement_id == m_item.measurement_ids[0])
            )).scalar_one()
            m_row.corrected_value = Decimal(str(m_row.value)) + Decimal("1")
            await session.flush()
            result = await boq_service.recompute_boq(
                session, project_id=project.id, boq_id=str(boq.id),
                actor=user.id)
            assert result["changed_items"] == 1
            entry = result["diff"][0]
            assert entry["before"]["quantity"] == str(before_qty)
            assert Decimal(entry["after"]["quantity"]) == before_qty + 1
            # re-refetch: the persisted row carries the new quantity
            fresh = (await session.execute(
                select(BoqItem).where(BoqItem.id == m_item.id)
            )).scalar_one()
            assert Decimal(str(fresh.quantity)) == before_qty + 1
            # Idempotent: a second recompute with no new corrections is a no-op
            again = await boq_service.recompute_boq(
                session, project_id=project.id, boq_id=str(boq.id),
                actor=user.id)
            assert again["changed_items"] == 0
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestValidationReport:
    async def test_report_lists_export_gate_problems(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            boq = await _draft_boq(session, user, project, run)
            report = await boq_service.validation_report(
                session, project_id=project.id, boq_id=str(boq.id))
            # The R5 collision blockers on this run surface in the report.
            codes = {p["code"] for p in report["problems"]}
            assert "unmapped_measurement" in codes
            assert report["unresolved_blockers"] >= 4
            assert report["item_count"] == 2  # m + count groups map
            assert report["ready_for_approval"] is False
            # An item without a rate is listed as unpriced
            created = await boq_service.add_manual_item(
                session, project_id=project.id, boq_id=str(boq.id),
                section_id=None, description="Provisional lump",
                unit=None, quantity=Decimal("1"), rate_minor=None,
                markup_bp=0, sort_order=50, actor=user.id)
            assert created["total_minor"] == 0
            report2 = await boq_service.validation_report(
                session, project_id=project.id, boq_id=str(boq.id))
            assert any(p["code"] == "unpriced_item"
                       and "Provisional lump" in p["message"]
                       for p in report2["problems"])
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestAnalyzeWiring:
    """The advisory pass through the STUB provider — no network, no AI key."""

    async def test_analyze_writes_only_advisory_rows(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            from backend.app.services import ai_service

            result = await ai_service.execute_analyze(
                session, run_id=str(run.id))
            # wall_plan: 2 elements + 0 unresolved exceptions (engine clean)
            assert result["ok"] is True
            assert result["elements_seen"] == 2
            assert result["suggestions_written"] == 2
            assert result["prompt_logs"] == 2
            suggestions = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.run_id == run.id)
            )).scalars().all()
            assert len(suggestions) == 2
            for s in suggestions:
                assert s.suggestion_type == "element_classification"
                assert 0.0 <= float(s.confidence) <= 0.95
                # the guardrail stamp rides in the payload
                assert "rejected_fields" in s.payload
            logs = (await session.execute(
                select(PromptLogModel).order_by(PromptLogModel.id)
            )).scalars().all()
            assert len(logs) == 2
            assert logs[0].provider == "stub"
            # Re-run replaces the run's suggestions, keeps prompt history
            result2 = await ai_service.execute_analyze(
                session, run_id=str(run.id))
            assert result2["suggestions_written"] == 2
            still = (await session.execute(
                select(AiSuggestion).where(AiSuggestion.run_id == run.id)
            )).scalars().all()
            assert len(still) == 2
            logs2 = (await session.execute(
                select(PromptLogModel).order_by(PromptLogModel.id)
            )).scalars().all()
            assert len(logs2) == 4  # append-only history
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_analyze_refuses_incomplete_run(self, migrated_db: str) -> None:
        engine, session, _user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                                 status="queued", params={})
            session.add(run)
            await session.flush()
            from backend.app.services import ai_service

            with pytest.raises(ai_service.AnalyzeError, match="nothing to analyze"):
                await ai_service.execute_analyze(session, run_id=str(run.id))
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()
