"""Request database lifecycle, independent of the ASGI application entrypoint."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def session_dependency(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    sessionmaker = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker is None:  # lazy init: lifespan may not have run (tests)
        from backend.app.db.base import make_async_engine, make_sessionmaker

        if not hasattr(request.app.state, "engine"):
            request.app.state.engine = make_async_engine(request.app.state.settings.database_url)
        request.app.state.sessionmaker = make_sessionmaker(request.app.state.engine)
        sessionmaker = request.app.state.sessionmaker
    session: AsyncSession = sessionmaker()
    # Exposed on request.state so CommitOnWriteRoute (backend/app/api/scope.py)
    # can commit BEFORE the response is sent. Since FastAPI 0.106 the yield
    # teardown below runs after the response bytes leave — a client's next
    # request could SELECT before this session's transaction commits (the
    # read-your-own-write race CI e2e caught on a loaded runner: 201 register
    # -> 401 unknown_user on the immediately following catalog seed).
    # The teardown commit below stays: idempotent after the route-class commit,
    # and still the safety net for requests the route class does not cover.
    request.state.db_session = session
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
