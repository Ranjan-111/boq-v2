"""Drawings API + parse service — the upload-to-sheets trust path (Round 4).

Drives the upload use-case and parse service DIRECTLY against a real migrated
scratch Postgres (same style as test_project_authorization.py):
  * uploads validate BEFORE storage (bad bytes never stored, 400/413 shape),
  * dedupe by (project, sha256) reuses the row; while a parse job is queued a
    re-upload is an honest 409 parse_already_active; after a failed parse a
    re-upload enqueues a fresh job,
  * execute_parse persists sheets + PROPOSED calibrations and never CONFIRMED,
  * re-parse of a parsed file is refused (idempotency guard),
  * garbage/missing bytes are an honest FAILED, not a silent empty success,
  * creator scoping: another user's reads are 404, never 403.
"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.drawings import (
    delete_drawing,
    do_upload,
    get_drawing,
    list_drawings,
    preview_drawing,
)
from backend.app.api.projects import ProjectCreate, create_project
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    DrawingFile,
    DrawingSheet,
    JobRun,
    Project,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import parse_service
from backend.app.services.parse_service import ParseRefused
from backend.app.storage.base import MemoryStorage

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "dxf"
RASTER_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "raster"
MAX_BYTES = 10 * 1024 * 1024


async def _make_user_and_project(
    session: AsyncSession,
) -> tuple[User, Project]:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4().hex}@example.com",
        password_hash=uuid.uuid4().hex,
        display_name="t",
        role="owner",
    )
    session.add(user)
    await session.flush()
    project = await create_project(
        ProjectCreate(name="P", currency="INR", region_code="IN"), user, session
    )
    return user, project


async def _upload(
    session: AsyncSession,
    project: Project,
    user: User,
    data: bytes,
    filename: str,
    storage: MemoryStorage,
    mime: str | None = "application/dxf",
) -> dict[str, str]:
    return await do_upload(
        session,
        project=project,
        user=user,
        filename=filename,
        data=data,
        declared_mime=mime,
        max_bytes=MAX_BYTES,
        storage=storage,
    )


class TestUploadValidation:
    async def test_valid_dxf_creates_row_job_and_stored_bytes(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                result = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                drawing_id, job_id = result["drawing_file_id"], result["job_id"]
                assert drawing_id and job_id
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == drawing_id)
                    )
                ).scalar_one()
                assert row.format == "dxf"
                assert row.parse_status == "pending"
                assert row.size_bytes == len(data)
                assert row.sha256 and len(row.sha256) == 64
                assert storage.get(row.storage_key) == data
                job = (
                    await session.execute(select(JobRun).where(JobRun.id == job_id))
                ).scalar_one()
                assert job.kind == "parse_drawing"
                assert job.payload == {"drawing_file_id": drawing_id}
                assert job.status == "queued"
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_same_bytes_while_parse_queued_is_409(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                first = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                # The first parse job is still queued: a re-upload of the same
                # bytes reuses the row and honestly reports the active parse.
                with pytest.raises(HTTPException) as exc:
                    await _upload(session, project, user, data, "renamed.dxf", storage)
                assert exc.value.status_code == 409
                assert exc.value.detail[0]["code"] == "parse_already_active"  # type: ignore[index]
                rows = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.project_id == project.id)
                    )
                ).scalars().all()
                assert len(rows) == 1
                assert str(rows[0].id) == first["drawing_file_id"]
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_same_bytes_after_failed_parse_enqueues_new_job(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                from backend.app.jobs import queue

                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                first = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                storage_key = (
                    await session.execute(
                        select(DrawingFile.storage_key).where(
                            DrawingFile.id == first["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                # The parse fails (stored bytes vanish) -> file FAILED, the
                # worker records the honest result and completes the job. A
                # re-upload must re-enqueue a NEW parse job for the SAME
                # deduped row — across MULTIPLE failed attempts (the
                # idempotency key is UNIQUE across all history, so each
                # re-enqueue needs a fresh attempt ordinal).
                current_job = first["job_id"]
                for _attempt in range(3):
                    storage._objects.clear()  # bytes lost under the key
                    bad = await parse_service.execute_parse(
                        session, drawing_file_id=first["drawing_file_id"], storage=storage
                    )
                    assert bad["ok"] is False
                    # The worker completes the job (it ran honestly; the FILE
                    # failed). queue.complete records the result.
                    await queue.complete(session, current_job, bad)
                    storage.put(storage_key, io.BytesIO(data), length=len(data))
                    reup = await _upload(
                        session, project, user, data, "wall_plan.dxf", storage
                    )
                    assert reup["drawing_file_id"] == first["drawing_file_id"]
                    assert reup["job_id"] != current_job
                    current_job = reup["job_id"]
                # After the failures, the restored bytes parse successfully.
                good = await parse_service.execute_parse(
                    session, drawing_file_id=first["drawing_file_id"], storage=storage
                )
                assert good["ok"] is True
                jobs = (
                    await session.execute(
                        select(JobRun).where(
                            JobRun.kind == "parse_drawing",
                            JobRun.payload["drawing_file_id"].as_string()
                            == first["drawing_file_id"],
                        )
                    )
                ).scalars().all()
                assert len(jobs) == 4  # 1 + 3 re-enqueues, all distinct keys
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_reupload_of_parsed_file_returns_standing_result(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                from backend.app.jobs import queue

                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                first = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                claimed = await queue.claim_next(session)
                assert claimed is not None
                await queue.complete(session, first["job_id"], {"ok": True})
                await parse_service.execute_parse(
                    session, drawing_file_id=first["drawing_file_id"], storage=storage
                )
                again = await _upload(session, project, user, data, "again.dxf", storage)
                # Already parsed: no new job is needed — the standing parse answers.
                assert again["drawing_file_id"] == first["drawing_file_id"]
                assert again["job_id"] == ""
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_disallowed_mime_rejected_before_storage(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                with pytest.raises(HTTPException) as exc:
                    await _upload(
                        session, project, user, data, "wall_plan.dxf", storage,
                        mime="application/x-msdownload",
                    )
                assert exc.value.status_code == 400
                assert exc.value.detail[0]["code"] == "bad_mime"  # type: ignore[index]
                assert len(storage._objects) == 0  # nothing stored on refusal
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_fake_exe_named_dxf_rejected(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                with pytest.raises(HTTPException) as exc:
                    await _upload(
                        session, project, user, b"MZ\x90\x00garbage" * 10, "plan.dxf", storage
                    )
                assert exc.value.status_code == 400
                assert exc.value.detail[0]["code"] == "bad_magic"  # type: ignore[index]
                assert len(storage._objects) == 0
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_oversize_rejected_413(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                with pytest.raises(HTTPException) as exc:
                    await do_upload(
                        session, project=project, user=user, filename="plan.dxf",
                        data=b"x" * 100, declared_mime=None, max_bytes=10, storage=storage,
                    )
                assert exc.value.status_code == 413
                assert exc.value.detail[0]["code"] == "too_large"  # type: ignore[index]
                await session.rollback()
        finally:
            await engine.dispose()


class TestParseExecution:
    async def test_raster_preview_is_authenticated_pixel_evidence(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (RASTER_FIXTURES / "blank.png").read_bytes()
                up = await _upload(session, project, user, data, "plan.png", storage,
                                    mime="image/png")
                response = await preview_drawing(
                    up["drawing_file_id"], user=user, session=session, storage=storage
                )
                assert response.media_type == "image/png"
                assert response.body == data
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-source-sha256"]
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_raster_parse_persists_honest_unknown_scale_sheet(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (RASTER_FIXTURES / "blank.png").read_bytes()
                up = await _upload(session, project, user, data, "plan.png", storage,
                                    mime="image/png")
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result == {
                    "ok": True, "parse_status": "parsed", "sheet_count": 1, "warnings": 2,
                }
                sheet = (
                    await session.execute(
                        select(DrawingSheet).where(
                            DrawingSheet.drawing_file_id == up["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                assert sheet.sheet_ref == "image:1"
                assert sheet.sheet_type is None
                cal = (
                    await session.execute(
                        select(ScaleCalibrationModel).where(
                            ScaleCalibrationModel.sheet_id == sheet.id
                        )
                    )
                ).scalar_one()
                assert cal.status == "unknown"
                assert cal.units_per_drawing_unit is None
                assert cal.method is None
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert any("no deterministic geometry" in warning
                           for warning in (row.parse_warnings or []))
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_execute_parse_persists_sheets_and_proposed_calibrations(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is True
                assert result["sheet_count"] >= 1
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert row.parse_status == "parsed"
                sheets = (
                    await session.execute(
                        select(DrawingSheet)
                        .where(DrawingSheet.drawing_file_id == row.id)
                        .order_by(DrawingSheet.page_number)
                    )
                ).scalars().all()
                assert len(sheets) == result["sheet_count"]
                modelspace = [s for s in sheets if s.sheet_ref == "modelspace"]
                assert modelspace, "wall_plan must parse a modelspace sheet"
                assert modelspace[0].sheet_type == "plan"
                assert modelspace[0].page_number == 0
                for sheet in sheets:
                    cal = (
                        await session.execute(
                            select(ScaleCalibrationModel).where(
                                ScaleCalibrationModel.sheet_id == sheet.id
                            )
                        )
                    ).scalar_one()
                    # THE gate: auto-detection only ever PROPOSES.
                    assert cal.status == "proposed"
                    assert cal.method == "detected_from_dxf_units"
                    assert cal.confirmed_by is None
                    assert cal.confirmed_at is None
                # wall_plan.dxf carries $INSUNITS=mm -> identity proposal 1.
                cal = (
                    await session.execute(
                        select(ScaleCalibrationModel).where(
                            ScaleCalibrationModel.sheet_id == modelspace[0].id
                        )
                    )
                ).scalar_one()
                assert cal.units_per_drawing_unit == Decimal(1)
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_unknown_units_propose_no_factor(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "no_units.dxf").read_bytes()
                up = await _upload(session, project, user, data, "no_units.dxf", storage)
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is True
                sheet = (
                    await session.execute(
                        select(DrawingSheet).where(
                            DrawingSheet.drawing_file_id == up["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                cal = (
                    await session.execute(
                        select(ScaleCalibrationModel).where(
                            ScaleCalibrationModel.sheet_id == sheet.id
                        )
                    )
                ).scalar_one()
                # No $INSUNITS: nothing proposed — the human states the factor.
                assert cal.status == "proposed"
                assert cal.units_per_drawing_unit is None
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_reparse_of_parsed_file_refused(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                ok = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert ok["ok"] is True
                with pytest.raises(ParseRefused):
                    await parse_service.execute_parse(
                        session, drawing_file_id=up["drawing_file_id"], storage=storage
                    )
                # State untouched by the refusal.
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert row.parse_status == "parsed"
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_missing_stored_bytes_fail_honestly(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                storage._objects.clear()  # bytes lost/corrupted under the key
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is False
                assert result["parse_status"] == "failed"
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert row.parse_warnings
                assert row.parse_warnings[0].startswith("parse_failed:")
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_structurally_invalid_dxf_fails_honestly(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                # SECTION+ENTITIES markers (so sniff accepts) but structurally
                # broken for ezdxf: an honest parse failure, not a guess.
                marker = b"0\nSECTION\n2\nENTITIES\n0\nGARBAGE\n"
                up = await _upload(session, project, user, marker, "plan.dxf", storage)
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is False
                assert result["parse_status"] == "failed"
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert row.parse_status == "failed"
                assert any("parse_failed" in w for w in row.parse_warnings or [])
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_non_dxf_format_fails_with_clear_reason(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                # Case 1: corrupt PDF bytes pass upload validation (magic
                # bytes) but the Round 5 parser refuses structurally — an
                # honest, machine-readable failure, never a faked success.
                pdf = b"%PDF-1.7\n%" + b"\x00\x01\x02" * 10
                up = await _upload(
                    session, project, user, pdf, "plan.pdf", storage, mime="application/pdf"
                )
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is False
                assert "PdfParseError" in result["error"]
                failed_row = (
                    await session.execute(
                        select(DrawingFile).where(
                            DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert any("parse_failed" in w for w in failed_row.parse_warnings or [])
                await session.rollback()

                # Case 2: malformed raster bytes are refused honestly by the
                # Pillow parser; no empty sheet or guessed geometry appears.
                async with make_sessionmaker(engine)() as session2:
                    user2, project2 = await _make_user_and_project(session2)
                    raster = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02" * 10
                    up2 = await _upload(
                        session2, project2, user2, raster, "plan.png", storage,
                        mime="image/png",
                    )
                    result2 = await parse_service.execute_parse(
                        session2, drawing_file_id=up2["drawing_file_id"],
                        storage=storage,
                    )
                    assert result2["ok"] is False
                    assert "RasterParseError" in result2["error"]
                    row2 = (
                        await session2.execute(
                            select(DrawingFile).where(
                                DrawingFile.id == up2["drawing_file_id"])
                        )
                    ).scalar_one()
                    assert row2.parse_status == "failed"
                    await session2.rollback()
        finally:
            await engine.dispose()

    async def test_paperspace_fixture_surfaces_parser_warnings(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "paperspace_only.dxf").read_bytes()
                up = await _upload(
                    session, project, user, data, "paperspace_only.dxf", storage
                )
                result = await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )
                assert result["ok"] is True
                row = (
                    await session.execute(
                        select(DrawingFile).where(DrawingFile.id == up["drawing_file_id"])
                    )
                ).scalar_one()
                assert result["warnings"] == len(row.parse_warnings or [])
                assert result["warnings"] >= 1
                sheets = (
                    await session.execute(
                        select(DrawingSheet).where(
                            DrawingSheet.drawing_file_id == row.id
                        )
                    )
                ).scalars().all()
                by_ref = {s.sheet_ref: s for s in sheets}
                assert "paperspace:Layout1" in by_ref
                # Non-modelspace sheets are never classified as "plan".
                assert by_ref["paperspace:Layout1"].sheet_type is None
                await session.rollback()
        finally:
            await engine.dispose()


class TestDrawingRoutes:
    async def test_list_detail_download_and_delete(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                user, project = await _make_user_and_project(session)
                other_user = User(
                    id=str(uuid.uuid4()),
                    email=f"{uuid.uuid4().hex}@example.com",
                    password_hash=uuid.uuid4().hex,
                    display_name="t",
                    role="owner",
                )
                session.add(other_user)
                await session.flush()
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                storage_key = (
                    await session.execute(
                        select(DrawingFile.storage_key).where(
                            DrawingFile.id == up["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                await parse_service.execute_parse(
                    session, drawing_file_id=up["drawing_file_id"], storage=storage
                )

                listed = await list_drawings(project, user, session)
                assert [i["parse_status"] for i in listed["items"]] == ["parsed"]
                assert listed["items"][0]["parse_warnings_count"] == 0
                assert listed["items"][0]["parse_warnings"] == []

                detail = await get_drawing(up["drawing_file_id"], user, session)
                assert detail["parse_status"] == "parsed"
                assert detail["sheets"]
                assert all(s["calibration_status"] == "proposed" for s in detail["sheets"])
                # Nested calibration object + modelspace flag — what the
                # frontend run/confirm forms bind to.
                assert all(s["calibration"]["status"] == "proposed"
                           for s in detail["sheets"])
                assert all(s["is_modelspace"] for s in detail["sheets"])

                # Creator scoping: a different user gets 404, never 403.
                with pytest.raises(HTTPException) as forbidden:
                    await get_drawing(up["drawing_file_id"], other_user, session)
                assert forbidden.value.status_code == 404

                await delete_drawing(up["drawing_file_id"], user, session, storage)
                remaining_sheets = (
                    await session.execute(
                        select(DrawingSheet).where(
                            DrawingSheet.drawing_file_id == up["drawing_file_id"]
                        )
                    )
                ).scalars().all()
                assert remaining_sheets == []
                assert not storage.exists(storage_key)
                with pytest.raises(HTTPException) as gone:
                    await get_drawing(up["drawing_file_id"], user, session)
                assert gone.value.status_code == 404
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_download_returns_signed_url(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                from backend.app.api.drawings import download_drawing

                user, project = await _make_user_and_project(session)
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up = await _upload(session, project, user, data, "wall_plan.dxf", storage)
                out = await download_drawing(up["drawing_file_id"], user, session, storage)
                assert out["url"].startswith("memory://")
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_delete_keeps_shared_blob_for_other_projects(
        self, migrated_db: str
    ) -> None:
        engine = make_async_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                from backend.app.api.projects import ProjectCreate, create_project

                user, project_a = await _make_user_and_project(session)
                project_b = await create_project(
                    ProjectCreate(name="B", currency="INR", region_code="IN"),
                    user,
                    session,
                )
                storage = MemoryStorage()
                data = (FIXTURES / "wall_plan.dxf").read_bytes()
                up_a = await _upload(session, project_a, user, data, "wall_plan.dxf", storage)
                up_b = await _upload(session, project_b, user, data, "wall_plan.dxf", storage)
                # Content-addressed key: both rows share ONE stored blob.
                key_a = (
                    await session.execute(
                        select(DrawingFile.storage_key).where(
                            DrawingFile.id == up_a["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                key_b = (
                    await session.execute(
                        select(DrawingFile.storage_key).where(
                            DrawingFile.id == up_b["drawing_file_id"]
                        )
                    )
                ).scalar_one()
                assert key_a == key_b

                # Deleting project A's drawing must NOT destroy B's bytes.
                await delete_drawing(up_a["drawing_file_id"], user, session, storage)
                assert storage.exists(key_a)
                await parse_service.execute_parse(
                    session, drawing_file_id=up_b["drawing_file_id"], storage=storage
                )

                # The last reference removed does clean up the blob.
                await delete_drawing(up_b["drawing_file_id"], user, session, storage)
                assert not storage.exists(key_b)
                await session.rollback()
        finally:
            await engine.dispose()
