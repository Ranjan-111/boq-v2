"""FastAPI app foundation — factory under 200 lines (docs/architecture.md §I-4).

Wiring: settings, logging, error envelope (RFC 7807), request-id middleware,
health endpoints, routers (auth, projects), DB session dependency.
Jobs/worker live separately (backend/app/jobs).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.config import Settings, get_settings

log = structlog.get_logger()


def problem(
    *,
    status: int,
    title: str,
    detail: str | None = None,
    code: str = "error",
    request_id: str | None = None,
) -> JSONResponse:
    """RFC 7807 problem+json — the ONLY error shape (docs/api-contract.md)."""
    body: dict[str, Any] = {
        "type": f"https://boq-v2.dev/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


async def request_context(request: Request, call_next: Any) -> Any:
    """Assign a request id + structured access log line."""
    rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = rid
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "request.unhandled", request_id=rid, method=request.method, path=request.url.path
        )
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info(
        "request",
        request_id=rid,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        elapsed_ms=round(elapsed_ms, 2),
    )
    response.headers["X-Request-Id"] = rid
    return response


def make_engine(settings: Settings) -> AsyncEngine:
    from backend.app.db.base import make_async_engine

    return make_async_engine(settings.database_url)


async def session_dependency(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    sessionmaker = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker is None:  # lazy init: lifespan may not have run (tests)
        from backend.app.db.base import make_sessionmaker

        if not hasattr(request.app.state, "engine"):
            request.app.state.engine = make_engine(request.app.state.settings)
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        # OpenAPI-first: the spec is the contract (docs/api-contract.md)
        openapi_tags=[
            {"name": "auth"},
            {"name": "projects"},
            {"name": "drawings"},
            {"name": "runs"},
            {"name": "jobs"},
        ],
    )
    app.state.settings = settings

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        from backend.app.db.base import make_sessionmaker

        app.state.engine = make_engine(settings)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        log.info("app.start", env=settings.env)
        try:
            yield
        finally:
            await app.state.engine.dispose()
            log.info("app.stop")

    app.middleware("http")(request_context)

    @app.get("/healthz", tags=["meta"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"], include_in_schema=False)
    async def readyz(request: Request) -> Any:
        engine: AsyncEngine = request.app.state.engine
        try:
            from sqlalchemy import text

            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception as exc:
            return problem(
                status=503,
                title="Not ready",
                detail=str(exc),
                code="db_unreachable",
                request_id=getattr(request.state, "request_id", None),
            )

    from backend.app.api import auth, projects

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")

    app.state.SessionLocal = None  # legacy name guard
    return app


# Gunicorn/uvicorn entrypoint
app = create_app()
