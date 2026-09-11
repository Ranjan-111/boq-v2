"""Job handlers (Round 4) — queue kinds wired to services.

Handlers receive (session, claimed_job) and never the transport. Each returns
a result dict recorded on the job row. Parse runs the parse service; run
executes the measurement run; export writes the artifact via boq_service.
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.jobs import worker
from backend.app.jobs.queue import ClaimedJob
from backend.app.storage.base import Storage, storage_from_settings

log = structlog.get_logger()


def _storage() -> Storage:
    """Storage adapter from settings (local dev adapter in V1)."""
    settings = get_settings()
    return storage_from_settings(settings.storage_backend, settings.storage_local_dir)


@worker.register("parse_drawing")
async def handle_parse_drawing(session: AsyncSession, job: ClaimedJob) -> dict[str, Any]:
    from backend.app.services.parse_service import execute_parse

    assert job.payload is not None, "parse_drawing payload required"
    storage = _storage()
    result = await execute_parse(
        session, drawing_file_id=str(job.payload["drawing_file_id"]), storage=storage
    )
    return result


@worker.register("measurement_run")
async def handle_measurement_run(session: AsyncSession, job: ClaimedJob) -> dict[str, Any]:
    from backend.app.services.run_service import execute_run

    assert job.payload is not None, "measurement_run payload required"
    payload = job.payload
    storage = _storage()
    max_thickness = payload.get("max_wall_thickness")
    return await execute_run(
        session,
        run_id=str(payload["run_id"]),
        storage=storage,
        max_wall_thickness=(float(max_thickness) if max_thickness is not None else None),
    )


@worker.register("boq_export")
async def handle_boq_export(session: AsyncSession, job: ClaimedJob) -> dict[str, Any]:
    from backend.app.services.boq_service import execute_export

    assert job.payload is not None, "boq_export payload required"
    payload = job.payload
    storage = _storage()
    return await execute_export(
        session, export_id=str(payload["export_id"]), storage=storage,
        actor=str(payload["actor"]),
    )


@worker.register("ai_analyze")
async def handle_ai_analyze(session: AsyncSession, job: ClaimedJob) -> dict[str, Any]:
    """Advisory AI pass (T060): prompt logs + suggestions, nothing else.

    The handler is deliberately thin — the trust boundary lives in
    ai_service (provider calls sanitized, confidence mandatory, rows go
    ONLY to prompt_logs/ai_suggestions). A provider failure fails this
    job honestly with the machine-readable reason.
    """
    from backend.app.services.ai_service import execute_analyze

    assert job.payload is not None, "ai_analyze payload required"
    return await execute_analyze(session, run_id=str(job.payload["run_id"]))
