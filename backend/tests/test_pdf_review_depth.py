"""Round 8 — the PDF review-depth journey against real PostgreSQL.

One end-to-end slice through every gate the three Round 8 backend changes
introduced (upload+parse -> PROPOSED bar-scale calibration -> human confirm
-> run with candidate emission -> audited review accept -> BOQ billing):

  1. PARSE (slice 1): parsing the scale_annotation fixture persists the
     sheet's calibration as PROPOSED with method BAR_SCALE_DETECTED and the
     EXACT factor ingestion's propose_scale_from_text computes for 1:100
     (100*25.4/72 quantized to 10 places) — never CONFIRMED. A DXF with no
     $INSUNITS gets the honest method-NULL calibration (nothing detected).
  2. RUN (slice 2): the PDF run surfaces the vector candidates as
     NEEDS_REVIEW measurements with evidence, through the same registered
     rules; stats.measured counts only MEASURED/MEASURED_ZERO (candidates
     never inflate it); run params record emit_candidates for replay.
  3. REVIEW + BOQ (slice 2 doctrine): accepting one candidate through
     review_service pivots NEEDS_REVIEW -> MEASURED audited; the BOQ build
     then bills the accepted candidate; the unaccepted sibling does NOT bill
     (BOQ bills only MEASURED/MEASURED_ZERO).

Idiom: test_boq_journey.py — real services, real scratch DB (migrated_db
fixture), per-row session.add + flush (the asyncpg batch trap).
"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    EvidenceLinkModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import boq_service, parse_service, review_service
from backend.app.services import run_service as run_service
from backend.app.storage.base import MemoryStorage

pytestmark = pytest.mark.integration

PDF_FIXTURE = (Path(__file__).resolve().parents[2]
               / "tests/fixtures/pdf/scale_annotation.pdf")
DXF_NO_UNITS = (Path(__file__).resolve().parents[2]
               / "tests/fixtures/dxf/no_units.dxf")

# What ingestion proposes for "1:100": 100 * 25.4 / 72 quantized to 10 places.
FACTOR_1_100 = Decimal("35.2777777778")


async def _make_user_and_project(
    session: AsyncSession,
) -> tuple[User, Project]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t", role="owner")
    session.add(user)
    await session.flush()
    project = Project(id=str(uuid.uuid4()), name="P", region_code="IN",
                     currency="INR", created_by=user.id)
    session.add(project)
    await session.flush()
    return user, project


async def _store_drawing(
    session: AsyncSession, *, project: Project, user: User, data: bytes,
    filename: str, fmt: str, storage: MemoryStorage,
) -> DrawingFile:
    """A stored, pending drawing row (upload transport is Lead-owned; the
    service boundary under test here is parse -> run -> review -> BOQ)."""
    key = f"uploads/{fmt}/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}"
    storage.put(key, io.BytesIO(data), length=len(data))
    drawing = DrawingFile(id=str(uuid.uuid4()), project_id=project.id,
                          filename=filename, format=fmt, size_bytes=len(data),
                          storage_key=key, sha256=uuid.uuid4().hex,
                          uploaded_by=user.id, parse_status="pending")
    session.add(drawing)
    await session.flush()
    return drawing


async def _confirm_scale(
    session: AsyncSession, sheet: DrawingSheet, user: User,
) -> None:
    """The human gate, walked the same way the sheets API does (status,
    factor, method, confirmed_by) — the only CONFIRMED writer in the product."""
    cal = (await session.execute(
        select(ScaleCalibrationModel).where(
            ScaleCalibrationModel.sheet_id == sheet.id)
    )).scalar_one()
    cal.status = "confirmed"
    cal.units_per_drawing_unit = FACTOR_1_100
    cal.method = "user_two_point"
    cal.confirmed_by = user.id
    await session.flush()
    session.add(AuditEntry(
        id=str(uuid.uuid4()), actor=user.id, action="confirm_scale",
        subject_type="sheet", subject_id=sheet.id, project_id=None,
        before={"status": "proposed"}, after={"status": "confirmed"},
    ))
    await session.flush()


async def _make_run(
    session: AsyncSession, project: Project, drawing: DrawingFile,
    sheet: DrawingSheet,
) -> MeasurementRun:
    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                          status="queued",
                          params={"drawing_file_id": str(drawing.id),
                                  "sheet_id": str(sheet.id),
                                  "max_wall_thickness": 250.0})
    session.add(run)
    await session.flush()
    return run


async def _seed_catalogue(session: AsyncSession) -> None:
    """One item per unit group the candidate run can bill: m2 areas, m lengths."""
    for code, description, unit, amount in (
        ("2.1.1", "Measured area (m2)", "m2", 4_250),
        ("2.1.2", "Measured length (m)", "m", 85_000),
    ):
        item = CatalogueItem(id=str(uuid.uuid4()), region_code="IN", code=code,
                             description=description, unit=unit,
                             category_path="measured", source="manual")
        session.add(item)
        await session.flush()
        session.add(RateModel(id=str(uuid.uuid4()),
                              catalogue_item_id=item.id, scope="default",
                              currency="INR", amount_minor=amount))
        await session.flush()


class TestPdfScaleProposal:
    """Slice 1 — the text-scale proposal lands in parse persistence."""

    async def test_pdf_parse_persists_proposed_bar_scale_calibration(
        self, migrated_db: str,
    ) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            user, project = await _make_user_and_project(session)
            storage = MemoryStorage()
            drawing = await _store_drawing(
                session, project=project, user=user,
                data=PDF_FIXTURE.read_bytes(), filename="scale_annotation.pdf",
                fmt="pdf", storage=storage)
            result = await parse_service.execute_parse(
                session, drawing_file_id=str(drawing.id), storage=storage)
            assert result["ok"] is True
            assert result["sheet_count"] == 1  # one page = one sheet
            sheet = (await session.execute(
                select(DrawingSheet).where(
                    DrawingSheet.drawing_file_id == drawing.id)
            )).scalar_one()
            assert sheet.sheet_ref == "page:0"
            cal = (await session.execute(
                select(ScaleCalibrationModel).where(
                    ScaleCalibrationModel.sheet_id == sheet.id)
            )).scalar_one()
            # THE gates: PROPOSED only, with the exact proposed factor and
            # the bar-scale method. NEVER CONFIRMED from a parse.
            assert cal.status == "proposed"
            assert cal.method == "bar_scale_detected"
            assert cal.units_per_drawing_unit == FACTOR_1_100
            assert cal.confirmed_by is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_dxf_without_units_gets_honest_null_method(
        self, migrated_db: str,
    ) -> None:
        """The honesty fix: no $INSUNITS -> nothing was detected -> method
        NULL, units NULL, status PROPOSED (the human must tell us)."""
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            user, project = await _make_user_and_project(session)
            storage = MemoryStorage()
            drawing = await _store_drawing(
                session, project=project, user=user,
                data=DXF_NO_UNITS.read_bytes(), filename="no_units.dxf",
                fmt="dxf", storage=storage)
            result = await parse_service.execute_parse(
                session, drawing_file_id=str(drawing.id), storage=storage)
            assert result["ok"] is True
            cal = (await session.execute(
                select(ScaleCalibrationModel).join(
                    DrawingSheet, ScaleCalibrationModel.sheet_id == DrawingSheet.id)
                .where(DrawingSheet.drawing_file_id == drawing.id)
            )).scalar_one()
            assert cal.status == "proposed"
            assert cal.method is None, "nothing was detected — no method claim"
            assert cal.units_per_drawing_unit is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestPdfRunCandidates:
    """Slice 2 — candidates surface NEEDS_REVIEW through the run service."""

    async def test_confirmed_pdf_run_surfaces_candidates_with_evidence(
        self, migrated_db: str,
    ) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            user, project = await _make_user_and_project(session)
            storage = MemoryStorage()
            drawing = await _store_drawing(
                session, project=project, user=user,
                data=PDF_FIXTURE.read_bytes(), filename="scale_annotation.pdf",
                fmt="pdf", storage=storage)
            await parse_service.execute_parse(
                session, drawing_file_id=str(drawing.id), storage=storage)
            sheet = (await session.execute(
                select(DrawingSheet).where(
                    DrawingSheet.drawing_file_id == drawing.id)
            )).scalar_one()
            await _confirm_scale(session, sheet, user)
            run = await _make_run(session, project, drawing, sheet)

            result = await run_service.execute_run(
                session, run_id=str(run.id), storage=storage,
                max_wall_thickness=250.0)
            assert result["ok"] is True
            # Stats honesty: candidates are NEEDS_REVIEW — measured stays 0.
            assert result["stats"]["measured"] == 0
            assert result["stats"]["exceptions"] == 0
            # The run recorded emit_candidates (replay honesty).
            run = (await session.execute(
                select(MeasurementRun).where(MeasurementRun.id == run.id)
            )).scalar_one()
            assert (run.params or {}).get("emit_candidates") is True
            assert run.engine_version == "0.5.0"

            rows = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id)
            )).scalars().all()
            # the fixture: one closed rect (area candidate) + one open
            # polyline (length candidate); the wall detectors found nothing
            assert len(rows) == 2
            assert all(r.state == "needs_review" for r in rows)
            assert all("candidate" in (r.label or "") for r in rows)
            by_rule = {r.rule_id: r for r in rows}
            assert set(by_rule) == {"polygon.area.v1", "polyline.length.v1"}
            # converted through the CONFIRMED 1:100 factor (mm base)
            assert by_rule["polygon.area.v1"].value == Decimal("35.842222")
            assert by_rule["polyline.length.v1"].value == Decimal("10.583333")
            assert by_rule["polygon.area.v1"].unit == "m2"
            assert by_rule["polyline.length.v1"].unit == "m"
            # every candidate row carries its evidence links
            for r in rows:
                ev = (await session.execute(
                    select(EvidenceLinkModel).where(
                        EvidenceLinkModel.subject_type == "measurement",
                        EvidenceLinkModel.subject_id == r.id)
                )).scalars().all()
                assert ev, "a surfaced candidate needs its evidence"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestPdfCandidateReviewAndBoq:
    """The doctrine: human accept pivots NEEDS_REVIEW -> MEASURED audited,
    and only then may the BOQ bill the candidate."""

    async def test_accept_one_candidate_bills_only_it(
        self, migrated_db: str,
    ) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            user, project = await _make_user_and_project(session)
            await _seed_catalogue(session)
            storage = MemoryStorage()
            drawing = await _store_drawing(
                session, project=project, user=user,
                data=PDF_FIXTURE.read_bytes(), filename="scale_annotation.pdf",
                fmt="pdf", storage=storage)
            await parse_service.execute_parse(
                session, drawing_file_id=str(drawing.id), storage=storage)
            sheet = (await session.execute(
                select(DrawingSheet).where(
                    DrawingSheet.drawing_file_id == drawing.id)
            )).scalar_one()
            await _confirm_scale(session, sheet, user)
            run = await _make_run(session, project, drawing, sheet)
            await run_service.execute_run(
                session, run_id=str(run.id), storage=storage,
                max_wall_thickness=250.0)

            rows = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.run_id == run.id)
                .order_by(MeasurementModel.created_at, MeasurementModel.id)
            )).scalars().all()
            assert len(rows) == 2
            area_row = next(r for r in rows
                            if r.rule_id == "polygon.area.v1")
            length_row = next(r for r in rows
                              if r.rule_id == "polyline.length.v1")

            # A BOQ before any accept: no MEASURED rows -> honest refusal.
            with pytest.raises(boq_service.BoqServiceError,
                               match="no measured quantities"):
                await boq_service.build_boq_from_run(
                    session, project_id=str(project.id),
                    from_run_id=str(run.id), actor=user.id)

            # THE human review accept: NEEDS_REVIEW -> MEASURED, audited.
            accepted = await review_service.review_measurement(
                session, measurement_ref=str(area_row.id), action="accept",
                value=None, reason="human confirmed the closed room ring",
                actor=user.id)
            assert accepted["ok"] is True
            accepted_row = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.id == area_row.id)
            )).scalar_one()
            assert accepted_row.state == "measured"
            audit = (await session.execute(
                select(AuditEntry).where(
                    AuditEntry.subject_type == "measurement",
                    AuditEntry.subject_id == accepted_row.id)
            )).scalars().all()
            assert len(audit) == 1
            assert audit[0].action == "accept_measurement"
            assert (audit[0].before or {}).get("state") == "needs_review"
            assert (audit[0].after or {}).get("state") == "measured"

            # The unaccepted sibling stays NEEDS_REVIEW.
            still_pending = (await session.execute(
                select(MeasurementModel).where(
                    MeasurementModel.id == length_row.id)
            )).scalar_one()
            assert still_pending.state == "needs_review"

            # BOQ build: the ACCEPTED candidate bills (auto-mapping by
            # (rule_id, unit) group); the pending sibling does NOT bill.
            built = await boq_service.build_boq_from_run(
                session, project_id=str(project.id),
                from_run_id=str(run.id), actor=user.id)
            assert built["item_count"] == 1
            assert built["unmapped"] == []
            items = (await session.execute(
                select(BoqItem).join(BoqModel, BoqItem.section_id.isnot(None))
                .where(BoqItem.catalogue_item_id.isnot(None))
            )).scalars().all()
            billed_ids = {mid for item in items
                          for mid in (item.measurement_ids or [])}
            assert str(accepted_row.measurement_id) in billed_ids, (
                "the accepted candidate is billed"
            )
            assert str(still_pending.measurement_id) not in billed_ids, (
                "an unaccepted candidate must never bill"
            )
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()
