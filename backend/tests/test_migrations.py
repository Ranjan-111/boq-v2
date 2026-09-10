"""Migration tests — Alembic is the only schema authority.

Requires a reachable Postgres. Uses a unique scratch database that it
creates and drops, so it never touches dev data.
"""
from __future__ import annotations

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

async def test_all_domain_tables_exist(migrated_db: str) -> None:
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


async def test_ai_separation_tables_exist(migrated_db: str) -> None:
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


async def test_downgrade_base_then_upgrade(migrated_db: str) -> None:
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
