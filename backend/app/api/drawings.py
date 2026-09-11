"""Drawings API — upload (validated, then stored), list, detail, delete, download.

Trust rules (docs/api-contract.md non-negotiables 3 & 5):
  * validate_upload BEFORE storage — no unvalidated byte ever reaches storage,
  * the parse job is enqueued; parsing itself runs in the job (parse_service),
  * ownership: every drawing resolves through its project's creator (404, not
    403 — no existence leak), matching scope.owned_project semantics.

Two routers, one module (the Lead registers both in main.py):
  * `router`         — project-scoped /projects/{project_id}/drawings
  * `drawing_router` — item-level /drawings/{drawing_id}[...]
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import get_storage, owned_project, problem_error
from backend.app.config import Settings, get_settings
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import (
    DrawingFile,
    DrawingSheet,
    JobRun,
    Project,
    ScaleCalibrationModel,
    User,
)
from backend.app.jobs import queue
from backend.app.jobs.queue import DuplicateJob, JobSpec
from backend.app.storage.base import KeyNotFound, Storage
from backend.app.uploads.validation import UploadRejected, validate_upload

router = APIRouter(prefix="/projects/{project_id}/drawings", tags=["drawings"])
drawing_router = APIRouter(prefix="/drawings", tags=["drawings"])

PARSE_JOB_KIND = "parse_drawing"


def _upload_rejection(exc: UploadRejected) -> HTTPException:
    status = 413 if exc.code == "too_large" else 400
    return problem_error(status, exc.code, exc.message)


async def _owned_drawing(
    drawing_id: str, user: User, session: AsyncSession
) -> tuple[DrawingFile, Project]:
    """Drawing + its project, both scoped to the caller (creator scoping)."""
    row = (
        await session.execute(
            select(DrawingFile, Project)
            .join(Project, DrawingFile.project_id == Project.id)
            .where(
                DrawingFile.id == drawing_id,
                Project.created_by == user.id,
                Project.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise problem_error(404, "not_found", "drawing not found")
    drawing, project = row
    return drawing, project


async def _submit_parse_job(
    session: AsyncSession, drawing_file_id: str
) -> str:
    """Enqueue the parse job for one drawing file, honestly.

    Two guards:
      * an ACTIVE (queued/running) parse for this file raises DuplicateJob
        -> the caller returns 409 parse_already_active (never two parses),
      * the idempotency key embeds an attempt ordinal because
        JobRun.idempotency_key is UNIQUE across ALL history (a re-parse after
        a failed parse needs a key no earlier job used — attempt N+1).
    """
    active = (
        await session.execute(
            select(JobRun.id).where(
                JobRun.kind == PARSE_JOB_KIND,
                JobRun.status.in_(("queued", "running")),
                JobRun.payload["drawing_file_id"].as_string() == drawing_file_id,
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise DuplicateJob(str(active))
    attempts = (
        await session.execute(
            select(func.count())
            .select_from(JobRun)
            .where(
                JobRun.kind == PARSE_JOB_KIND,
                JobRun.payload["drawing_file_id"].as_string() == drawing_file_id,
            )
        )
    ).scalar_one()
    spec = JobSpec(
        kind=PARSE_JOB_KIND,
        payload={"drawing_file_id": drawing_file_id},
        idempotency_key=f"parse:{drawing_file_id}:v{attempts + 1}",
    )
    return await queue.submit(session, spec)


async def do_upload(
    session: AsyncSession,
    *,
    project: Project,
    user: User,
    filename: str,
    data: bytes,
    declared_mime: str | None,
    max_bytes: int,
    storage: Storage,
) -> dict[str, str]:
    """The upload use-case, separated from the multipart transport for testing.

    Validate -> dedupe -> store -> enqueue. Raises HTTPException on refusal.
    Returns {"drawing_file_id", "job_id"} (202 semantics).
    """
    try:
        validated = validate_upload(
            filename=filename,
            data=data,
            max_bytes=max_bytes,
            declared_mime=declared_mime,
        )
    except UploadRejected as exc:
        raise _upload_rejection(exc) from exc

    # Idempotent upload: the same bytes in the same project reuse the row.
    existing = (
        await session.execute(
            select(DrawingFile).where(
                DrawingFile.project_id == project.id,
                DrawingFile.sha256 == validated.sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        drawing_file_id = str(existing.id)
        if existing.parse_status not in ("pending", "failed"):
            # Already parsed/parsing: no new job — the standing parse answers.
            return {"drawing_file_id": drawing_file_id, "job_id": ""}
    else:
        drawing = DrawingFile(
            id=str(uuid.uuid4()),
            project_id=project.id,
            filename=validated.filename,
            format=validated.format,
            size_bytes=validated.size_bytes,
            storage_key=validated.storage_key,
            sha256=validated.sha256,
            uploaded_by=user.id,
        )
        session.add(drawing)
        await session.flush()
        drawing_file_id = str(drawing.id)
        # Storage AFTER validation (content-addressed key; put is idempotent).
        storage.put(validated.storage_key, io.BytesIO(data), length=len(data))

    try:
        job_id = await _submit_parse_job(session, drawing_file_id)
    except DuplicateJob as exc:
        # A parse is already queued/running for this file: honest conflict.
        raise problem_error(
            409, "parse_already_active", f"a parse job is already active: {exc.existing_id}"
        ) from exc
    return {"drawing_file_id": drawing_file_id, "job_id": job_id}


@router.post("", status_code=202)
async def upload_drawing(
    project: Project = Depends(owned_project),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings),
    storage: Storage = Depends(get_storage),
    file: UploadFile = File(...),
) -> dict[str, str]:
    data = await file.read()
    return await do_upload(
        session,
        project=project,
        user=user,
        filename=file.filename or "",
        data=data,
        declared_mime=file.content_type,
        max_bytes=settings.max_upload_bytes,
        storage=storage,
    )


@router.get("")
async def list_drawings(
    project: Project = Depends(owned_project),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DrawingFile)
            .where(DrawingFile.project_id == project.id)
            .order_by(DrawingFile.uploaded_at, DrawingFile.id)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "format": d.format,
                "size_bytes": d.size_bytes,
                "sha256": d.sha256,
                "parse_status": d.parse_status,
                # Full warnings array (the UI renders them) + count kept for
                # the list view's badge.
                "parse_warnings": list(d.parse_warnings or []),
                "parse_warnings_count": len(d.parse_warnings or []),
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in rows
        ]
    }


@drawing_router.get("/{drawing_id}")
async def get_drawing(
    drawing_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    drawing, _project = await _owned_drawing(drawing_id, user, session)
    sheet_rows = (
        await session.execute(
            select(DrawingSheet, ScaleCalibrationModel)
            .outerjoin(
                ScaleCalibrationModel,
                ScaleCalibrationModel.sheet_id == DrawingSheet.id,
            )
            .where(DrawingSheet.drawing_file_id == drawing.id)
            .order_by(DrawingSheet.page_number, DrawingSheet.sheet_ref)
        )
    ).all()
    return {
        "id": str(drawing.id),
        "project_id": str(drawing.project_id),
        "filename": drawing.filename,
        "format": drawing.format,
        "size_bytes": drawing.size_bytes,
        "sha256": drawing.sha256,
        "parse_status": drawing.parse_status,
        "parse_warnings": list(drawing.parse_warnings or []),
        "uploaded_at": drawing.uploaded_at.isoformat() if drawing.uploaded_at else None,
        "sheets": [
            {
                "id": str(sheet.id),
                "sheet_ref": sheet.sheet_ref,
                "page_number": sheet.page_number,
                "title": sheet.title,
                "sheet_type": sheet.sheet_type,
                "is_modelspace": sheet.sheet_ref == "modelspace",
                # Flat status kept for list badges; the nested calibration
                # object is what the run form + confirm form bind to.
                "calibration_status": cal.status if cal is not None else "unknown",
                "calibration": (
                    {
                        "status": cal.status,
                        "method": cal.method,
                        "units_per_drawing_unit": (
                            str(cal.units_per_drawing_unit)
                            if cal.units_per_drawing_unit is not None
                            else None
                        ),
                        "confirmed_at": (
                            cal.confirmed_at.isoformat() if cal.confirmed_at else None
                        ),
                    }
                    if cal is not None
                    else None
                ),
            }
            for sheet, cal in sheet_rows
        ],
    }


@drawing_router.delete("/{drawing_id}", status_code=204)
async def delete_drawing(
    drawing_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    storage: Storage = Depends(get_storage),
) -> None:
    drawing, _project = await _owned_drawing(drawing_id, user, session)
    # V1 hard-deletes (the model has no deleted_at): calibrations and sheets
    # cascade manually, then the row, then the stored bytes.
    sheet_ids = select(DrawingSheet.id).where(DrawingSheet.drawing_file_id == drawing.id)
    await session.execute(
        delete(ScaleCalibrationModel).where(ScaleCalibrationModel.sheet_id.in_(sheet_ids))
    )
    await session.execute(
        delete(DrawingSheet).where(DrawingSheet.drawing_file_id == drawing.id)
    )
    await session.delete(drawing)
    await session.flush()
    # Storage keys are content-addressed (sha256): the same bytes uploaded to
    # another project share this blob. Delete the bytes only when this row was
    # the last reference — never destroy another project's drawing bytes.
    other_refs = (
        await session.execute(
            select(func.count())
            .select_from(DrawingFile)
            .where(
                DrawingFile.storage_key == drawing.storage_key,
                DrawingFile.id != drawing.id,
            )
        )
    ).scalar_one()
    if other_refs == 0:
        storage.delete(drawing.storage_key)


@drawing_router.get("/{drawing_id}/download")
async def download_drawing(
    drawing_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    storage: Storage = Depends(get_storage),
) -> dict[str, str]:
    """Signed URL — stored bytes are never served straight from a public path."""
    drawing, _project = await _owned_drawing(drawing_id, user, session)
    return {"url": storage.signed_url(drawing.storage_key)}


@drawing_router.get("/{drawing_id}/preview")
async def preview_drawing(
    drawing_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
    storage: Storage = Depends(get_storage),
) -> Response:
    """Authenticated raster preview; pixels remain review evidence only.

    This endpoint deliberately serves no normalized geometry or scale. The
    frontend labels the image as a pixel preview and keeps the measurement
    workflow behind the same human scale gate as every other drawing.
    """
    drawing, _project = await _owned_drawing(drawing_id, user, session)
    if drawing.format != "raster":
        raise problem_error(409, "preview_not_raster", "only raster drawings have pixel previews")
    try:
        data = storage.get(drawing.storage_key)
    except KeyNotFound as exc:
        raise problem_error(404, "not_found", "raster source is unavailable") from exc
    media_type = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(Path(drawing.filename).suffix.lower(), "application/octet-stream")
    return Response(content=data, media_type=media_type,
                    headers={"Cache-Control": "no-store", "X-Source-Sha256": drawing.sha256})
