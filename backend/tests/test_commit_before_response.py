"""Read-your-own-write regression: mutating handlers commit BEFORE response.

CI e2e (run 34862695461) caught the real race on a loaded runner: two
workers, the same ~200ms window —

  POST /auth/register                       201
  POST /catalog/items  (other worker)       201
  POST /catalog/items  (viewer-surfaces)    401 unknown_user   <- the row the
  PUT  /catalog/items/{id}/rates/default    404 not found         201s above
                                                              were NOT yet
                                                              committed

Mechanism: session_dependency committed in yield-teardown, which since
FastAPI 0.106 runs AFTER the response bytes are sent; the client's next
request (fired milliseconds later, no artificial delay — exactly what a
browser refetch or an E2E seed does) can execute its SELECT before that
commit lands.

Fix: CommitOnWriteRoute (backend/app/api/routes.py) wraps every mutating
route so the commit happens after the handler returns but before starlette
sends the response. These tests drive the REAL app through the ASGI
transport and fire the immediately-following request with zero delay —
the failure mode itself — against a live migrated DB.
"""
from __future__ import annotations

import uuid

import pytest

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import User

pytestmark = pytest.mark.integration


async def _close(engine, session) -> None:  # type: ignore[no-untyped-def]
    await session.rollback()
    await session.close()
    await engine.dispose()


class TestCommitBeforeResponse:
    async def test_register_then_immediate_me_sees_the_user(
        self, migrated_db: str,
    ) -> None:
        """The exact CI failure: 201 register -> next request 401 because
        the user row was not yet committed when the 201 was delivered."""
        import httpx

        from backend.app.config import Settings
        from backend.app.main import create_app

        secret = "ryow-test-secret-0123456789abcdef"  # noqa: S105
        settings = Settings(database_url=migrated_db, jwt_secret=secret)
        app = create_app(settings)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                r = await client.post("/api/v1/auth/register", json={
                    "email": f"ryow-{uuid.uuid4().hex}@example.com",
                    "password": "ryow-password-1",
                    "display_name": "RYOW",
                })
                assert r.status_code == 201, r.text
                token = r.json()["access_token"]
                # ZERO delay: the request the race loses.
                r2 = await client.get("/api/v1/auth/me", headers={
                    "Authorization": f"Bearer {token}"})
                assert r2.status_code == 200, r2.text
                assert r2.json()["email"].startswith("ryow-")
        finally:
            await app.state.engine.dispose()

    async def test_catalog_create_then_immediate_rate_put(
        self, migrated_db: str,
    ) -> None:
        """The second CI failure: 201 catalog/items -> 404 on the rate PUT
        for that very item id (the item row was not committed yet)."""
        import httpx

        from backend.app.config import Settings
        from backend.app.main import create_app

        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        user = User(
            id=str(uuid.uuid4()),
            email=f"ryow-{uuid.uuid4().hex}@example.com",
            password_hash=uuid.uuid4().hex,
            display_name="t",
            role="owner",
        )
        session.add(user)
        await session.commit()
        try:
            from backend.app.auth.tokens import issue_token

            secret = "ryow-test-secret-0123456789abcdef"  # noqa: S105
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
                    code = f"RYOW-{uuid.uuid4().hex[:8]}"
                    r = await client.post("/api/v1/catalog/items", json={
                        "region_code": "IN", "code": code,
                        "description": "ryow regression item",
                        "unit": "m", "category_path": "walls/brick",
                    }, headers=headers)
                    assert r.status_code == 201, r.text
                    item_id = r.json()["id"]
                    # ZERO delay: the PUT the race 404'd.
                    r2 = await client.put(
                        f"/api/v1/catalog/items/{item_id}/rates/default",
                        json={"amount_minor": 85000, "currency": "INR"},
                        headers=headers)
                    assert r2.status_code == 200, r2.text
                    # And the next GET sees the rate (the write is durable).
                    r3 = await client.get(
                        f"/api/v1/catalog/items/{item_id}/rates",
                        headers=headers)
                    assert r3.status_code == 200, r3.text
                    assert r3.json()["items"], "rate row invisible after 200"
            finally:
                await app.state.engine.dispose()
        finally:
            await _close(engine, session)

    async def test_error_paths_never_commit_partial_writes(
        self, migrated_db: str,
    ) -> None:
        """The commit-before-send wrapper must NOT commit when the handler
        raised: a 409 duplicate register leaves no half-state, and the
        teardown rollback keeps authority on error paths."""
        import httpx
        from sqlalchemy import select

        from backend.app.config import Settings
        from backend.app.main import create_app

        secret = "ryow-test-secret-0123456789abcdef"  # noqa: S105
        settings = Settings(database_url=migrated_db, jwt_secret=secret)
        app = create_app(settings)
        email = f"ryow-dup-{uuid.uuid4().hex}@example.com"
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                r1 = await client.post("/api/v1/auth/register", json={
                    "email": email, "password": "ryow-password-1",
                    "display_name": "RYOW"})
                assert r1.status_code == 201, r1.text
                # Duplicate: 409, and the ORIGINAL registration stays intact
                # (visible to an immediately following login).
                r2 = await client.post("/api/v1/auth/register", json={
                    "email": email, "password": "ryow-password-2",
                    "display_name": "RYOW2"})
                assert r2.status_code == 409, r2.text
                r3 = await client.post("/api/v1/auth/login", json={
                    "email": email, "password": "ryow-password-1"})
                assert r3.status_code == 200, r3.text
        finally:
            await app.state.engine.dispose()

        # No phantom second user survived the 409 path.
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            rows = (await session.execute(
                select(User).where(User.email == email))).scalars().all()
            assert len(rows) == 1, rows
        finally:
            await _close(engine, session)
