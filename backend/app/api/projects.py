"""Projects API — the first authenticated resource (docs/api-contract.md)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.db.models import Project, User
from backend.app.main import session_dependency

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client_name: str | None = Field(default=None, max_length=200)
    region_code: str = Field(min_length=2, max_length=8, pattern="^[A-Za-z0-9-]+$")
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")


class ProjectOut(BaseModel):
    id: str | uuid.UUID
    name: str
    client_name: str | None
    region_code: str
    currency: str

    model_config = {"from_attributes": True}

    @classmethod
    def serialize(cls, p: Project) -> dict[str, object]:
        return {
            "id": str(p.id),
            "name": p.name,
            "client_name": p.client_name,
            "region_code": p.region_code,
            "currency": p.currency,
        }


@router.post("", status_code=201, response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> Project:
    project = Project(
        id=str(uuid.uuid4()),
        name=body.name,
        client_name=body.client_name,
        region_code=body.region_code.upper(),
        currency=body.currency.upper(),
        created_by=user.id,
    )
    session.add(project)
    await session.flush()
    return project


@router.get("", response_model=dict)
async def list_projects(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    q = select(Project).where(Project.deleted_at.is_(None)).order_by(Project.created_at, Project.id)
    if cursor:
        q = q.where(Project.id > cursor)
    q = q.limit(min(limit, 100))
    result = await session.execute(q)
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "name": p.name,
                "client_name": p.client_name,
                "region_code": p.region_code,
                "currency": p.currency,
            }
            for p in items
        ],
        "next_cursor": str(items[-1].id) if len(items) == min(limit, 100) else None,
    }


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise HTTPException(
            status_code=404, detail=[{"code": "not_found", "message": "project not found"}]
        )
    return project
