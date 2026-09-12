"""T125 perf-suite fixtures — live PostgreSQL, migrated scratch DBs.

Same discipline as backend/tests/conftest.py's migrated_db (the proven
pattern, restated here because tests/perf is a separate pytest root):

  * the scratch database name is GENERATED (uuid) with the boq_test_ prefix
    — a caller-supplied database can never be dropped, and teardown only
    drops the database this fixture created,
  * migrations run via Alembic against the scratch URL (never create_all),
  * Postgres itself must be reachable at DATABASE_URL; if not, tests that
    need a DB skip (the S3_TEST_ENDPOINT idiom) rather than fail lying.

Gating: the perf suite is collected everywhere but runs only under
BOQ_PERF=1 (module-level skipif in each test module — the same idiom as
backend/tests/test_s3_storage.py) and is deselected from the normal CI
matrix with `-m "not perf"`.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

# §G perf targets are generous on absolute time but REAL walls — no fakes.
BOQ_PERF = os.environ.get("BOQ_PERF", "") == "1"


@pytest.fixture(scope="module")
def migrated_db() -> Generator[str]:
    """A fresh, migrated, live-PostgreSQL scratch DB (drop-safe, generated name)."""
    admin_url = make_url(
        os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://boq:boq@localhost:5432/boq",
        )
    ).set(database="postgres")
    # Generated identifier only: no caller-selected database may ever be dropped.
    database_name = f"boq_test_{uuid.uuid4().hex}"
    database_url = admin_url.set(database=database_name).render_as_string(
        hide_password=False)

    async def setup() -> None:
        engine = create_async_engine(
            admin_url, isolation_level="AUTOCOMMIT", connect_args={"timeout": 10}
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        finally:
            await engine.dispose()

    async def teardown() -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            await engine.dispose()

    try:
        asyncio.run(setup())
    except (OSError, TimeoutError) as exc:
        pytest.skip(f"Postgres not reachable: {exc}")

    try:
        env = dict(os.environ)
        env["DATABASE_URL"] = database_url
        up = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini",
             "upgrade", "head"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
        yield database_url
    finally:
        asyncio.run(teardown())
