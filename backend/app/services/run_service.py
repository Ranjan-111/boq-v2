"""Run service (Round 4) — execute a measurement run against persisted inputs.

The run boundary is where Round 3's verified trust invariants meet storage:
  * the engine re-parses the STORED bytes (never trusts client-passed geometry),
  * the scale gate reads the persisted CONFIRMED calibration (human gate),
  * every measurement row keeps its durable uuid5 identity + evidence,
  * run/exception rows are written through core.domain.states transitions.

Layering (import-linter "Domain service layering"): backend services may
import the domain packages (ingestion, takeoff, core). This module never
imports FastAPI — job handlers and routers call it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    DrawingFile,
    DrawingSheet,
    Element,
    EvidenceLinkModel,
    ExceptionModel,
    GeometryModel,
    MeasurementModel,
    MeasurementRun,
    ScaleCalibrationModel,
)
from backend.app.storage.base import Storage
from core.domain.enums import (
    ExceptionSeverity,
    MeasurementState,
    RunState,
    ScaleCalibrationStatus,
    ScaleMethod,
)
from core.domain.states import transition_run
from core.geometry import NormalizedGeometry
from core.units.geometry_units import ScaleCalibration
from ingestion.dxf import DxfParseError, parse_dxf
from takeoff.engine import RunOutput, measure_parsed

MAX_WALL_THICKNESS_DEFAULT = 250.0


class RunExecutionError(RuntimeError):
    """The run could not execute — honest failure, never a guessed quantity."""


async def _persist_run_output(
    session: AsyncSession,
    *,
    run: MeasurementRun,
    sheet: DrawingSheet,
    parsed_geometries: tuple[NormalizedGeometry, ...],
    out: RunOutput,
) -> None:
    """Write the engine's immutable output as rows (single transaction)."""
    # Elements + geometries (element_index -> RunOutput.elements ordering).
    element_ids: list[str] = []
    for record in out.elements:
        element_id = str(uuid.uuid4())
        element_ids.append(element_id)
        session.add(Element(
            id=element_id, run_id=run.id, sheet_id=sheet.id,
            element_type=record.element_type.value, type_source=record.type_source.value,
            label=record.label,
        ))
        await session.flush()
        session.add(GeometryModel(
            id=str(uuid.uuid4()), element_id=element_id,
            geom_type=record.geometry.geom_type.value,
            coordinates=record.geometry.to_json()["coordinates"],
            source_format=record.geometry.source_format.value,
            source_handles=[h.to_json() for h in record.geometry.source_handles],
        ))
        await session.flush()
    # Measurements + evidence links.
    for m in out.measurements:
        if m.element_index is None or not (0 <= m.element_index < len(element_ids)):
            raise RunExecutionError("measurement without a bound element")
        measurement_row_id = str(uuid.uuid4())
        session.add(MeasurementModel(
            id=measurement_row_id, run_id=run.id, element_id=element_ids[m.element_index],
            measurement_id=m.measurement_id, quantity_type=m.quantity_type.value,
            value=m.value, unit=m.unit.value, rule_id=m.rule_id,
            engine_version=m.engine_version, inputs=list(m.inputs),
            inputs_digest=m.inputs_digest, state=m.state.value, label=m.label,
        ))
        await session.flush()
        for ev in m.evidence:
            session.add(EvidenceLinkModel(
                id=str(uuid.uuid4()), subject_type="measurement",
                subject_id=uuid.UUID(measurement_row_id), kind=ev.kind, ref=ev.ref,
                note=ev.note,
            ))
        await session.flush()
    # Exceptions (sheet-scoped; measurement linkage where the engine provides it).
    for exc in out.exceptions:
        session.add(ExceptionModel(
            id=str(uuid.uuid4()), run_id=run.id,
            measurement_id=None, element_id=None, sheet_id=sheet.id,
            # exc.code is an ExceptionCode StrEnum member (== its string value)
            code=exc.code, severity=exc.severity.value, message=exc.message,
            evidence=[{"kind": e.kind, "ref": e.ref, "note": e.note} for e in exc.evidence],
        ))
        await session.flush()


async def execute_run(
    session: AsyncSession,
    *,
    run_id: str,
    storage: Storage,
    max_wall_thickness: float | None = MAX_WALL_THICKNESS_DEFAULT,
) -> dict[str, Any]:
    """Execute a queued run: stored bytes -> engine -> persisted rows.

    Marks the run COMPLETED / COMPLETED_WITH_EXCEPTIONS / FAILED via the
    state machine; raises nothing on engine refusals (they become exception
    rows + COMPLETED_WITH_EXCEPTIONS), only on infra errors (which mark FAILED).
    """
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        raise RunExecutionError(f"run {run_id} not found")
    if run.status != RunState.QUEUED.value:
        raise RunExecutionError(f"run {run_id} is {run.status}, not queued")

    # The run consumes exactly one drawing file + one sheet in V1.
    params: dict[str, Any] = run.params or {}
    drawing_file_id = params.get("drawing_file_id")
    sheet_id = params.get("sheet_id")
    if not drawing_file_id or not sheet_id:
        await _fail_run(session, run, "run params missing drawing_file_id/sheet_id")
        return {"ok": False, "status": run.status, "error": run.error}

    try:
        run.status = transition_run(RunState(run.status), RunState.RUNNING).value
        run.started_at = datetime.now(UTC)
        await session.flush()
    except ValueError as exc:
        raise RunExecutionError(str(exc)) from exc

    drawing = (await session.execute(
        select(DrawingFile).where(
            DrawingFile.id == drawing_file_id,
            DrawingFile.parse_status == "parsed",
        )
    )).scalar_one_or_none()
    sheet = (await session.execute(
        select(DrawingSheet).where(DrawingSheet.id == sheet_id)
    )).scalar_one_or_none()
    if drawing is None or sheet is None or sheet.drawing_file_id != drawing.id:
        await _fail_run(
            session, run,
            "drawing file is not parsed or sheet does not belong to it",
        )
        return {"ok": False, "status": run.status, "error": run.error}

    calibration_row = (await session.execute(
        select(ScaleCalibrationModel).where(
            ScaleCalibrationModel.sheet_id == sheet.id,
            ScaleCalibrationModel.status == ScaleCalibrationStatus.CONFIRMED.value,
        )
    )).scalar_one_or_none()
    if calibration_row is None:
        # Human gate unsatisfied: honest refusal as an exception row, run
        # completes WITH exceptions (the review queue lights up).
        session.add(ExceptionModel(
            id=str(uuid.uuid4()), run_id=run.id, sheet_id=sheet.id,
            code="scale_unconfirmed", severity=ExceptionSeverity.BLOCKING.value,
            message="sheet scale is not confirmed — confirm scale before measuring",
            evidence=None,
        ))
        stats = await _finish_run(session, run, measured=0, blocked=0, exceptions=1)
        return {"ok": False, "status": run.status, "stats": stats,
                "error": "scale_unconfirmed"}

    # Re-parse the STORED bytes: the run never trusts client-passed geometry.
    try:
        data = storage.get(drawing.storage_key)
    except KeyError as exc:
        await _fail_run(session, run, f"stored drawing missing: {exc}")
        return {"ok": False, "status": run.status, "error": run.error}
    try:
        parsed = parse_dxf(data)
    except DxfParseError as exc:
        await _fail_run(session, run, f"drawing re-parse failed: {exc}")
        return {"ok": False, "status": run.status, "error": run.error}

    calibration = ScaleCalibration(
        sheet_id=sheet.sheet_ref,
        status=ScaleCalibrationStatus.CONFIRMED,
        units_per_drawing_unit=(Decimal(str(calibration_row.units_per_drawing_unit))
                                if calibration_row.units_per_drawing_unit is not None else None),
        method=calibration_row.method or ScaleMethod.USER_TWO_POINT.value,
    )
    out: RunOutput = measure_parsed(
        parsed, sheet_id=sheet.sheet_ref, calibration=calibration,
        max_wall_thickness=max_wall_thickness,
    )
    await _persist_run_output(session, run=run, sheet=sheet,
                              parsed_geometries=parsed.geometries, out=out)

    blocking = sum(1 for e in out.exceptions
                   if e.severity is ExceptionSeverity.BLOCKING)
    measured = sum(1 for m in out.measurements
                   if m.state in (MeasurementState.MEASURED, MeasurementState.MEASURED_ZERO))
    stats = await _finish_run(
        session, run, measured=measured, blocked=blocking,
        exceptions=len(out.exceptions),
    )
    run.engine_version = out.engine_version
    await session.flush()
    return {"ok": True, "status": run.status, "stats": stats}


async def _fail_run(
    session: AsyncSession, run: MeasurementRun, message: str
) -> None:
    run.error = message
    try:
        run.status = transition_run(RunState(run.status), RunState.FAILED).value
    except ValueError:
        run.status = RunState.FAILED.value  # already terminal; keep honest
    run.finished_at = datetime.now(UTC)
    await session.flush()


async def _finish_run(
    session: AsyncSession, run: MeasurementRun, *, measured: int, blocked: int,
    exceptions: int,
) -> dict[str, int]:
    """Record stats and transition RUNNING -> terminal (with/without exceptions)."""
    run.stats = {"measured": measured, "blocked": blocked, "exceptions": exceptions}
    run.finished_at = datetime.now(UTC)
    terminal = (RunState.COMPLETED_WITH_EXCEPTIONS if exceptions
                else RunState.COMPLETED)
    try:
        run.status = transition_run(RunState(run.status), terminal).value
    except ValueError as exc:
        raise RunExecutionError(f"run {run.id}: {exc}") from exc
    await session.flush()
    return {"measured": measured, "blocked": blocked, "exceptions": exceptions}
