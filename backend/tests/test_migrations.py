"""Migration tests — Alembic is the only schema authority.

Requires a reachable Postgres. Uses a scratch database (boq_test_mig) that it
creates and drops, so it never touches dev data.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users", "projects", "storeys", "drawing_files", "drawing_sheets",
    "scale_calibrations", "measurement_runs", "elements", "geometries",
    "measurements", "exceptions", "evidence_links", "catalogue_items",
    "rates", "boqs", "boq_sections", "boq_items", "export_artifacts",
    "audit_log", "ai_suggestions", "jobs",
}

BASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://boq:boq@localhost:5432/boq"
).rsplit("/", 1)[0]
TEST_DB = os.environ.get("MIGRATION_TEST_DB", "boq_test_mig")


async def _admin_engine():
    engine = create_async_engine(BASE_URL, isolation_level="AUTOCOMMIT")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrated_db():
    """Create scratch DB, run upgrade head via subprocess, yield, drop."""
    import subprocess
    import sys

    async def setup() -> str | None:
        engine = create_async_engine(BASE_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
            return None
        except Exception as exc:
            return str(exc)
        finally:
            await engine.dispose()

    err = asyncio.run(setup())
    if err is not None:
        pytest.skip(f"Postgres not reachable: {err}")

    env = dict(os.environ)
    env["DATABASE_URL"] = f"{BASE_URL}/{TEST_DB}"
    up = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"

    yield f"{BASE_URL}/{TEST_DB}"

    async def teardown() -> None:
        engine = create_async_engine(BASE_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
        finally:
            await engine.dispose()

    asyncio.run(teardown())


async def test_all_domain_tables_exist(migrated_db):
    engine = create_async_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name != 'alembic_version'"
                )
            )
            tables = {r[0] for r in rows}
    finally:
        await engine.dispose()
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"


async def test_ai_separation_tables_exist(migrated_db):
    """Domain invariant: AI suggestions live in their own table, not in measurements."""
    engine = create_async_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name='measurements'")
            )
            cols = {r[0] for r in rows}
    finally:
        await engine.dispose()
    assert "payload" not in cols, "measurements must not carry AI payload columns"
    assert "ai_confidence" not in cols, "measurements must not carry AI confidence"


async def test_downgrade_base_then_upgrade(migrated_db):
    """Idempotent roundtrip: downgrade all, upgrade back."""
    import subprocess
    import sys

    env = dict(os.environ)
    env["DATABASE_URL"] = migrated_db
    down = subprocess.run(  # noqa: ASYNC221 - test runner subprocess is fine
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "downgrade", "base"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    up = subprocess.run(  # noqa: ASYNC221 - test runner subprocess is fine
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert up.returncode == 0, f"re-upgrade failed:\n{up.stdout}\n{up.stderr}"
