"""Jobs API — status reads for async work (parse/run/export jobs).

V1 simplification (honest, single-workspace): JobRun rows carry no owner
column, so any authenticated user may read any job status — identical to the
project scoping stance where the workspace has no cross-tenant boundary. When
multi-tenancy lands, the job payload (drawing_file_id/run_id) will resolve
through the same creator scoping as every other resource. Until then this is
documented, not hidden.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import User
from backend.app.jobs import queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    status = await queue.get_status(session, job_id)
    if status is None:
        raise problem_error(404, "not_found", "job not found")
    return status
