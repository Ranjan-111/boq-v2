"""GET /runs/{id}/geometry — the viewer's base layer (manual-testing pass).

The zero-measurement viewer complaint: "the user should still SEE the
drawing". Elements + geometries are run-scoped and persist independent of
measurements — this endpoint exposes them so the frontend can draw the base
drawing. Pins:

  * a completed run returns every classified element with its geometry,
  * ownership: a stranger gets 404, not the geometry,
  * malformed run ids are honest 404s (never asyncpg 500s),
  * a run that never persisted elements (e.g. failed before the engine ran)
    returns an EMPTY list, not an error — the viewer then honestly says
    there is nothing safe to render.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.runs import get_run_geometry
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    DrawingFile,
    DrawingSheet,
    MeasurementRun,
    Project,
    ScaleCalibrationModel,
    User,
)
from backend.app.storage.base import MemoryStorage

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


async def _setup(migrated_db: str) -> tuple[AsyncEngine, AsyncSession, User,
                                            Project, DrawingFile, DrawingSheet,
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
                                status="confirmed",
                                method="user_two_point",
                                units_per_drawing_unit=Decimal("1.0"),
                                confirmed_by=user.id)
    session.add(cal)
    await session.flush()
    return engine, session, user, project, drawing, sheet, storage


async def _close(engine: AsyncEngine, session: AsyncSession) -> None:
    await session.rollback()
    await session.close()
    await engine.dispose()


async def _completed_run(session: AsyncSession, project: Project,
                         drawing: DrawingFile, sheet: DrawingSheet,
                         storage: MemoryStorage) -> MeasurementRun:
    from backend.app.services import run_service

    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                         status="queued",
                         params={"drawing_file_id": str(drawing.id),
                                 "sheet_id": str(sheet.id),
                                 "max_wall_thickness": 250.0})
    session.add(run)
    await session.flush()
    result = await run_service.execute_run(session, run_id=run.id,
                                           storage=storage,
                                           max_wall_thickness=250)
    assert result["ok"] is True
    return run


class TestRunGeometry:
    async def test_completed_run_returns_classified_elements(
        self, migrated_db: str
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _completed_run(session, project, drawing, sheet, storage)
            out = await get_run_geometry(str(run.id), user=user, session=session)
            assert out["run_id"] == str(run.id)
            assert out["count"] == len(out["elements"])
            assert out["count"] > 0, "wall_plan must yield classified elements"
            el = out["elements"][0]
            assert set(el) == {
                "element_id", "element_type", "type_source", "label", "geometry"}
            assert el["element_type"] == "wall"
            assert el["type_source"] == "geometry_deterministic"
            geo = el["geometry"]
            assert set(geo) == {"geom_type", "coordinates", "layer"}
            assert geo["geom_type"] in ("line", "polyline", "polygon")
            assert geo["coordinates"], "coordinates must be non-empty points"
            assert all(isinstance(p, list) for p in geo["coordinates"])
        finally:
            await _close(engine, session)

    async def test_run_with_no_elements_returns_empty_not_error(
        self, migrated_db: str
    ) -> None:
        """A run that failed before the engine persisted anything (e.g.
        scale never confirmed) still owns its geometry endpoint: an EMPTY
        list is the honest answer, the viewer states it plainly."""
        engine, session, user, project, drawing, sheet, _storage = (
            await _setup(migrated_db))
        try:
            # Confirmed calibration above but a QUEUED (never-executed) run —
            # no elements persisted yet.
            run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                                  status="queued",
                                  params={"drawing_file_id": str(drawing.id),
                                          "sheet_id": str(sheet.id)})
            session.add(run)
            await session.flush()
            out = await get_run_geometry(str(run.id), user=user, session=session)
            assert out["count"] == 0
            assert out["elements"] == []
        finally:
            await _close(engine, session)

    async def test_stranger_gets_404_not_geometry(
        self, migrated_db: str
    ) -> None:
        engine, session, _user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _completed_run(session, project, drawing, sheet, storage)
            stranger = User(id=str(uuid.uuid4()),
                           email=f"{uuid.uuid4().hex}@s.io",
                           password_hash=uuid.uuid4().hex, display_name="s",
                           role="owner")
            session.add(stranger)
            await session.flush()
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as raised:
                await get_run_geometry(str(run.id), user=stranger,
                                       session=session)
            assert raised.value.status_code == 404
        finally:
            await _close(engine, session)

    async def test_malformed_run_id_is_404_not_500(
        self, migrated_db: str
    ) -> None:
        engine, session, user, *_rest = await _setup(migrated_db)
        try:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as raised:
                await get_run_geometry("not-a-uuid", user=user,
                                       session=session)
            assert raised.value.status_code == 404
        finally:
            await _close(engine, session)
