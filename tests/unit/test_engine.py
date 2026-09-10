"""T049 — measurement engine tests: scale gate, wall quantities, exceptions.

The engine is the pipeline core (docs/architecture.md determinism boundary):
geometries in → measurements + exceptions out, every measurement replayable
({rule_id, engine_version, inputs_digest}) with >=1 evidence link.

Pinned behavior:
  * unconfirmed scale → ZERO measurements + one BLOCKING SCALE_UNCONFIRMED,
  * confirmed scale (mm drawing, 1.0 factor) → wall length + footprint area
    in project units with full provenance,
  * kernel refusals surface as exception records (never swallowed),
  * determinism: same inputs → byte-identical outputs.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.domain.enums import (
    ExceptionSeverity,
    GeomType,
    MeasurementState,
    MeasurementUnit,
    QuantityType,
    ScaleCalibrationStatus,
    SourceFormat,
)
from core.geometry import NormalizedGeometry, SourceHandleRef
from core.units.geometry_units import ScaleCalibration
from takeoff.engine import measure_sheet


def wall_face(a: tuple[float, float], b: tuple[float, float], handle: str) -> NormalizedGeometry:
    return NormalizedGeometry(
        geom_type=GeomType.POLYLINE,
        coordinates=[a, b],
        source_format=SourceFormat.DXF_ENTITY,
        source_handles=(
            SourceHandleRef(
                format=SourceFormat.DXF_ENTITY,
                sheet_ref="modelspace",
                entity_ref=handle,
                layer="WALL",
            ),
        ),
        layer="WALL",
    )


F_A = wall_face((0.0, 0.0), (1000.0, 0.0), "AA")
F_B = wall_face((0.0, 200.0), (1000.0, 200.0), "AB")

CONFIRMED = ScaleCalibration(
    sheet_id="modelspace",
    status=ScaleCalibrationStatus.CONFIRMED,
    units_per_drawing_unit=Decimal("1.0"),
    method="detected_from_dxf_units",
)
UNCONFIRMED = ScaleCalibration(
    sheet_id="modelspace",
    status=ScaleCalibrationStatus.PROPOSED,
    units_per_drawing_unit=Decimal("1.0"),
    method="detected_from_dxf_units",
)


class TestScaleGate:
    def test_unconfirmed_scale_blocks_all_measurement(self) -> None:
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=UNCONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        assert out.measurements == (), "nothing may be measured without confirmed scale"
        assert len(out.exceptions) == 1
        exc = out.exceptions[0]
        assert exc.code.value == "scale_unconfirmed"
        assert exc.severity is ExceptionSeverity.BLOCKING
        assert exc.sheet_id == "modelspace"

    def test_unknown_scale_status_also_blocks(self) -> None:
        unknown = ScaleCalibration(
            sheet_id="modelspace",
            status=ScaleCalibrationStatus.UNKNOWN,
            units_per_drawing_unit=None,
        )
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=unknown,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        assert out.measurements == ()
        assert out.exceptions[0].code.value == "scale_unconfirmed"


class TestWallMeasurements:
    def test_confirmed_scale_measures_walls(self) -> None:
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=CONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        assert not out.exceptions
        lengths = [m for m in out.measurements if m.quantity_type is QuantityType.LENGTH]
        areas = [m for m in out.measurements if m.quantity_type is QuantityType.AREA]
        assert len(lengths) == 1
        assert len(areas) == 1

        length = lengths[0]
        # 1000 mm → 1.000000 m, banker's-rounded to 6 places
        assert length.value == Decimal("1.000000")
        assert length.unit is MeasurementUnit.M
        assert length.state is MeasurementState.MEASURED
        assert length.rule_id == "wall.centerline.length.v1"
        assert length.engine_version == out.engine_version
        # replay contract
        assert len(length.inputs_digest) == 64  # sha256 hex
        assert length.inputs == ("AA", "AB")
        # evidence invariant: >=1 link highlightable in the viewer
        assert length.evidence
        assert all(e.kind == "geometry" for e in length.evidence)
        # wall-specific derived data for the evidence panel
        assert length.thickness == pytest.approx(200.0)
        assert length.label == "Wall 1"

    def test_footprint_area_derived(self) -> None:
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=CONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        areas = [m for m in out.measurements if m.quantity_type is QuantityType.AREA]
        area = areas[0]
        # 1000mm x 200mm = 200,000 mm² = 0.2 m²
        assert area.value == Decimal("0.200000")
        assert area.unit is MeasurementUnit.M2
        assert area.rule_id == "wall.footprint.area.v1"
        assert area.label == "Wall 1 footprint"

    def test_two_walls_two_lengths(self) -> None:
        c = wall_face((0.0, 500.0), (1000.0, 500.0), "EE")
        d = wall_face((0.0, 700.0), (1000.0, 700.0), "FF")
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B, c, d],
            calibration=CONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        lengths = [m for m in out.measurements if m.quantity_type is QuantityType.LENGTH]
        assert len(lengths) == 2
        assert {m.label for m in lengths} == {"Wall 1", "Wall 2"}


class TestExceptionSurfacing:
    def test_self_intersecting_polygon_surfaces_exception(self) -> None:
        # a closed bowtie polygon: the kernel refuses it → exception surfaces
        bowtie = NormalizedGeometry(
            geom_type=GeomType.POLYGON,
            coordinates=[
                (0.0, 0.0),
                (2.0, 2.0),
                (2.0, 0.0),
                (0.0, 2.0),
                (0.0, 0.0),
            ],
            source_format=SourceFormat.DXF_ENTITY,
            source_handles=(
                SourceHandleRef(
                    format=SourceFormat.DXF_ENTITY,
                    sheet_ref="modelspace",
                    entity_ref="BT1",
                    layer="WALL",
                ),
            ),
            layer="WALL",
        )
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B, bowtie],
            calibration=CONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        codes = {e.code.value for e in out.exceptions}
        assert "self_intersecting" in codes
        # the wall still measured; the bowtie was surfaced, not swallowed
        lengths = [m for m in out.measurements if m.quantity_type is QuantityType.LENGTH]
        assert len(lengths) == 1


class TestDeterminism:
    def test_same_inputs_identical_outputs(self) -> None:
        outs = [
            measure_sheet(
                sheet_id="modelspace",
                geometries=[F_A, F_B],
                calibration=CONFIRMED,
                drawing_units="mm",
            max_wall_thickness=250,
            )
            for _ in range(3)
        ]
        base = outs[0]
        for other in outs[1:]:
            assert other.measurements == base.measurements
            assert other.exceptions == base.exceptions

    def test_inputs_digest_is_stable_across_runs(self) -> None:
        outs = [
            measure_sheet(
                sheet_id="modelspace",
                geometries=[F_A, F_B],
                calibration=CONFIRMED,
                drawing_units="mm",
            max_wall_thickness=250,
            )
            for _ in range(2)
        ]
        d1 = [m.inputs_digest for m in outs[0].measurements]
        d2 = [m.inputs_digest for m in outs[1].measurements]
        assert d1 == d2


class TestRunOutputElements:
    """Round 4 persistence contract: measurements bind to element records.

    RunOutput.elements carries one ElementRecord per detected wall and each
    measurement's element_index points into it. The binding is additive —
    the replay identity (inputs_digest -> uuid5 measurement_id) never includes
    element_index, so Round 3 determinism pins are unaffected.
    """

    def test_elements_present_and_bound(self) -> None:
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=CONFIRMED,
            drawing_units="mm",
            max_wall_thickness=250,
        )
        assert len(out.elements) == 1
        assert out.measurements, "a wall must be measured"
        for m in out.measurements:
            assert m.element_index == 0
            assert out.elements[m.element_index].element_type.value == "wall"

    def test_blocked_sheet_has_no_elements(self) -> None:
        out = measure_sheet(
            sheet_id="modelspace",
            geometries=[F_A, F_B],
            calibration=UNCONFIRMED,
            drawing_units="mm",
        )
        assert out.elements == ()
        assert out.measurements == ()
        assert out.exceptions[0].code == "scale_unconfirmed"  # StrEnum compares equal

    def test_element_binding_never_feeds_replay_digest(self) -> None:
        outs = [
            measure_sheet(
                sheet_id="modelspace",
                geometries=[F_A, F_B],
                calibration=CONFIRMED,
                drawing_units="mm",
                max_wall_thickness=250,
            )
            for _ in range(2)
        ]
        ids0 = [m.measurement_id for m in outs[0].measurements]
        ids1 = [m.measurement_id for m in outs[1].measurements]
        assert ids0 == ids1, "identity must stay content-bound without element_index"
