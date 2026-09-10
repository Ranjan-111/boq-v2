"""Existing project routes must isolate creators using the real PostgreSQL query path."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from backend.app.api.projects import ProjectCreate, create_project, get_project, list_projects
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import User

pytestmark = pytest.mark.integration


async def test_project_queries_scope_to_creator(migrated_db: str) -> None:
    engine = make_async_engine(migrated_db)
    try:
        async with make_sessionmaker(engine)() as session:
            users = [
                User(
                    id=str(uuid.uuid4()),
                    email=f"{uuid.uuid4().hex}@example.com",
                    password_hash=uuid.uuid4().hex,
                    display_name="test",
                    role="owner",
                )
                for _ in range(2)
            ]
            # One insert per flush — the application's real pattern (each API
            # request creates one row). Batched ORM inserts of string UUID PKs
            # are a known insertmanyvalues/asyncpg sentinel limitation; see
            # round-3-report.md "Foundation findings".
            for user in users:
                session.add(user)
                await session.flush()
            body = ProjectCreate(name="Private project", currency="INR", region_code="IN")
            own = await create_project(body, users[0], session)
            other = await create_project(body, users[1], session)
            # created_by/id round-trip as str on the API-facing object; the ORM
            # re-fetch may normalize to uuid.UUID, so compare as strings.
            assert str(own.created_by) == str(users[0].id)
            assert str((await get_project(own.id, users[0], session)).id) == str(own.id)
            listed = await list_projects(users[0], session)
            assert [str(row["id"]) for row in listed["items"]] == [str(own.id)]
            with pytest.raises(HTTPException) as forbidden:
                await get_project(other.id, users[0], session)
            assert forbidden.value.status_code == 404
            own.deleted_at = datetime.now(UTC)
            await session.flush()
            assert (await list_projects(users[0], session))["items"] == []
            with pytest.raises(HTTPException) as deleted:
                await get_project(own.id, users[0], session)
            assert deleted.value.status_code == 404
            await session.rollback()
    finally:
        await engine.dispose()
