"""Application startup and shutdown must own database resource lifetime."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.config import Settings
from backend.app.main import create_app

pytestmark = pytest.mark.unit


async def test_lifespan_initializes_and_disposes_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the external database engine is replaced; app/session wiring runs normally.
    engine = MagicMock(spec=AsyncEngine)
    engine.dispose = AsyncMock()
    monkeypatch.setattr("backend.app.db.base.create_async_engine", lambda *a, **kw: engine)
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        assert app.state.engine is engine
        assert app.state.sessionmaker is not None
        engine.dispose.assert_not_awaited()
    engine.dispose.assert_awaited_once()


async def test_lifespan_disposes_database_on_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.dispose = AsyncMock()
    monkeypatch.setattr("backend.app.db.base.create_async_engine", lambda *a, **kw: engine)
    app = create_app(Settings())
    with pytest.raises(RuntimeError, match="application failed"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("application failed")
    engine.dispose.assert_awaited_once()
