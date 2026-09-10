"""Measurement engine (T049) — the deterministic pipeline core for a run.

Pure: geometries in → measurements + exceptions out. No DB, no AI, no clock.

Pipeline per measurable sheet (Round 5 — the full takeoff engine):
  1. SCALE GATE: core.units.ScaleCalibration.require_confirmed() — a sheet
     without CONFIRMED scale produces ZERO measurements and one BLOCKING
     SCALE_UNCONFIRMED exception per sheet (never guesses).
  2. WALL DETECTION (T042): parallel-pair pairing → WallCandidates.
  3. ROOMS (T043): polygonize wall centerlines → enclosed faces; gross
     (to centerline) + net (minus footprints) areas; labels from text
     tokens (label evidence), never guessed.
  4. OPENINGS (T045): named door/window blocks spanning walls + aligned
     face gaps; ambiguous/partial spans surface for review.
  5. DEDUCTIONS (T046): opening areas subtract from wall footprint areas;
     net wall areas; MEASURED_ZERO carries its own evidence.
  6. FLOORS (T044): room gross/net roll-up per storey (storey grouping is
     a labeling concern in V1 — one plan = one floor).
  7. EXCEPTIONS: kernel refusals become NOT_MEASURABLE/BLOCKED rows +
     ExceptionRecords — surfaced, never swallowed.

Every measurement carries the full replay contract (rule_id, engine_version,
inputs_digest) and >=1 evidence link (invariant 1) or it is BLOCKED.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
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
from core.geometry import (
    NormalizedGeometry,
    ParseResult,
    SourceHandleRef,
    TextToken,
)
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
    # Round 5 (full takeoff engine) — rooms/openings refusals.
    "room_not_enclosed": ExceptionSeverity.REVIEW,
    "room_topology": ExceptionSeverity.BLOCKING,
    "opening_ambiguous": ExceptionSeverity.REVIEW,
    "opening_partial_span": ExceptionSeverity.REVIEW,
    "pdf_path_unclassified": ExceptionSeverity.REVIEW,
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
    # Index into RunOutput.elements for the element this measurement belongs
    # to (Round 4 persistence: measurement rows carry element_id). Additive —
    # the replay identity binds inputs_digest only, which this never feeds.
    element_index: int | None = None
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
    # Element records the measurements belong to (Round 4 persistence).
    # Ordering is the contract: MeasurementRecord.element_index indexes this
    # tuple. Additive — callers that ignore it are unaffected.
    elements: tuple[ElementRecord, ...] = ()


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


def _convert_count(
    value: Decimal, drawing_unit: str, calibration: ScaleCalibration,
    target: MeasurementUnit,
) -> Decimal:
    """Counts are unit-free: the conversion is the identity (scale and drawing
    units do not apply — counting doors is not a length). Validated strictly
    so a misuse cannot silently pass a scaled count through."""
    if target is not MeasurementUnit.COUNT:
        raise ValueError(f"{target} is not a count unit")
    _ = drawing_unit, calibration  # counts ignore scale by definition
    return value


def measure_parsed(
    parsed: ParseResult, *, sheet_id: str, calibration: ScaleCalibration,
    max_wall_thickness: float | None = None,
    block_names: dict[str, str] | None = None,
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
        parse_warnings=warnings, text_tokens=parsed.text_tokens,
        block_names=block_names,
    )


def measure_sheet(
    *, sheet_id: str, geometries: list[NormalizedGeometry], calibration: ScaleCalibration,
    drawing_units: str, target_length_unit: MeasurementUnit = MeasurementUnit.M,
    target_area_unit: MeasurementUnit = MeasurementUnit.M2,
    max_wall_thickness: float | None = None,
    source_id: str | None = None, source_version: str | None = None,
    parse_warnings: tuple[str, ...] = (),
    text_tokens: tuple[TextToken, ...] = (),
    block_names: dict[str, str] | None = None,
) -> RunOutput:
    """Pure geometry entrypoint; caller must supply complete warnings/source context.

    File callers should use measure_parsed so extraction refusals cannot be lost.
    text_tokens: drawing text labels (room labels — label evidence, never
    geometry). block_names: INSERT handle -> block name (opening blocks, T045).
    Both are optional; their absence disables room labels / named-block
    openings honestly (face-gap detection still runs).
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

    # --- Rooms (T043) -------------------------------------------------------
    from takeoff.room_detection import (
        RoomDetectionResult,
        assign_room_labels,
        detect_rooms,
        room_geometry,
    )

    room_result: RoomDetectionResult = detect_rooms(detection.walls)
    room_records = list(room_result.rooms)
    assign_room_labels(room_records, text_tokens)
    for msg in room_result.topology_refusals:
        exceptions.append(_exc("room_topology", msg, sheet_id=sheet_id))
    if room_result.not_enclosed:
        exceptions.append(_exc(
            "room_not_enclosed",
            f"{room_result.wall_count} walls but no enclosed room on this sheet",
            sheet_id=sheet_id,
        ))

    # --- Openings (T045) + deductions (T046) --------------------------------
    from takeoff.openings import detect_openings

    opening_result = detect_openings(
        detection.walls, geometries, block_names=block_names
    )
    for msg in opening_result.ambiguous:
        exceptions.append(_exc("opening_ambiguous", msg, sheet_id=sheet_id))
    for msg in opening_result.partial_span:
        exceptions.append(_exc("opening_partial_span", msg, sheet_id=sheet_id))

    elements: list[ElementRecord] = [
        ElementRecord(
            element_type=ElementType.WALL,
            type_source=ElementTypeSource.GEOMETRY_DETERMINISTIC,
            geometry=wall_footprint(wall),
            label=f"Wall {i}",
        )
        for i, wall in enumerate(detection.walls, start=1)
    ]
    wall_element_geoms = {i: elements[i].geometry for i in range(len(elements))}

    # Room elements (gross ring is the element geometry; the net ring is a
    # derived geometry consumed by the net-area rule).
    room_element_index: dict[int, int] = {}
    for ri, room in enumerate(room_records):
        gross_geom = room_geometry(
            room.gross_ring, room.source_handles,
            source_format_value=(room.source_handles[0].format.value
                                 if room.source_handles else "dxf_entity"),
            sheet_ref=sheet_id,
            derived_from=room.derived_from,
        )
        room_element_index[ri] = len(elements)
        elements.append(ElementRecord(
            element_type=ElementType.ROOM,
            type_source=ElementTypeSource.GEOMETRY_DETERMINISTIC,
            geometry=gross_geom,
            label=room.label or f"Room {ri + 1}",
        ))
    # Opening elements (one per opening; kind in the label).
    opening_element_index: dict[int, int] = {}
    for oi, opening in enumerate(opening_result.openings):
        # Evidence geometry: the contributing member geometries are already
        # in the input list; the opening element points at its wall.
        wall_geom = wall_element_geoms.get(opening.wall_index)
        if wall_geom is None:  # pragma: no cover - openings always bind a wall
            continue
        opening_element_index[oi] = len(elements)
        elements.append(ElementRecord(
            element_type=(ElementType.DOOR if opening.kind == "door"
                          else ElementType.WINDOW if opening.kind == "window"
                          else ElementType.OPENING),
            type_source=ElementTypeSource.GEOMETRY_DETERMINISTIC,
            geometry=wall_geom,
            label=f"{opening.kind.capitalize()} in Wall {opening.wall_index + 1}",
        ))

    def _emit(
        *,
        rule_id: str,
        quantity_type: QuantityType,
        target: MeasurementUnit,
        rule_inputs: list[NormalizedGeometry],
        raw: Decimal,
        element_index: int,
        evidence_refs: tuple[SourceHandleRef, ...],
        label: str,
        extra_constants: dict[str, Any],
        element_type: ElementType = ElementType.WALL,
    ) -> None:
        """One measurement through the registered rule (single emit discipline)."""
        rule = get_rule(rule_id)
        value = round_quantity(
            (convert_length if quantity_type is QuantityType.LENGTH else
             convert_area if quantity_type is QuantityType.AREA else
             _convert_count)(Decimal(raw), drawing_units, calibration, target)
        )
        inputs = MeasurementInputs(
            refs=tuple(h.entity_ref for h in evidence_refs),
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
                **extra_constants,
            },
        )
        evidence = tuple(EvidenceLink(
            kind="geometry", ref=_canonical({"source": source_id or snapshot,
                "version": source_version or snapshot, "handle": h.to_json()}),
            note=label,
        ) for h in evidence_refs)
        measurements.append(MeasurementRecord(
            quantity_type=quantity_type, value=value, unit=target,
            rule_id=rule_id, engine_version=ENGINE_VERSION,
            inputs_digest=inputs.digest(),
            state=measurement_state_for(value, has_evidence=bool(evidence)),
            element_type=element_type, evidence=evidence,
            inputs=inputs.refs, label=label, element_index=element_index,
        ))


    def _refs(
        *geoms: NormalizedGeometry,
    ) -> tuple[SourceHandleRef, ...]:
        """All source handles of the given geometries, order-stable."""
        return tuple(h for g in geoms for h in g.source_handles)

    # --- Wall measurements (T042): length + footprint + count + net --------
    for wall_no, wall in enumerate(detection.walls, 1):
        footprint = wall_element_geoms[wall_no - 1]
        # Slots of THIS wall, in the detection's opening order (the replay
        # inputs for the count and the net-of-openings rules).
        wall_slots: list[NormalizedGeometry] = [
            slot for slot, o in zip(opening_result.slots,
                                   opening_result.openings,
                                   strict=True)
            if o.wall_index == wall_no - 1
        ]
        # Length (centerline) — unchanged R3 behavior; keeps the wall-specific
        # derived data the R4 evidence viewer highlights.
        _emit(
            rule_id="wall.centerline.length.v1",
            quantity_type=QuantityType.LENGTH,
            target=target_length_unit,
            rule_inputs=list(wall.edge_geometries),
            raw=Decimal(str(run_rule("wall.centerline.length.v1",
                                     list(wall.edge_geometries)))),
            element_index=wall_no - 1,
            evidence_refs=_refs(*wall.edge_geometries),
            label=f"Wall {wall_no}",
            extra_constants={},
        )
        length_record = measurements[-1]
        measurements[-1] = replace(
            length_record, centerline=wall.centerline, thickness=wall.thickness
        )
        # Gross footprint area (deductions NOT applied) — unchanged R3 behavior.
        _emit(
            rule_id="wall.footprint.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=[footprint],
            raw=Decimal(str(run_rule("wall.footprint.area.v1", [footprint]))),
            element_index=wall_no - 1,
            evidence_refs=_refs(footprint),
            label=f"Wall {wall_no} footprint",
            extra_constants={},
        )
        # Opening count (T045): one slot geometry per opening; zero openings
        # is an EMPTY slot list — an honest MEASURED_ZERO, never a face count.
        refs = (tuple(h for s in wall_slots for h in s.source_handles)
                or _refs(*wall.edge_geometries))
        _emit(
            rule_id="opening.count.v1",
            quantity_type=QuantityType.COUNT,
            target=MeasurementUnit.COUNT,
            rule_inputs=wall_slots,
            raw=Decimal(str(run_rule("opening.count.v1", wall_slots))),
            element_index=wall_no - 1,
            evidence_refs=refs,
            label=(f"Wall {wall_no} openings ({len(wall_slots)})"
                   if wall_slots else f"Wall {wall_no} openings (none detected)"),
            extra_constants={},
        )
        # Net wall area (T046): footprint minus the slot geometries — the
        # rule subtracts from geometry alone; replay is self-contained.
        _emit(
            rule_id="wall.net.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=[footprint, *wall_slots],
            raw=Decimal(str(run_rule("wall.net.area.v1",
                                     [footprint, *wall_slots]))),
            element_index=wall_no - 1,
            evidence_refs=_refs(footprint, *wall.edge_geometries,
                                *wall_slots),
            label=f"Wall {wall_no} net of openings",
            extra_constants={},
        )

    # --- Room measurements (T043/T044) --------------------------------------
    room_gross_geoms: list[NormalizedGeometry] = []
    room_net_geoms: list[NormalizedGeometry] = []
    for ri, room in enumerate(room_records):
        gross_geom = elements[room_element_index[ri]].geometry
        net_geom = room_geometry(
            room.net_ring, room.source_handles,
            source_format_value=(room.source_handles[0].format.value
                                 if room.source_handles else "dxf_entity"),
            sheet_ref=sheet_id,
            derived_from=room.derived_from,
        )
        room_gross_geoms.append(gross_geom)
        room_net_geoms.append(net_geom)
        label = room.label or f"Room {ri + 1}"
        _emit(
            rule_id="room.gross.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=[gross_geom],
            raw=Decimal(str(run_rule("room.gross.area.v1", [gross_geom]))),
            element_index=room_element_index[ri],
            evidence_refs=room.source_handles,
            label=f"{label} gross area",
            extra_constants={"bounding_walls": list(room.bounding_walls)},
            element_type=ElementType.ROOM,
        )
        _emit(
            rule_id="room.net.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=[net_geom],
            raw=Decimal(str(run_rule("room.net.area.v1", [net_geom]))),
            element_index=room_element_index[ri],
            evidence_refs=room.source_handles,
            label=f"{label} net area",
            extra_constants={"bounding_walls": list(room.bounding_walls)},
            element_type=ElementType.ROOM,
        )
        if room.label_token is not None:
            # Label evidence rides as its own evidence link on the room
            # measurements (text_token kind) — the label is never guessed.
            label_ref = EvidenceLink(
                kind="text_token",
                ref=_canonical({"source": source_id or snapshot,
                                "token": room.label_token.to_json()}),
                note=f"room label {room.label_token.text!r}",
            )
            for m in measurements[-2:]:
                measurements[measurements.index(m)] = replace(
                    m, evidence=(*m.evidence, label_ref)
                )

    # --- Floor roll-up (T044) — one floor element per sheet in V1 -----------
    if room_gross_geoms:
        floor_element_index = len(elements)
        elements.append(ElementRecord(
            element_type=ElementType.FLOOR_FINISH,
            type_source=ElementTypeSource.GEOMETRY_DETERMINISTIC,
            geometry=room_gross_geoms[0],
            label="Floor (all rooms)",
        ))
        floor_refs = tuple(h for g in (*room_gross_geoms, *room_net_geoms)
                           for h in g.source_handles)
        _emit(
            rule_id="floor.gross.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=list(room_gross_geoms),
            raw=Decimal(str(run_rule("floor.gross.area.v1", list(room_gross_geoms)))),
            element_index=floor_element_index,
            evidence_refs=floor_refs,
            label="Floor gross area (room roll-up)",
            extra_constants={"room_count": len(room_gross_geoms)},
            element_type=ElementType.FLOOR_FINISH,
        )
        _emit(
            rule_id="floor.net.area.v1",
            quantity_type=QuantityType.AREA,
            target=target_area_unit,
            rule_inputs=list(room_net_geoms),
            raw=Decimal(str(run_rule("floor.net.area.v1", list(room_net_geoms)))),
            element_index=floor_element_index,
            evidence_refs=floor_refs,
            label="Floor net area (room roll-up)",
            extra_constants={"room_count": len(room_gross_geoms)},
            element_type=ElementType.FLOOR_FINISH,
        )

    for geom in geometries:
        if geom.geom_type is GeomType.POLYGON:
            try:
                area_of(geom)
            except NotMeasurable as exc:
                code = "self_intersecting" if "self-inter" in str(exc) else "open_polyline"
                exceptions.append(_exc(code, f"polygon refused: {exc}", sheet_id=sheet_id))
    return RunOutput(tuple(measurements), tuple(exceptions), elements=tuple(elements))


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
