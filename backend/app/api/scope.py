"""Shared API dependencies: project scoping + storage access.

Every project-scoped route resolves ownership through `owned_project` —
the ONLY authorization path for project data (creator scoping, V1 has no
membership table). Storage is a request-scoped adapter from settings.
"""
from __future__ import annotations

import uuid
from typing import cast

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import Project, User
from backend.app.storage.base import Storage, storage_from_settings


def problem_error(status: int, code: str, message: str) -> HTTPException:
    """Error detail in the [{code, message}] envelope the frontend parses."""
    return HTTPException(status_code=status, detail=[{"code": code, "message": message}])


async def owned_project(
    project_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> Project:
    """Resolve a project the caller owns, or 404 (never 403 — no existence leak).

    UUID-guard first: a malformed id 404s before SQL — the asyncpg Uuid cast
    trap would otherwise answer a 500 for garbage input.
    """
    try:
        uuid.UUID(project_id)
    except ValueError as exc:
        raise problem_error(404, "not_found", "project not found") from exc
    project = (
        await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.created_by == user.id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise problem_error(404, "not_found", "project not found")
    return project


def get_storage(request: Request) -> Storage:
    """Storage adapter from app settings (local dev adapter in V1)."""
    settings = request.app.state.settings
    if not hasattr(request.app.state, "storage"):
        request.app.state.storage = storage_from_settings(
            settings.storage_backend, settings.storage_local_dir
        )
    return cast(Storage, request.app.state.storage)
