"""Measurement engine (T049) — the deterministic pipeline core for a run.

Pure: geometries in → measurements + exceptions out. No DB, no AI, no clock.

Pipeline per measurable sheet:
  1. SCALE GATE: core.units.ScaleCalibration.require_confirmed() — a sheet
     without CONFIRMED scale produces ZERO measurements and one BLOCKING
     SCALE_UNCONFIRMED exception per sheet (never guesses).
  2. WALL DETECTION (T042): parallel-pair pairing → WallCandidates.
  3. PER WALL: length measurement (rule wall.centerline.length.v1) with
     evidence = both edge geometries' source handles; footprint area derived.
  4. EXCEPTIONS: kernel refusals (SELF_INTERSECTING, OPEN_POLYLINE) become
     NOT_MEASURABLE/BLOCKED rows + ExceptionRecords — surfaced, never swallowed.

Every measurement carries the full replay contract (rule_id, engine_version,
inputs_digest) and >=1 evidence link (invariant 1) or it is BLOCKED.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from core.domain.enums import (
    ElementType,
    ElementTypeSource,
    ExceptionSeverity,
    GeomType,
    MeasurementState,
    MeasurementUnit,
    QuantityType,
)
from core.geometry import NormalizedGeometry, ParseResult
from core.provenance.records import (
    EvidenceLink,
    ExceptionRecord,
    MeasurementInputs,
)
from core.units.geometry_units import (
    ScaleCalibration,
    ScaleNotConfirmed,
    convert_area,
    convert_length,
    drawing_unit_mm,
    measurement_state_for,
    round_quantity,
)
from takeoff.rules import ENGINE_VERSION, get_rule, run_rule
from takeoff.wall_detection import WallCandidate, detect_walls, wall_footprint

# Severity policy (data, not code): which exception codes block what.
SEVERITY_POLICY: dict[str, ExceptionSeverity] = {
    "scale_unconfirmed": ExceptionSeverity.BLOCKING,
    "self_intersecting": ExceptionSeverity.BLOCKING,
    "open_polyline": ExceptionSeverity.REVIEW,
    "overlap_detected": ExceptionSeverity.REVIEW,
    "missing_evidence": ExceptionSeverity.BLOCKING,
    "parse_incomplete": ExceptionSeverity.BLOCKING,
    "ambiguous_sheet": ExceptionSeverity.BLOCKING,
}


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    """One deterministic quantity with full provenance (docs/domain-model.md)."""

    quantity_type: QuantityType
    value: Decimal  # project units (post confirmed-scale conversion)
    unit: MeasurementUnit
    rule_id: str
    engine_version: str
    inputs_digest: str
    state: MeasurementState
    element_type: ElementType
    evidence: tuple[EvidenceLink, ...]
    inputs: tuple[str, ...]  # geometry ids / source handles consumed
    label: str | None = None
    # Wall-specific derived data for the evidence panel (drawing units):
    centerline: tuple[tuple[float, float], tuple[float, float]] | None = None
    thickness: float | None = None


    @property
    def measurement_id(self) -> str:
        """Stable identity of this immutable computed result, not its label."""
        return str(uuid5(NAMESPACE_URL, f"boq:measurement:{self.inputs_digest}"))


@dataclass(frozen=True, slots=True)
class RunOutput:
    """Everything one measurement run produced (immutable snapshot)."""

    measurements: tuple[MeasurementRecord, ...]
    exceptions: tuple[ExceptionRecord, ...]
    engine_version: str = ENGINE_VERSION


def _exc(code: str, message: str, **kw: object) -> ExceptionRecord:
    from core.domain.enums import ExceptionCode

    try:
        enum_code = ExceptionCode(code)
    except ValueError:
        enum_code = ExceptionCode.PARSE_INCOMPLETE  # unknown codes never crash the engine
    return ExceptionRecord(
        code=enum_code,
        severity=SEVERITY_POLICY.get(code, ExceptionSeverity.REVIEW),
        message=message,
        **kw,  # type: ignore[arg-type]
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def measure_parsed(
    parsed: ParseResult, *, sheet_id: str, calibration: ScaleCalibration,
    max_wall_thickness: float | None = None,
) -> RunOutput:
    """Consume the entire parser result, including refusals and raw-file identity."""
    sheet = next((s for s in parsed.sheets if s.sheet_ref == sheet_id), None)
    warnings = parsed.warnings
    if sheet is None or not sheet.is_modelspace:
        warnings = (*warnings, "requested sheet is absent or not modelspace")
    if not parsed.source_sha256:
        warnings = (*warnings, "parsed input has no raw source version")
    return measure_sheet(
        sheet_id=sheet_id, geometries=list(parsed.geometries), calibration=calibration,
        drawing_units=parsed.drawing_units, max_wall_thickness=max_wall_thickness,
        source_id=parsed.source_sha256, source_version=parsed.source_sha256,
        parse_warnings=warnings,
    )


def measure_sheet(
    *, sheet_id: str, geometries: list[NormalizedGeometry], calibration: ScaleCalibration,
    drawing_units: str, target_length_unit: MeasurementUnit = MeasurementUnit.M,
    target_area_unit: MeasurementUnit = MeasurementUnit.M2,
    max_wall_thickness: float | None = None,
    source_id: str | None = None, source_version: str | None = None,
    parse_warnings: tuple[str, ...] = (),
) -> RunOutput:
    """Pure geometry entrypoint; caller must supply complete warnings/source context.

    File callers should use measure_parsed so extraction refusals cannot be lost.
    """
    try:
        factor = calibration.require_confirmed()
        if calibration.sheet_id != sheet_id:
            raise ScaleNotConfirmed("calibration belongs to another sheet")
        drawing_unit_mm(drawing_units)
        if target_length_unit not in (MeasurementUnit.MM, MeasurementUnit.M):
            raise ValueError("invalid length target")
        if target_area_unit is not MeasurementUnit.M2:
            raise ValueError("invalid area target")
    except (ScaleNotConfirmed, ValueError) as exc:
        return RunOutput((), (_exc("scale_unconfirmed", str(exc), sheet_id=sheet_id),))
    if parse_warnings:
        return RunOutput((), tuple(_exc("parse_incomplete", warning, sheet_id=sheet_id)
                                   for warning in sorted(set(parse_warnings))))
    if any(h.sheet_ref != sheet_id for g in geometries for h in g.source_handles):
        return RunOutput((), (_exc("ambiguous_sheet", "source belongs to another sheet",
                                  sheet_id=sheet_id),))
    if any(not g.source_handles or any(h.entity_ref.strip() in ("", "0", "None")
                                       for h in g.source_handles) for g in geometries):
        return RunOutput((), (_exc("missing_evidence", "each source geometry needs evidence",
                                  sheet_id=sheet_id),))
    try:
        snapshots = sorted((g.to_json() for g in geometries), key=_canonical)
        snapshot = hashlib.sha256(_canonical(snapshots).encode()).hexdigest()
    except (ValueError, TypeError):
        return RunOutput((), (_exc("parse_incomplete", "non-finite or invalid geometry",
                                  sheet_id=sheet_id),))
    from takeoff.kernel import NotMeasurable, area_of
    from takeoff.wall_detection import OFFSET_EPS, PARALLEL_EPS

    exceptions: list[ExceptionRecord] = []
    measurements: list[MeasurementRecord] = []
    try:
        detection = detect_walls(geometries, max_thickness=max_wall_thickness)
    except ValueError as exc:
        return RunOutput((), (_exc("parse_incomplete", str(exc), sheet_id=sheet_id),))
    if max_wall_thickness is None and detection.considered_edges:
        exceptions.append(_exc("overlap_detected", "explicit maximum wall thickness required",
                               sheet_id=sheet_id))
    for a, b in detection.overlaps:
        exceptions.append(_exc("overlap_detected", f"ambiguous wall faces {a} and {b}",
                               sheet_id=sheet_id))
    for h in detection.unmatched_edges:
        exceptions.append(_exc("overlap_detected", f"wall edge {h} has no unique supported pair",
                               sheet_id=sheet_id))
    for wall_no, wall in enumerate(detection.walls, 1):
        footprint = wall_footprint(wall)
        for rule_id, quantity_type, target, rule_inputs, label in (
            ("wall.centerline.length.v1", QuantityType.LENGTH, target_length_unit,
             list(wall.edge_geometries), f"Wall {wall_no}"),
            ("wall.footprint.area.v1", QuantityType.AREA, target_area_unit,
             [footprint], f"Wall {wall_no} footprint"),
        ):
            rule = get_rule(rule_id)
            raw = Decimal(str(run_rule(rule_id, rule_inputs)))
            conversion = convert_length if quantity_type is QuantityType.LENGTH else convert_area
            value = round_quantity(conversion(raw, drawing_units, calibration, target))
            inputs = MeasurementInputs(
                refs=tuple(h.entity_ref for h in wall.source_handles),
                constants={
                    "schema": "measurement-replay-v2", "sheet": sheet_id,
                    "source_id": source_id or snapshot,
                    "source_version": source_version or snapshot,
                    "sheet_geometry": snapshots,
                    "rule_inputs": [g.to_json() for g in rule_inputs],
                    "scale": str(factor.normalize()), "scale_method": calibration.method,
                    "scale_status": calibration.status.value,
                    "drawing_units": drawing_units, "target_units": target.value,
                    "rule_id": rule_id, "rule_version": rule.version,
                    "engine_version": ENGINE_VERSION, "max_wall_thickness": max_wall_thickness,
                    "parallel_eps": PARALLEL_EPS, "offset_eps": OFFSET_EPS,
                    "wall_layers_only": True,
                },
            )
            evidence = tuple(EvidenceLink(
                kind="geometry", ref=_canonical({"source": source_id or snapshot,
                    "version": source_version or snapshot, "handle": h.to_json()}),
                note="wall source face",
            ) for h in wall.source_handles)
            measurements.append(MeasurementRecord(
                quantity_type=quantity_type, value=value, unit=target,
                rule_id=rule_id, engine_version=ENGINE_VERSION, inputs_digest=inputs.digest(),
                state=measurement_state_for(value, has_evidence=bool(evidence)),
                element_type=ElementType.WALL, evidence=evidence, inputs=inputs.refs,
                label=label, centerline=wall.centerline, thickness=wall.thickness,
            ))
    for geom in geometries:
        if geom.geom_type is GeomType.POLYGON:
            try:
                area_of(geom)
            except NotMeasurable as exc:
                code = "self_intersecting" if "self-inter" in str(exc) else "open_polyline"
                exceptions.append(_exc(code, f"polygon refused: {exc}", sheet_id=sheet_id))
    return RunOutput(tuple(measurements), tuple(exceptions))


# ---------------------------------------------------------------------------
# Element records (the Element rows the backend persists per run)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ElementRecord:
    """docs/domain-model.md §Element — here a wall element per detection."""

    element_type: ElementType
    type_source: ElementTypeSource
    geometry: NormalizedGeometry
    label: str | None


def elements_from_walls(walls: list[WallCandidate]) -> list[ElementRecord]:
    out: list[ElementRecord] = []
    for i, wall in enumerate(walls, start=1):
        out.append(
            ElementRecord(
                element_type=ElementType.WALL,
                type_source=ElementTypeSource.GEOMETRY_DETERMINISTIC,
                geometry=wall_footprint(wall),
                label=f"Wall {i}",
            )
        )
    return out
