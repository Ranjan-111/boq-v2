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
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
