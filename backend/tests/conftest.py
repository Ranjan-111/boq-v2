"""Real PostgreSQL integration fixtures; never drop or migrate a supplied database."""

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


@pytest.fixture(scope="module")
def migrated_db() -> Generator[str]:
    admin_url = make_url(
        os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://boq:boq@localhost:5432/boq",
        )
    ).set(database="postgres")
    # Generated identifier only: no caller-selected database may ever be dropped.
    database_name = f"boq_test_{uuid.uuid4().hex}"
    database_url = admin_url.set(database=database_name).render_as_string(hide_password=False)

    async def setup() -> None:
        engine = create_async_engine(
            admin_url, isolation_level="AUTOCOMMIT", connect_args={"timeout": 3}
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
            [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
        yield database_url
    finally:
        asyncio.run(teardown())
