"""Round 7 Worker A slice — xlsx/pdf export dispatch through execute_export
(T101/T102 backend wiring).

Pinned behavior (the test_boq_journey idiom — real services, live scratch DB):
  * an APPROVED BOQ (blockers resolved the journey way) exports xlsx and pdf
    through the SAME gate path: artifact succeeded, sha256 set, manifest
    carries the real format, storage key ends with the right extension,
  * the produced bytes are loadable/valid (xlsx loads via openpyxl with the
    header + grand total; pdf starts with %PDF),
  * re-exporting the EXPORTED version in another format is allowed and
    byte-stable (the manifest sha256 is meaningful),
  * refusal is format-INDEPENDENT: a not-approved BOQ refuses xlsx exactly
    like csv (no format can bypass the gates),
  * the export is audited and flips APPROVED -> EXPORTED for each format.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    ExceptionModel,
    ExportArtifact,
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


# ---------------------------------------------------------------------------
# Setup helpers (the test_boq_journey idiom, copied not imported)
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


async def _seed_catalogue(session: AsyncSession) -> None:
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


async def _approved_boq(session: AsyncSession, user: User, project: Project,
                        run: MeasurementRun,
                        storage: MemoryStorage) -> BoqModel:
    """A BOQ walked to APPROVED the journey way (blockers resolved first)."""
    await _seed_catalogue(session)
    built = await boq_service.build_boq_from_run(
        session, project_id=project.id, from_run_id=str(run.id),
        actor=user.id)
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == built["boq_id"])
    )).scalar_one()
    await boq_service.submit_boq(session, project_id=project.id,
                                 boq_id=str(boq.id), actor=user.id)
    await boq_service.review_boq(session, project_id=project.id,
                                 boq_id=str(boq.id), actor=user.id)
    blockers = (await session.execute(
        select(ExceptionModel).where(
            ExceptionModel.run_id == run.id,
            ExceptionModel.resolved_at.is_(None))
    )).scalars().all()
    for b in blockers:
        b.resolved_at = datetime.now(UTC)
    await session.flush()
    approved = await boq_service.approve_boq(
        session, project_id=project.id, boq_id=str(boq.id), actor=user.id)
    assert approved["status"] == "approved"
    return boq


async def _refetch(session: AsyncSession, model: Any, row_id: str) -> Any:
    """Re-select a row the service mutated (the asyncpg identity-map trap)."""
    return (await session.execute(
        select(model).where(model.id == row_id)
    )).scalar_one()


class TestXlsxExport:
    async def test_approved_boq_exports_deterministic_xlsx(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            artifact = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                      format="xlsx", status="pending",
                                      storage_key="", sha256="",
                                      created_by=user.id)
            session.add(artifact)
            await session.flush()
            out = await boq_service.execute_export(
                session, export_id=str(artifact.id), storage=storage,
                actor=user.id)
            assert out["ok"] is True
            fresh = await _refetch(session, ExportArtifact, str(artifact.id))
            assert fresh.status == "succeeded"
            assert fresh.sha256 == out["sha256"]
            assert fresh.sha256, "sha256 set on success"
            assert fresh.storage_key.endswith(f"/{artifact.id}.xlsx")
            manifest: dict[str, Any] = fresh.manifest
            assert manifest["format"] == "xlsx"
            assert manifest["row_count"] == 2  # m + count groups map
            assert manifest["boq_id"] == str(boq.id)
            provenance = manifest["provenance"]
            sidecar = storage.get(provenance["storage_key"])
            assert hashlib.sha256(sidecar).hexdigest() == provenance["sha256"]
            sidecar_doc = json.loads(sidecar)
            assert sidecar_doc["schema_version"] == "boq-provenance-v1"
            assert sidecar_doc["boq"]["source_run_id"] == str(run.id)
            assert len(sidecar_doc["rows"]) == manifest["row_count"]
            assert sidecar_doc["measurements"]
            assert sidecar_doc["measurements"][0]["source_handles"]
            assert sidecar_doc["measurements"][0]["evidence"]
            data = storage.get(fresh.storage_key)
            # The artifact is a real workbook: header + items + grand total.
            # (openpyxl ships no stubs — the ezdxf per-line-ignore idiom.)
            import openpyxl  # type: ignore[import-untyped]

            wb = openpyxl.load_workbook(io.BytesIO(data))
            ws = wb.active
            assert ws["A2"].value == "sr_no"
            assert ws.cell(row=ws.max_row, column=2).value == "GRAND TOTAL"
            # The rows are the BOQ's priced items (m + count sections).
            item_rows = (await session.execute(
                select(BoqItem).join(BoqSection,
                                    BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == boq.id)
            )).scalars().all()
            assert ws.max_row == 1 + 1 + len(item_rows) + 1  # title+header+items+total
            # Byte-determinism through the real service path: a second xlsx
            # export of the same EXPORTED version is byte-identical.
            boq_after = await _refetch(session, BoqModel, str(boq.id))
            assert boq_after.status == BoqStatus.EXPORTED.value
            artifact2 = ExportArtifact(id=str(uuid.uuid4()),
                                       boq_id=boq.id, format="xlsx",
                                       status="pending", storage_key="",
                                       sha256="", created_by=user.id)
            session.add(artifact2)
            await session.flush()
            out2 = await boq_service.execute_export(
                session, export_id=str(artifact2.id), storage=storage,
                actor=user.id)
            assert out2["ok"] is True
            assert out2["sha256"] == out["sha256"], "byte-deterministic"
            # The export is audited.
            audit = (await session.execute(
                select(AuditEntry).where(
                    AuditEntry.subject_id == artifact.id,
                    AuditEntry.action == "export")
            )).scalars().all()
            assert audit, "xlsx export must be audited"
        finally:
            await _close(engine, session)


class TestPdfExport:
    async def test_approved_boq_exports_deterministic_pdf(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            artifact = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                      format="pdf", status="pending",
                                      storage_key="", sha256="",
                                      created_by=user.id)
            session.add(artifact)
            await session.flush()
            out = await boq_service.execute_export(
                session, export_id=str(artifact.id), storage=storage,
                actor=user.id)
            assert out["ok"] is True
            fresh = await _refetch(session, ExportArtifact, str(artifact.id))
            assert fresh.status == "succeeded"
            assert fresh.sha256 == out["sha256"]
            assert fresh.storage_key.endswith(f"/{artifact.id}.pdf")
            manifest: dict[str, Any] = fresh.manifest
            assert manifest["format"] == "pdf"
            data = storage.get(fresh.storage_key)
            assert data.startswith(b"%PDF-") and len(data) > 0
            # Byte-determinism: re-export of the EXPORTED version is stable.
            artifact2 = ExportArtifact(id=str(uuid.uuid4()),
                                       boq_id=boq.id, format="pdf",
                                       status="pending", storage_key="",
                                       sha256="", created_by=user.id)
            session.add(artifact2)
            await session.flush()
            out2 = await boq_service.execute_export(
                session, export_id=str(artifact2.id), storage=storage,
                actor=user.id)
            assert out2["ok"] is True
            assert out2["sha256"] == out["sha256"], "byte-deterministic"
        finally:
            await _close(engine, session)


class TestGateFormatIndependence:
    """No format can bypass the export gates — refusal is not csv-specific."""

    async def test_not_approved_refuses_every_format(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            # Build but LEAVE in DRAFT: no submit/review/approve.
            await _seed_catalogue(session)
            built = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=str(run.id),
                actor=user.id)
            for fmt in ("csv", "xlsx", "pdf"):
                artifact = ExportArtifact(id=str(uuid.uuid4()),
                                          boq_id=built["boq_id"],
                                          format=fmt, status="pending",
                                          storage_key="", sha256="",
                                          created_by=user.id)
                session.add(artifact)
                await session.flush()
                out = await boq_service.execute_export(
                    session, export_id=str(artifact.id), storage=storage,
                    actor=user.id)
                assert out["ok"] is False, fmt
                assert out["status"] == "failed"
                assert "not APPROVED" in out["error"]
                fresh = await _refetch(session, ExportArtifact,
                                       str(artifact.id))
                assert fresh.status == "failed"
                assert fresh.sha256 == ""
                assert not fresh.storage_key, "nothing stored on refusal"
        finally:
            await _close(engine, session)

    async def test_unknown_format_fails_honestly(self, migrated_db: str,
                                                 ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            # A format outside the API's pydantic pattern, seeded directly
            # (the honest DB-level answer if a row ever held one).
            artifact = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                      format="json_sidecar", status="pending",
                                      storage_key="", sha256="",
                                      created_by=user.id)
            session.add(artifact)
            await session.flush()
            out = await boq_service.execute_export(
                session, export_id=str(artifact.id), storage=storage,
                actor=user.id)
            assert out["ok"] is False
            assert out["status"] == "failed"
            assert "unsupported export format" in out["error"]
        finally:
            await _close(engine, session)


class TestExportCreateBoundary:
    """The pydantic gate: only csv|xlsx|pdf may create an export artifact."""

    def test_format_pattern_is_csv_xlsx_pdf(self) -> None:
        from pydantic import ValidationError

        from backend.app.api.boqs import ExportCreate

        for fmt in ("csv", "xlsx", "pdf"):
            assert ExportCreate(format=fmt).format == fmt
        for bad in ("docx", "CSV", "", "json_sidecar", "csv\nxlsx"):
            with pytest.raises(ValidationError):
                ExportCreate(format=bad)
