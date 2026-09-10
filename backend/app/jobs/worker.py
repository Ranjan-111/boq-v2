"""Worker process + handler registry.

Handlers are registered by kind; the worker loop claims jobs via SKIP LOCKED
and executes. Handlers receive (session, claimed_job) and NEVER the transport.

Run: make worker  →  .venv/bin/python -m backend.app.jobs.worker
"""
from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.jobs import queue
from backend.app.jobs.queue import ClaimedJob

log = structlog.get_logger()

Handler = Callable[[AsyncSession, ClaimedJob], Awaitable[dict[str, Any] | None]]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"duplicate handler kind: {kind}")
        _HANDLERS[kind] = fn
        return fn

    return deco


def handler_for(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


# ---------------------------------------------------------------------------
# Built-in handler: ping (proves the loop works; used by tests)
# ---------------------------------------------------------------------------


@register("ping")
async def _ping(_: AsyncSession, job: ClaimedJob) -> dict[str, Any]:
    return {"pong": True, "kind": job.kind, "attempt": job.attempts}


async def process_one(session: AsyncSession) -> bool:
    """Claim and execute one job. Returns True if a job was processed."""
    claimed = await queue.claim_next(session)
    if claimed is None:
        return False
    handler = handler_for(claimed.kind)
    if handler is None:
        retryable = claimed.attempts < claimed.max_attempts
        status = await queue.fail(
            session, claimed.id, f"no handler for kind {claimed.kind!r}", retryable=retryable
        )
        log.error("job.unknown_kind", job_id=claimed.id, kind=claimed.kind, next_status=status)
        return True
    try:
        result = await handler(session, claimed)
    except Exception as exc:
        retryable = claimed.attempts < claimed.max_attempts
        status = await queue.fail(
            session, claimed.id, f"{type(exc).__name__}: {exc}", retryable=retryable
        )
        log.exception(
            "job.failed", job_id=claimed.id, kind=claimed.kind, next_status=status
        )
        return True
    await queue.complete(session, claimed.id, result)
    log.info("job.succeeded", job_id=claimed.id, kind=claimed.kind)
    return True


async def run_worker(*, poll_seconds: float | None = None, once: bool = False) -> None:
    """The worker loop. once=True processes until the queue is empty then exits (tests)."""
    import backend.app.jobs.handlers  # noqa: F401 — registers parse/run/export kinds
    from backend.app.db.base import make_async_engine, make_sessionmaker

    settings = get_settings()
    poll = poll_seconds or settings.job_poll_seconds
    engine = make_async_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    stop = asyncio.Event()

    def _stop(*_: Any) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover (windows)
            pass

    log.info("worker.start", poll_seconds=poll)
    try:
        while not stop.is_set():
            async with sessionmaker() as session:
                did = await process_one(session)
                await session.commit()
            if not did:
                if once:
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        log.info("worker.stop")


if __name__ == "__main__":
    # Run the CANONICAL module, not this __main__ copy: python -m executes
    # this file as __main__, a SEPARATE module object from backend.app.jobs.
    # worker (which handlers.py registers into via `from ... import worker`).
    # Delegating keeps exactly one _HANDLERS registry.
    from backend.app.jobs.worker import run_worker as _run_worker

    asyncio.run(_run_worker())
