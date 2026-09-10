"""Parse service (Round 4) — stored drawing bytes -> parsed sheets + calibrations.

The parse boundary's trust stance (docs/domain-model.md, Round 3 semantics):
  * idempotency guard: a drawing already parsed (or mid-parse) is refused, not
    re-parsed silently — re-parse would invalidate sheet identity downstream
    (calibrations, elements, measurements all point at sheet rows),
  * honest failure: non-DXF formats and structural parse errors mark the file
    FAILED with a machine-readable reason in parse_warnings — never a faked
    success, never a swallowed error,
  * the human gate: auto-detection from $INSUNITS writes PROPOSED calibrations
    ONLY. Only POST /sheets/{id}/scale/confirm (an authenticated human) can
    ever write CONFIRMED.

Layering (import-linter "Domain service layering"): services import the domain
packages (ingestion, core). This module never imports FastAPI — the job handler
and routers call it.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import DrawingFile, DrawingSheet, ScaleCalibrationModel
from backend.app.storage.base import KeyNotFound, Storage
from core.domain.enums import ScaleCalibrationStatus, ScaleMethod
from core.geometry import SheetSummary
from ingestion.dxf import DxfParseError, parse_dxf

# $INSUNITS codes the parser surfaces (core.geometry SheetSummary.unit_code).
# A drawing unit of exactly 1 unit-per-drawing-unit is the identity proposal
# for these codes; everything else stays NULL until a human confirms.
_KNOWN_UNIT_CODES = frozenset({"mm", "cm", "m", "in", "ft"})

_PARSE_REFUSAL_PREFIX = "parse_failed: "


class ParseRefused(RuntimeError):
    """The parse cannot run (wrong state, missing bytes, unsupported format)."""


async def execute_parse(
    session: AsyncSession, *, drawing_file_id: str, storage: Storage
) -> dict[str, Any]:
    """Parse one stored drawing file and persist sheets + PROPOSED calibrations.

    Returns a structured result the job layer records verbatim:
      {"ok": bool, "parse_status": str, ...} — failures are returned, never
      raised, so the job result (visible via GET /jobs/{id}) carries the reason.
    Only infrastructural misuse raises (ParseRefused for the idempotency guard).
    """
    drawing = (
        await session.execute(select(DrawingFile).where(DrawingFile.id == drawing_file_id))
    ).scalar_one_or_none()
    if drawing is None:
        raise ParseRefused(f"drawing file {drawing_file_id} not found")
    if drawing.parse_status not in ("pending", "failed"):
        # Idempotency guard: a parsed (or mid-parse) file is never re-parsed
        # silently — sheet identity would change under downstream references.
        raise ParseRefused(
            f"drawing file {drawing_file_id} is {drawing.parse_status}; "
            "only pending or failed files may be (re)parsed"
        )

    drawing.parse_status = "parsing"
    await session.flush()

    try:
        data = storage.get(drawing.storage_key)
    except KeyNotFound as exc:
        return await _fail(
            session, drawing, f"stored bytes missing for key {drawing.storage_key}: {exc}"
        )

    if drawing.format != "dxf":
        # Honest failure: PDF/raster parsing arrives in a later round. The
        # upload was validated and stored; parsing is refused loudly.
        return await _fail(
            session, drawing, f"only DXF parsing exists in V1 (format={drawing.format!r})"
        )

    try:
        result = parse_dxf(data)
    except DxfParseError as exc:
        return await _fail(session, drawing, f"DxfParseError: {exc}")

    # A re-parse replaces sheets: the old sheet rows (and their calibrations)
    # are invalidated by the new parse identity. Delete before inserting.
    old_sheet_ids = select(DrawingSheet.id).where(DrawingSheet.drawing_file_id == drawing.id)
    await session.execute(
        delete(ScaleCalibrationModel).where(ScaleCalibrationModel.sheet_id.in_(old_sheet_ids))
    )
    await session.execute(
        delete(DrawingSheet).where(DrawingSheet.drawing_file_id == drawing.id)
    )
    await session.flush()

    for page_number, summary in enumerate(result.sheets):
        await _persist_sheet(session, drawing_file_id=drawing.id, page_number=page_number,
                             summary=summary)

    drawing.parse_status = "parsed"
    drawing.parse_warnings = list(result.warnings)
    await session.flush()
    return {
        "ok": True,
        "parse_status": "parsed",
        "sheet_count": len(result.sheets),
        "warnings": len(result.warnings),
    }


async def _persist_sheet(
    session: AsyncSession, *, drawing_file_id: str, page_number: int, summary: SheetSummary
) -> None:
    sheet = DrawingSheet(
        id=str(uuid.uuid4()),
        drawing_file_id=drawing_file_id,
        page_number=page_number,
        sheet_ref=summary.sheet_ref,
        title=summary.layout_name,
        # V1 treats the modelspace layout as the plan sheet; paper-space
        # layouts stay unclassified (which viewport is the drawing is never
        # guessed — the parser marks them not measurable).
        sheet_type="plan" if summary.is_modelspace else None,
    )
    session.add(sheet)
    await session.flush()

    # Auto-detection only ever PROPOSES. For a known $INSUNITS code the
    # identity factor (1 unit-per-drawing-unit) is the proposal; unknown
    # units stay NULL until a human confirms. NEVER CONFIRMED here.
    proposal = (
        Decimal(1)
        if summary.unit_code is not None and summary.unit_code in _KNOWN_UNIT_CODES
        else None
    )
    session.add(ScaleCalibrationModel(
        id=str(uuid.uuid4()),
        sheet_id=sheet.id,
        status=ScaleCalibrationStatus.PROPOSED.value,
        method=ScaleMethod.DETECTED_FROM_DXF_UNITS.value,
        units_per_drawing_unit=proposal,
    ))
    await session.flush()


async def _fail(
    session: AsyncSession, drawing: DrawingFile, message: str
) -> dict[str, Any]:
    """Mark the file FAILED with the machine-readable reason in parse_warnings."""
    drawing.parse_status = "failed"
    drawing.parse_warnings = [f"{_PARSE_REFUSAL_PREFIX}{message}"]
    await session.flush()
    return {"ok": False, "parse_status": "failed", "error": message}
