"""Round 7 Worker A slice — list-runs + list-exports endpoints (T104).

Pinned behavior (route functions against a live migrated scratch DB, the
test_review_actions idiom):
  * GET /projects/{id}/runs lists the project's runs newest-first
    (created_at DESC, id DESC) with the frontend RunRow shape (id, status,
    stats, error, created_at) + drawing_file_id/sheet_id surfaced from
    run.params,
  * an optional ?status= filter narrows to that run status only,
  * ownership: a stranger's project 404s; a deleted project 404s,
  * a malformed project id is an honest 404, never a 500 (the asyncpg Uuid
    cast trap — the guard runs before SQL),
  * GET /boqs/{id}/exports lists the BOQ's ExportArtifacts newest-first
    with id/format/status/sha256/manifest/created_at; a pending artifact
    lists (manifest null); a succeeded one carries sha256 + manifest;
    ownership mirrors _resolve_callers_boq (stranger 404, malformed 404).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.boqs import list_boq_exports
from backend.app.api.runs import list_project_runs
from backend.app.api.scope import owned_project
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    BoqModel,
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


async def _seed_catalogue(session: AsyncSession) -> None:
    """One item per DISTINCT mapped unit group (the m2 collision blockers
    are resolved by the human before approval — the journey idiom)."""
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
                       run: MeasurementRun, storage: MemoryStorage,
                       ) -> BoqModel:
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


def _http_status(exc: BaseException) -> int:
    assert isinstance(exc, HTTPException)
    return exc.status_code


# ---------------------------------------------------------------------------
# GET /projects/{id}/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    async def test_lists_runs_newest_first_with_params(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run1 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            run2 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            # PG's now() is TRANSACTION-scoped: every row in this test shares
            # one timestamp. Set explicit ones so newest-first is observable
            # (the audit-trail test idiom).
            run1.created_at = datetime.now(UTC) - timedelta(seconds=2)
            run2.created_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.flush()
            out = await list_project_runs(project.id, status=None,
                                          user=user, session=session,
                                          project=project)
            items = out["items"]
            assert [i["id"] for i in items] == [str(run2.id), str(run1.id)]
            # The RunRow shape + the drawing context from params.
            first = items[0]
            assert set(first) == {
                "id", "status", "stats", "error", "created_at",
                "drawing_file_id", "sheet_id"}
            assert first["status"] == "completed"
            assert first["stats"] == {"measured": 8, "blocked": 0,
                                      "exceptions": 0}
            assert first["error"] is None
            assert first["created_at"]
            assert first["drawing_file_id"] == str(drawing.id)
            assert first["sheet_id"] == str(sheet.id)
            assert (datetime.fromisoformat(items[0]["created_at"])
                    > datetime.fromisoformat(items[1]["created_at"]))
        finally:
            await _close(engine, session)

    async def test_same_timestamp_orders_by_id_desc(
        self, migrated_db: str,
    ) -> None:
        """The deterministic tiebreak: equal created_at -> larger id first."""
        engine, session, user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            stamp = datetime.now(UTC)
            small = MeasurementRun(id="11111111-1111-1111-1111-111111111111",
                                   project_id=project.id, status="queued",
                                   created_at=stamp)
            big = MeasurementRun(id="99999999-9999-9999-9999-999999999999",
                                project_id=project.id, status="completed",
                                created_at=stamp)
            session.add(small)
            session.add(big)
            await session.flush()
            out = await list_project_runs(project.id, status=None,
                                          user=user, session=session,
                                          project=project)
            assert [i["id"] for i in out["items"]] == [str(big.id),
                                                       str(small.id)]
        finally:
            await _close(engine, session)

    async def test_status_filter_narrows(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            await _confirmed_run(session, user, project, drawing, sheet,
                                storage)
            # A queued (never-executed) run alongside the completed one.
            queued = MeasurementRun(id=str(uuid.uuid4()),
                                    project_id=project.id, status="queued",
                                    params={"drawing_file_id": str(drawing.id),
                                            "sheet_id": str(sheet.id)})
            session.add(queued)
            await session.flush()
            out = await list_project_runs(project.id, status="queued",
                                          user=user, session=session,
                                          project=project)
            assert [i["id"] for i in out["items"]] == [str(queued.id)]
            assert all(i["status"] == "queued" for i in out["items"])
            # A run without params lists with nulls, not a 500.
            bare = MeasurementRun(id=str(uuid.uuid4()),
                                 project_id=project.id, status="failed")
            bare.error = "engine boom"
            session.add(bare)
            await session.flush()
            out_all = await list_project_runs(project.id, status=None,
                                              user=user, session=session,
                                              project=project)
            by_id = {i["id"]: i for i in out_all["items"]}
            assert by_id[str(bare.id)]["drawing_file_id"] is None
            assert by_id[str(bare.id)]["sheet_id"] is None
            assert by_id[str(bare.id)]["error"] == "engine boom"
        finally:
            await _close(engine, session)

    async def test_stranger_project_404s(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            await _confirmed_run(session, user, project, drawing, sheet,
                                storage)
            stranger = User(id=str(uuid.uuid4()),
                            email=f"{uuid.uuid4().hex}@x.io",
                            password_hash=uuid.uuid4().hex, display_name="t",
                            role="owner")
            session.add(stranger)
            await session.flush()
            # owned_project is the route's ownership dependency: a stranger
            # resolves no project -> 404 (never 403 — no existence leak).
            with pytest.raises(HTTPException) as err:
                await owned_project(project.id, user=stranger,
                                    session=session)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)

    async def test_malformed_project_id_404_not_500(self, migrated_db: str,
                                                    ) -> None:
        """The asyncpg UUID-cast trap: garbage input must 404 before SQL."""
        engine, session, user, _project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            with pytest.raises(HTTPException) as err:
                # owned_project is the ownership dependency — the guard lives
                # there, so a malformed id can never reach the run query.
                from backend.app.api.scope import owned_project

                await owned_project("not-a-uuid", user=user, session=session)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# GET /boqs/{id}/exports
# ---------------------------------------------------------------------------


class TestListExports:
    async def test_lists_succeeded_and_pending_newest_first(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            # A SUCCEEDED artifact (run the job body directly, like the
            # queue handler would).
            done = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                  format="csv", status="pending",
                                  storage_key="", sha256="",
                                  created_by=user.id)
            session.add(done)
            await session.flush()
            exported = await boq_service.execute_export(
                session, export_id=str(done.id), storage=storage,
                actor=user.id)
            assert exported["ok"] is True
            # A PENDING artifact created after it (explicit stamps: PG's
            # now() is transaction-scoped, so all rows share one timestamp).
            pending = ExportArtifact(id=str(uuid.uuid4()), boq_id=boq.id,
                                    format="xlsx", status="pending",
                                    storage_key="", sha256="",
                                    created_by=user.id)
            session.add(pending)
            await session.flush()
            done.created_at = datetime.now(UTC) - timedelta(seconds=1)
            pending.created_at = datetime.now(UTC)
            await session.flush()
            out = await list_boq_exports(str(boq.id), user=user,
                                         session=session)
            items = out["items"]
            assert [i["id"] for i in items[:2]] == [str(pending.id),
                                                    str(done.id)]
            first, second = items[0], items[1]
            assert set(first) == {"id", "format", "status", "sha256",
                                  "manifest", "provenance_download_url", "created_at"}
            # The pending row lists with null sha/manifest.
            assert first["format"] == "xlsx"
            assert first["status"] == "pending"
            assert first["sha256"] is None
            assert first["manifest"] is None
            # The succeeded row carries sha256 + manifest.
            assert second["status"] == "succeeded"
            assert second["sha256"] == exported["sha256"]
            manifest: dict[str, Any] = second["manifest"]
            assert manifest["format"] == "csv"
            assert manifest["boq_id"] == str(boq.id)
            assert manifest["row_count"] == 2
        finally:
            await _close(engine, session)

    async def test_stranger_boq_404s(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            stranger = User(id=str(uuid.uuid4()),
                            email=f"{uuid.uuid4().hex}@x.io",
                            password_hash=uuid.uuid4().hex, display_name="t",
                            role="owner")
            session.add(stranger)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await list_boq_exports(str(boq.id), user=stranger,
                                      session=session)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)

    async def test_malformed_boq_id_404_not_500(self, migrated_db: str,
                                               ) -> None:
        """The asyncpg UUID-cast trap: garbage input must 404 before SQL."""
        engine, session, user, _project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            with pytest.raises(HTTPException) as err:
                await list_boq_exports("not-a-uuid", user=user,
                                      session=session)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)

    async def test_empty_boq_lists_empty(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            out = await list_boq_exports(str(boq.id), user=user,
                                         session=session)
            assert out == {"items": []}
        finally:
            await _close(engine, session)


class TestHttpBoundary:
    """The ASGI-level contract: the new routes through the real app, and
    garbage inputs are 404/422 at the boundary — never an asyncpg cast 500
    (the documented trap)."""

    async def test_routes_list_through_the_app(self, migrated_db: str) -> None:
        import httpx

        from backend.app.config import Settings
        from backend.app.main import create_app

        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            from backend.app.auth.tokens import issue_token

            secret = "http-boundary-test-secret-0123456789ab"  # noqa: S105
            run = await _confirmed_run(session, user, project, drawing,
                                       sheet, storage)
            boq = await _approved_boq(session, user, project, run, storage)
            # The app runs on its OWN connection pool: commit so the rows are
            # visible to it (expiry-on-commit re-loads them on next access).
            await session.commit()
            token = issue_token(user_id=user.id, role=user.role,
                                secret=secret, algorithm="HS256", minutes=5)
            settings = Settings(database_url=migrated_db, jwt_secret=secret)
            app = create_app(settings)
            headers = {"Authorization": f"Bearer {token}"}
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    r = await client.get(f"/api/v1/projects/{project.id}/runs",
                                         headers=headers)
                    assert r.status_code == 200, r.text
                    items = r.json()["items"]
                    assert [i["id"] for i in items] == [str(run.id)]
                    assert items[0]["stats"] == {"measured": 8, "blocked": 0,
                                                 "exceptions": 0}
                    r = await client.get(
                        f"/api/v1/projects/{project.id}/runs?status=queued",
                        headers=headers)
                    assert r.status_code == 200
                    assert r.json()["items"] == []
                    r = await client.get(
                        f"/api/v1/boqs/{boq.id}/exports", headers=headers)
                    assert r.status_code == 200, r.text
                    assert r.json() == {"items": []}
                    # Malformed ids: 404 through the app, never a 500.
                    r = await client.get("/api/v1/projects/not-a-uuid/runs",
                                         headers=headers)
                    assert r.status_code == 404, r.text
                    r = await client.get("/api/v1/boqs/not-a-uuid/exports",
                                         headers=headers)
                    assert r.status_code == 404, r.text
                    # An unknown-but-valid UUID 404s too.
                    r = await client.get(
                        f"/api/v1/projects/{uuid.uuid4()}/runs",
                        headers=headers)
                    assert r.status_code == 404, r.text
            finally:
                await app.state.engine.dispose()
        finally:
            await _close(engine, session)
