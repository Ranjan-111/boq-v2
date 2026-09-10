"""Run service + BOQ service journey — the Round 4 trust boundary end-to-end.

These tests drive the Lead-owned services DIRECTLY (rows set up manually,
fixture bytes in MemoryStorage) so they don't depend on the upload router
(Worker 1's ownership). The full API journey is wired in integration.

Gates proven here (docs/domain-model.md Round 3 semantics, server-side):
  1. a run without a CONFIRMED calibration completes WITH a BLOCKING
     scale_unconfirmed exception and ZERO measurements,
  2. a confirmed-scale run persists measurements with durable identity +
     evidence + elements, stats via the run state machine,
  3. BOQ build refuses incomplete runs; assembles only evidenced MEASURED,
  4. approve refuses until REVIEWED; refuses with unresolved blockers,
  5. export builds the trusted approval scope server-side; ungated BOQ
     export (not approved) fails; approved export produces byte-deterministic
     artifact + manifest and flips APPROVED -> EXPORTED; re-export is allowed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    BoqModel,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    ExceptionModel,
    ExportArtifact,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import boq_service, run_service
from backend.app.storage.base import MemoryStorage
from core.domain.enums import BoqStatus

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


async def _setup_project_with_drawing(
    migrated_db: str,
) -> tuple[Any, AsyncSession, User, Project, DrawingFile, DrawingSheet, MemoryStorage]:
    """User + project + parsed drawing + modelspace sheet + PROPOSED calibration."""
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


async def _confirm_scale(
    session: AsyncSession, sheet: DrawingSheet, user: User,
) -> None:
    """Human-gated confirmation by a REAL persisted user (FK: confirmed_by)."""
    cal = (await session.execute(
        select(ScaleCalibrationModel)
        .where(ScaleCalibrationModel.sheet_id == sheet.id)
    )).scalar_one()
    cal.status = "confirmed"
    cal.units_per_drawing_unit = Decimal("1.0")
    cal.method = "user_two_point"
    cal.confirmed_by = user.id
    await session.flush()


async def _seed_catalogue(session: AsyncSession) -> CatalogueItem:
    item = CatalogueItem(id=str(uuid.uuid4()), region_code="IN", code="2.1.1",
                         description="Brick wall 230mm thick", unit="m",
                         category_path="walls/brick", source="manual")
    session.add(item)
    await session.flush()
    session.add(RateModel(id=str(uuid.uuid4()), catalogue_item_id=item.id,
                          scope="default", currency="INR", amount_minor=85_000))
    await session.flush()
    return item


async def _make_run(
    session: AsyncSession, project: Project, drawing: DrawingFile,
    sheet: DrawingSheet, thickness: float = 250.0,
) -> MeasurementRun:
    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                         status="queued",
                         params={"drawing_file_id": str(drawing.id),
                                 "sheet_id": str(sheet.id),
                                 "max_wall_thickness": thickness})
    session.add(run)
    await session.flush()
    return run


async def _refetch_run(session: AsyncSession, run: MeasurementRun) -> MeasurementRun:
    """Re-select the run row: the service's internal re-select mutates a
    different identity-map instance (str vs UUID key — the documented asyncpg
    dual-representation trap), so assertions must read the persisted state."""
    return (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == run.id)
    )).scalar_one()


async def _refetch(session: AsyncSession, model: Any, row_id: str) -> Any:
    """Re-select a row the service mutated via its own re-select (the asyncpg
    str-vs-UUID identity-map trap: the caller's pre-service object goes stale)."""
    return (await session.execute(
        select(model).where(model.id == row_id)
    )).scalar_one()


class TestRunService:
    async def test_unconfirmed_scale_blocks_with_exception(self, migrated_db: str) -> None:
        engine, session, _user, project, drawing, sheet, storage = (
            await _setup_project_with_drawing(migrated_db))
        try:
            run = await _make_run(session, project, drawing, sheet)
            result = await run_service.execute_run(
                session, run_id=run.id, storage=storage, max_wall_thickness=250)
            assert result["ok"] is False
            assert result["error"] == "scale_unconfirmed"
            persisted = await _refetch_run(session, run)
            assert persisted.status == "completed_with_exceptions"
            exc_rows = (await session.execute(
                select(ExceptionModel).where(ExceptionModel.run_id == run.id)
            )).scalars().all()
            assert len(exc_rows) == 1
            assert exc_rows[0].code == "scale_unconfirmed"
            assert exc_rows[0].severity == "blocking"
            meas = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.run_id == run.id)
            )).scalars().all()
            assert meas == [], "unconfirmed scale must produce ZERO measurements"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_confirmed_scale_persists_measured_rows_with_evidence(
        self, migrated_db: str,
    ) -> None:
        engine, session, _user, project, drawing, sheet, storage = (
            await _setup_project_with_drawing(migrated_db))
        try:
            await _confirm_scale(session, sheet, _user)
            run = await _make_run(session, project, drawing, sheet)
            result = await run_service.execute_run(
                session, run_id=run.id, storage=storage, max_wall_thickness=250)
            assert result["ok"] is True
            assert result["stats"]["measured"] == 4  # 2 walls x (length + area)
            persisted = await _refetch_run(session, run)
            assert persisted.status == "completed"
            rows = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.run_id == run.id)
            )).scalars().all()
            assert len(rows) == 4
            for r in rows:
                assert r.state == "measured"
                assert r.measurement_id  # durable identity persisted
                assert r.inputs_digest
            # identity is unique per run (the migration constraint)
            assert len({r.measurement_id for r in rows}) == 4
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestBoqServiceGates:
    async def test_full_gated_journey_to_exported_boq(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup_project_with_drawing(migrated_db))
        try:
            await _seed_catalogue(session)
            await _confirm_scale(session, sheet, user)
            run = await _make_run(session, project, drawing, sheet)
            await run_service.execute_run(session, run_id=run.id, storage=storage,
                                          max_wall_thickness=250)

            # Build: only the m-unit measurements map (the area m2 has no item).
            built = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=run.id, actor=user.id)
            boq_id = built["boq_id"]
            assert built["item_count"] == 1
            # The two m2 footprint measurements map to no catalogue item and
            # are reported as unmapped blockers — honest, never silently dropped.
            assert sorted(built["unmapped"]) == [
                "Wall 1 footprint (m2)", "Wall 2 footprint (m2)"]

            boq = (await session.execute(
                select(BoqModel).where(BoqModel.id == boq_id))).scalar_one()
            assert boq.status == BoqStatus.DRAFT.value

            # Approve before REVIEWED -> refused (the gate refuses early with
            # a human-readable message; the state machine would also refuse).
            with pytest.raises(boq_service.BoqServiceError, match="submit then review"):
                await boq_service.approve_boq(session, project_id=project.id,
                                              boq_id=boq_id, actor=user.id)
            await boq_service.submit_boq(session, project_id=project.id,
                                         boq_id=boq_id, actor=user.id)
            # IN_REVIEW -> APPROVED is illegal; REVIEWED (the reviewer's
            # completion hop, review_boq) is required.
            with pytest.raises(boq_service.BoqServiceError):
                await boq_service.approve_boq(session, project_id=project.id,
                                              boq_id=boq_id, actor=user.id)
            reviewed = await boq_service.review_boq(
                session, project_id=project.id, boq_id=boq_id, actor=user.id)
            assert reviewed["status"] == "reviewed"
            approved = await boq_service.approve_boq(
                session, project_id=project.id, boq_id=boq_id, actor=user.id)
            assert approved["status"] == "approved"
            # Export with a trusted server-built approval context.
            artifact = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                      format="csv", status="pending",
                                      storage_key="", sha256="",
                                      created_by=user.id)
            session.add(artifact)
            await session.flush()
            exported = await boq_service.execute_export(
                session, export_id=artifact.id, storage=storage, actor=user.id)
            assert exported["ok"] is True
            artifact = await _refetch(session, ExportArtifact, artifact.id)
            boq = await _refetch(session, BoqModel, boq_id)
            assert artifact.status == "succeeded"
            assert artifact.sha256 == exported["sha256"]
            assert boq.status == BoqStatus.EXPORTED.value
            csv_text = storage.get(artifact.storage_key).decode("utf-8")
            assert csv_text.startswith("sr_no,code,description")
            assert "9137.50" not in csv_text or "850.00" in csv_text  # rate present
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.action == "export")
            )).scalars().all()
            assert audit, "export must be audited"

            # Re-export of the same EXPORTED version is allowed (byte-identical).
            artifact2 = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                       format="csv", status="pending",
                                       storage_key="", sha256="",
                                       created_by=user.id)
            session.add(artifact2)
            await session.flush()
            exported2 = await boq_service.execute_export(
                session, export_id=artifact2.id, storage=storage, actor=user.id)
            assert exported2["ok"] is True
            assert exported2["sha256"] == exported["sha256"], "byte-deterministic"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_export_refused_when_not_approved(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup_project_with_drawing(migrated_db))
        try:
            await _seed_catalogue(session)
            await _confirm_scale(session, sheet, user)
            run = await _make_run(session, project, drawing, sheet)
            await run_service.execute_run(session, run_id=run.id, storage=storage,
                                          max_wall_thickness=250)
            built = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=run.id, actor=user.id)
            artifact = ExportArtifact(
                id=str(uuid.uuid4()), boq_id=built["boq_id"], format="csv",
                status="pending", storage_key="", sha256="", created_by=user.id)
            session.add(artifact)
            await session.flush()
            result = await boq_service.execute_export(
                session, export_id=artifact.id, storage=storage, actor=user.id)
            assert result["ok"] is False
            assert result["status"] == "failed"
            assert "not APPROVED" in result["error"]
            artifact = await _refetch(session, ExportArtifact, artifact.id)
            assert artifact.status == "failed"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_approve_refused_with_unresolved_blockers(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup_project_with_drawing(migrated_db))
        try:
            await _seed_catalogue(session)
            # Leave scale UNCONFIRMED -> run completes with a BLOCKING exception.
            run = await _make_run(session, project, drawing, sheet)
            await run_service.execute_run(session, run_id=run.id, storage=storage,
                                          max_wall_thickness=250)
            with pytest.raises(boq_service.BoqServiceError, match="no measured quantities"):
                await boq_service.build_boq_from_run(
                    session, project_id=project.id, from_run_id=run.id,
                    actor=user.id)
            # Now a measured run + an artificial unresolved blocker on it.
            await _confirm_scale(session, sheet, user)
            run2 = await _make_run(session, project, drawing, sheet)
            await run_service.execute_run(session, run_id=run2.id, storage=storage,
                                           max_wall_thickness=250)
            blocker = ExceptionModel(
                id=str(uuid.uuid4()), run_id=run2.id, sheet_id=sheet.id,
                code="overlap_detected", severity="blocking",
                message="adversarial probe blocker")
            session.add(blocker)
            await session.flush()
            built = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=run2.id, actor=user.id)
            boq = (await session.execute(
                select(BoqModel).where(BoqModel.id == built["boq_id"]))).scalar_one()
            await boq_service.submit_boq(session, project_id=project.id,
                                        boq_id=boq.id, actor=user.id)
            reviewed = await boq_service.review_boq(
                session, project_id=project.id, boq_id=boq.id, actor=user.id)
            assert reviewed["status"] == "reviewed"
            with pytest.raises(boq_service.BoqServiceError, match="adversarial probe blocker"):
                await boq_service.approve_boq(session, project_id=project.id,
                                              boq_id=boq.id, actor=user.id)
            # Resolve the blocker -> approval succeeds.
            blocker.resolved_at = datetime.now(UTC)
            await session.flush()
            ok = await boq_service.approve_boq(session, project_id=project.id,
                                               boq_id=boq.id, actor=user.id)
            assert ok["status"] == "approved"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestQueueFail:
    """Regression: queue.fail reused one bind param across a varchar column
    assignment and a text comparison — asyncpg rejects that with
    AmbiguousParameterError, so EVERY fail() call crashed (Worker 1 probe)."""

    async def test_fail_marks_terminal_and_retryable(self, migrated_db: str) -> None:
        from backend.app.db.models import JobRun
        from backend.app.jobs import queue

        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            job_id = await queue.submit(session, queue.JobSpec(
                kind="measurement_run", payload={"probe": True},
                idempotency_key=f"probe:{uuid.uuid4().hex}"))
            # Terminal failure: status flips, finished_at set, error recorded.
            status = await queue.fail(session, job_id, "boom", retryable=False)
            assert status == "failed"
            row = (await session.execute(
                select(JobRun).where(JobRun.id == job_id)
            )).scalar_one()
            assert row.status == "failed"
            assert row.error == "boom"
            assert row.finished_at is not None
            # Retryable failure: requeued, finished_at NOT set.
            job_id2 = await queue.submit(session, queue.JobSpec(
                kind="measurement_run", payload={"probe": True},
                idempotency_key=f"probe:{uuid.uuid4().hex}"))
            status2 = await queue.fail(session, job_id2, "retry me", retryable=True)
            assert status2 == "queued"
            row2 = (await session.execute(
                select(JobRun).where(JobRun.id == job_id2)
            )).scalar_one()
            assert row2.status == "queued"
            assert row2.finished_at is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()
