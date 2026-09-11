"""Sheets API — sheet detail + THE scale confirmation human gate.

docs/api-contract.md non-negotiable 3: "Scale confirmation is an explicit human
POST — parse/auto-detect cannot." This module is the ONLY code path anywhere in
the product that writes a CONFIRMED calibration (parse_service writes PROPOSED
only, enforced by its own contract). Each confirmation appends an AuditEntry —
who confirmed, from what, to what.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import (
    AuditEntry,
    DrawingFile,
    DrawingSheet,
    Project,
    ScaleCalibrationModel,
    User,
)
from core.domain.enums import AuditAction, ScaleCalibrationStatus

router = APIRouter(prefix="/sheets", tags=["sheets"])


class ScaleConfirmBody(BaseModel):
    """The human's scale statement. Finite, positive, ≤10 decimal places."""

    units_per_drawing_unit: Decimal = Field(gt=0, max_digits=19, decimal_places=10)
    method: Literal["user_two_point", "user_known_ratio"]
    # The measured reference points backing the factor (viewer sends the two
    # picked points; V1 persists nothing from them — the factor is the input).
    points: list[list[float]] | None = None


async def _owned_sheet(
    sheet_id: str, user: User, session: AsyncSession
) -> tuple[DrawingSheet, DrawingFile]:
    """Sheet + its drawing, both scoped to the caller through the project."""
    row = (
        await session.execute(
            select(DrawingSheet, DrawingFile)
            .join(DrawingFile, DrawingSheet.drawing_file_id == DrawingFile.id)
            .join(Project, DrawingFile.project_id == Project.id)
            .where(
                DrawingSheet.id == sheet_id,
                Project.created_by == user.id,
                Project.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise problem_error(404, "not_found", "sheet not found")
    sheet, drawing = row
    return sheet, drawing


async def _calibration(
    session: AsyncSession, sheet_id: str
) -> ScaleCalibrationModel | None:
    return (
        await session.execute(
            select(ScaleCalibrationModel).where(ScaleCalibrationModel.sheet_id == sheet_id)
        )
    ).scalar_one_or_none()


@router.get("/{sheet_id}")
async def get_sheet(
    sheet_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    sheet, _drawing = await _owned_sheet(sheet_id, user, session)
    cal = await _calibration(session, sheet.id)
    return {
        "id": str(sheet.id),
        "drawing_file_id": str(sheet.drawing_file_id),
        "sheet_ref": sheet.sheet_ref,
        "page_number": sheet.page_number,
        "title": sheet.title,
        "sheet_type": sheet.sheet_type,
        "is_modelspace": sheet.sheet_ref == "modelspace",
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
        # Parse context (geometry viewer arrives with the tiles endpoint; the
        # honest signal today is whether the file parsed and what it refused).
        "parse_status": _drawing.parse_status,
        "parse_warnings_count": len(_drawing.parse_warnings or []),
    }


@router.post("/{sheet_id}/scale/confirm")
async def confirm_scale(
    sheet_id: str,
    body: ScaleConfirmBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    """THE HUMAN GATE — the only CONFIRMED calibration writer in the product.

    Re-confirmation is allowed (a human may correct the factor); every call
    appends its own audit row. Nothing here is derivable automatically: the
    client states the factor, the server records who stated it.
    """
    sheet, _drawing = await _owned_sheet(sheet_id, user, session)
    cal = await _calibration(session, sheet.id)
    before_status = (
        cal.status if cal is not None else ScaleCalibrationStatus.UNKNOWN.value
    )
    before_factor = (
        str(cal.units_per_drawing_unit)
        if cal is not None and cal.units_per_drawing_unit is not None
        else None
    )
    if cal is None:
        cal = ScaleCalibrationModel(id=str(uuid.uuid4()), sheet_id=sheet.id)
        session.add(cal)
    cal.status = ScaleCalibrationStatus.CONFIRMED.value
    cal.method = body.method
    cal.units_per_drawing_unit = body.units_per_drawing_unit
    cal.confirmed_by = user.id
    cal.confirmed_at = datetime.now(UTC)
    await session.flush()

    session.add(AuditEntry(
        id=str(uuid.uuid4()),
        actor=user.id,
        action=AuditAction.CONFIRM_SCALE.value,
        subject_type="sheet",
        subject_id=sheet.id,
        # Project-scoped (Round 6 audit trail): THE human gate belongs on
        # the project's trail, not just the global one.
        project_id=uuid.UUID(str(_drawing.project_id)),
        before={"status": before_status, "units_per_drawing_unit": before_factor},
        after={
            "status": ScaleCalibrationStatus.CONFIRMED.value,
            "method": body.method,
            "units_per_drawing_unit": str(body.units_per_drawing_unit),
        },
    ))
    await session.flush()
    return {
        "id": str(cal.id),
        "sheet_id": str(sheet.id),
        "status": cal.status,
        "method": cal.method,
        "units_per_drawing_unit": str(cal.units_per_drawing_unit),
        "confirmed_by": str(cal.confirmed_by) if cal.confirmed_by else None,
        "confirmed_at": cal.confirmed_at.isoformat() if cal.confirmed_at else None,
    }
