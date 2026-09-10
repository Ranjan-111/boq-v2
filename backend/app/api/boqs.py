"""BOQ & export API (Round 4) — build, tree, approval gates, export (contract).

Approval and export are enforced SERVER-SIDE (boq_service):
  * approve refuses unless REVIEWED + items exist + zero unresolved blockers,
  * export enqueues a job whose handler builds the ExportApproval context
    from TRUSTED persistence — never from client input.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import get_storage, owned_project, problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import (
    BoqModel,
    ExportArtifact,
    Project,
    User,
)
from backend.app.jobs.queue import DuplicateJob, JobSpec, submit
from backend.app.services import boq_service

router = APIRouter(tags=["boq"])


class BoqCreate(BaseModel):
    from_run_id: str


class NoteBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/projects/{project_id}/boqs", status_code=201)
async def create_boq(
    project_id: str,
    body: BoqCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, Any]:
    try:
        return await boq_service.build_boq_from_run(
            session, project_id=project.id, from_run_id=body.from_run_id,
            actor=user.id,
        )
    except boq_service.BoqServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


@router.get("/projects/{project_id}/boqs")
async def list_boqs(
    project_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    project: Project = Depends(owned_project),
) -> dict[str, Any]:
    rows = (await session.execute(
        select(BoqModel).where(BoqModel.project_id == project.id)
        .order_by(BoqModel.created_at, BoqModel.id)
    )).scalars().all()
    return {"items": [
        {"id": str(b.id), "version": b.version, "status": b.status,
         "from_run_id": str(b.from_run_id) if b.from_run_id else None}
        for b in rows
    ]}


async def _owned_boq_or_404(
    session: AsyncSession, boq_id: str, project: Project
) -> BoqModel:
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == boq_id,
                               BoqModel.project_id == project.id)
    )).scalar_one_or_none()
    if boq is None:
        raise problem_error(404, "not_found", "BOQ not found")
    return boq


@router.get("/boqs/{boq_id}")
async def get_boq(
    boq_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    # /boqs/{id} is project-implicit; resolve ownership via the boq's project.
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == boq_id)
    )).scalar_one_or_none()
    if boq is None:
        raise problem_error(404, "not_found", "BOQ not found")
    owned = (await session.execute(
        select(Project).where(Project.id == boq.project_id,
                              Project.created_by == user.id,
                              Project.deleted_at.is_(None))
    )).scalar_one_or_none()
    if owned is None:
        raise problem_error(404, "not_found", "BOQ not found")
    tree = await boq_service.load_boq_tree(session, boq_id=boq_id,
                                           project_id=str(owned.id))
    return tree or {}


@router.post("/boqs/{boq_id}/submit")
async def submit_boq(
    boq_id: str,
    _: NoteBody | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    boq = await _resolve_callers_boq(session, boq_id, user)
    try:
        return await boq_service.submit_boq(
            session, project_id=str(boq.project_id), boq_id=boq_id,
            actor=user.id)
    except boq_service.BoqServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


@router.post("/boqs/{boq_id}/approve")
async def approve_boq(
    boq_id: str,
    body: NoteBody | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    boq = await _resolve_callers_boq(session, boq_id, user)
    try:
        return await boq_service.approve_boq(
            session, project_id=str(boq.project_id), boq_id=boq_id,
            actor=user.id, note=body.note if body else None)
    except boq_service.BoqServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


@router.post("/boqs/{boq_id}/reject")
async def reject_boq(
    boq_id: str,
    body: NoteBody | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    boq = await _resolve_callers_boq(session, boq_id, user)
    try:
        return await boq_service.reject_boq(
            session, project_id=str(boq.project_id), boq_id=boq_id,
            actor=user.id, note=body.note if body else None)
    except boq_service.BoqServiceError as exc:
        raise problem_error(exc.status, exc.code, exc.message) from exc


async def _resolve_callers_boq(
    session: AsyncSession, boq_id: str, user: User
) -> BoqModel:
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == boq_id)
    )).scalar_one_or_none()
    if boq is None:
        raise problem_error(404, "not_found", "BOQ not found")
    owned = (await session.execute(
        select(Project).where(Project.id == boq.project_id,
                              Project.created_by == user.id,
                              Project.deleted_at.is_(None))
    )).scalar_one_or_none()
    if owned is None:
        raise problem_error(404, "not_found", "BOQ not found")
    return boq


class ExportCreate(BaseModel):
    format: str = Field(pattern="^(csv)$")  # xlsx/pdf arrive later (T101/T102)


@router.post("/boqs/{boq_id}/exports", status_code=202)
async def create_export(
    boq_id: str,
    body: ExportCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, str]:
    boq = await _resolve_callers_boq(session, boq_id, user)
    artifact = ExportArtifact(
        id=str(uuid.uuid4()), boq_id=boq.id, format=body.format,
        status="pending", storage_key="", sha256="", created_by=user.id,
    )
    session.add(artifact)
    await session.flush()
    try:
        job_id = await submit(session, JobSpec(
            kind="boq_export",
            payload={"export_id": artifact.id, "actor": user.id},
            idempotency_key=f"export:{artifact.id}",
        ))
    except DuplicateJob as exc:
        raise problem_error(409, "export_already_active", str(exc)) from exc
    return {"export_id": artifact.id, "job_id": job_id}


@router.get("/exports/{export_id}")
async def get_export(
    export_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    artifact = (await session.execute(
        select(ExportArtifact).where(ExportArtifact.id == export_id)
    )).scalar_one_or_none()
    if artifact is None:
        raise problem_error(404, "not_found", "export not found")
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == artifact.boq_id)
    )).scalar_one_or_none()
    owned = None
    if boq is not None:
        owned = (await session.execute(
            select(Project).where(Project.id == boq.project_id,
                                  Project.created_by == user.id,
                                  Project.deleted_at.is_(None))
        )).scalar_one_or_none()
    if boq is None or owned is None:
        raise problem_error(404, "not_found", "export not found")
    storage = get_storage(request)
    download_url = None
    if artifact.status == "succeeded" and artifact.storage_key:
        download_url = storage.signed_url(artifact.storage_key)
    return {
        "id": str(artifact.id), "boq_id": str(artifact.boq_id),
        "format": artifact.format, "status": artifact.status,
        "sha256": artifact.sha256 or None, "manifest": artifact.manifest,
        "download_url": download_url,
    }
