"""Postgres-backed job queue — SKIP LOCKED claim, idempotency, progress.

docs/architecture.md §D: one worker role; JobRun row is the truth; handlers
never import the transport. This module provides the queue primitives; the
worker loop + handler registry live in worker.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import JobRun


@dataclass(slots=True)
class JobSpec:
    kind: str
    payload: dict[str, Any] | None = None
    idempotency_key: str | None = None
    max_attempts: int = 3


class DuplicateJob(RuntimeError):
    """Raised when an idempotency_key is already used by a non-terminal job."""

    def __init__(self, existing_id: str) -> None:
        super().__init__(f"idempotency key already active: job {existing_id}")
        self.existing_id = existing_id


async def submit(session: AsyncSession, spec: JobSpec) -> str:
    """Enqueue a job; idempotency: active job with same key returns existing.

    'Active' = queued or running (terminal jobs with the same key are allowed
    to be resubmitted, e.g. a re-parse after failure).
    """
    if spec.idempotency_key:
        row = (
            await session.execute(
                text(
                    "SELECT id FROM jobs WHERE idempotency_key = :k "
                    "AND status IN ('queued','running') LIMIT 1"
                ),
                {"k": spec.idempotency_key},
            )
        ).scalar_one_or_none()
        if row is not None:
            raise DuplicateJob(row)
    job = JobRun(
        id=str(uuid.uuid4()),
        kind=spec.kind,
        payload=spec.payload,
        idempotency_key=spec.idempotency_key,
        max_attempts=spec.max_attempts,
    )
    session.add(job)
    await session.flush()
    return job.id


_CLAIM_SQL = text(
    """
    UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'queued'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, kind, payload, attempts, max_attempts
    """
)


@dataclass(slots=True)
class ClaimedJob:
    id: str
    kind: str
    payload: dict[str, Any] | None
    attempts: int
    max_attempts: int


async def claim_next(session: AsyncSession) -> ClaimedJob | None:
    """Atomically claim one queued job (SKIP LOCKED — multi-worker safe)."""
    row = (await session.execute(_CLAIM_SQL)).mappings().first()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        kind=row["kind"],
        payload=row["payload"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


async def report_progress(session: AsyncSession, job_id: str, percent: int) -> None:
    percent = max(0, min(100, int(percent)))
    await session.execute(
        text("UPDATE jobs SET progress = :p WHERE id = :id"), {"p": percent, "id": job_id}
    )
    await session.flush()


async def complete(
    session: AsyncSession, job_id: str, result: dict[str, Any] | None = None
) -> None:
    import json

    result_json = json.dumps(result) if result is not None else None
    await session.execute(
        text(
            "UPDATE jobs SET status = 'succeeded', finished_at = now(), "
            "result = CAST(:r AS jsonb), progress = 100 WHERE id = :id"
        ),
        {"r": result_json, "id": job_id},
    )
    await session.flush()


async def fail(session: AsyncSession, job_id: str, error: str, *, retryable: bool) -> str:
    """Fail a job: retry (requeue) if attempts remain, else mark failed. Returns new status."""
    new_status = "queued" if retryable else "failed"
    await session.execute(
        text(
            "UPDATE jobs SET status = :s, error = :e, "
            "finished_at = CASE WHEN :s = 'failed' THEN now() ELSE finished_at END "
            "WHERE id = :id"
        ),
        {"s": new_status, "e": error, "id": job_id},
    )
    await session.flush()
    return new_status


async def cancel(session: AsyncSession, job_id: str) -> bool:
    """Cancel a queued job; running jobs cannot be cancelled in V1."""
    res = await session.execute(
        text(
            "UPDATE jobs SET status = 'cancelled', finished_at = now() "
            "WHERE id = :id AND status = 'queued'"
        ),
        {"id": job_id},
    )
    await session.flush()
    return res.rowcount > 0


async def get_status(session: AsyncSession, job_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, kind, status, attempts, progress, error, created_at, "
                "started_at, finished_at FROM jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).mappings().first()
    if row is None:
        return None
    out = dict(row)
    for k in ("created_at", "started_at", "finished_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
