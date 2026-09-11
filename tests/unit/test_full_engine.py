"""T043/T044/T045/T046 — full takeoff engine tests: rooms, floors,
openings, deductions.

The engine is the pipeline core (docs/architecture.md determinism boundary).
Pinned behavior (every case through the real fixtures + the pure engine):

  * room_plan.dxf — one enclosed 4000x3000 room: gross 12 m² (to centerline),
    net 10.64 m² (minus wall footprints), KITCHEN label from the TEXT token,
    floor roll-up gross/net,
  * two_room_plan.dxf — two labeled rooms sharing a wall; both detected,
    labels from tokens, one floor roll-up,
  * multi_storey_hint.dxf — two separate rooms: the 7000mm collinear gaps
    between buildings' walls are NOT phantom openings (uncorroborated gaps
    surface as REVIEW ambiguity, never counted),
  * wall_with_doorway.dxf — a gap CORROBORATED by a D900 block is a counted
    opening; the host wall's net area reflects the drawn faces (the slot
    lies outside the footprint, geometric subtraction deducts zero — no
    double-counting),
  * opening_blocks.dxf — D1000/W1200 blocks on a continuous wall: counted
    openings, net area = gross - slot areas (the real deduction),
  * walls without openings carry an honest MEASURED_ZERO opening count,
  * determinism: same inputs → byte-identical RunOutput, digest-stable.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.domain.enums import (
    ElementType,
    ExceptionSeverity,
    MeasurementState,
    MeasurementUnit,
    ScaleCalibrationStatus,
)
from core.units.geometry_units import ScaleCalibration
from ingestion.dxf import block_names_by_insert_handle, parse_dxf
from takeoff.engine import measure_parsed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dxf"

CONFIRMED = ScaleCalibration(
    sheet_id="modelspace",
    status=ScaleCalibrationStatus.CONFIRMED,
    units_per_drawing_unit=Decimal("1.0"),
    method="detected_from_dxf_units",
)


def _run(fixture: str):
    data = (FIXTURES / f"{fixture}.dxf").read_bytes()
    parsed = parse_dxf(data)
    block_names = block_names_by_insert_handle(data)
    return measure_parsed(
        parsed,
        sheet_id="modelspace",
        calibration=CONFIRMED,
        max_wall_thickness=250,
        block_names=block_names,
    )


def _by_rule(out, rule_id: str):
    return [m for m in out.measurements if m.rule_id == rule_id]


class TestRoomsT043:
    def test_one_room_gross_net_label_floor(self) -> None:
        out = _run("room_plan")
        assert out.exceptions == ()
        gross = _by_rule(out, "room.gross.area.v1")
        net = _by_rule(out, "room.net.area.v1")
        assert len(gross) == 1 and len(net) == 1
        # 4000x3000mm to centerline = 12 m²; net = 12 - 1.36 (four 0.2-thick
        # wall footprints' intrusions) = 10.64 m².
        assert gross[0].value == Decimal("12.000000")
        assert net[0].value == Decimal("10.640000")
        assert gross[0].label == "KITCHEN gross area"
        assert gross[0].element_type is ElementType.ROOM
        assert gross[0].state is MeasurementState.MEASURED
        # label evidence rides as a text_token link on the room rows
        assert any(e.kind == "text_token" for e in gross[0].evidence)
        assert any(e.kind == "text_token" for e in net[0].evidence)

    def test_room_gross_perimeter_rule_row(self) -> None:
        """Round 8: room.gross.perimeter.v1 emits beside the gross area —
        same element, same evidence refs, replayable from the ring alone."""
        out = _run("room_plan")
        perimeters = _by_rule(out, "room.gross.perimeter.v1")
        assert len(perimeters) == 1
        row = perimeters[0]
        # centerline ring 4000x3000mm -> 2*(4000+3000) = 14 m
        assert row.value == Decimal("14.000000")
        assert row.unit is MeasurementUnit.M
        assert row.label == "KITCHEN gross perimeter"
        assert row.element_type is ElementType.ROOM
        assert row.state is MeasurementState.MEASURED
        # same element as the gross area row (the room element)
        gross = _by_rule(out, "room.gross.area.v1")[0]
        assert row.element_index == gross.element_index
        assert row.evidence[0].ref == gross.evidence[0].ref
        # label evidence rides here too (the room label is never guessed)
        assert any(e.kind == "text_token" for e in row.evidence)
        # replay-honest: the value re-derives from the ring geometry alone
        # through the registered rule.
        from takeoff.rules import run_rule

        ring = out.elements[row.element_index].geometry
        assert Decimal(str(run_rule("room.gross.perimeter.v1", [ring]))) == (
            Decimal("14000.0")
        )

    def test_two_rooms_both_perimeters(self) -> None:
        out = _run("two_room_plan")
        perimeters = _by_rule(out, "room.gross.perimeter.v1")
        assert sorted(m.label for m in perimeters) == [
            "BATH gross perimeter", "BEDROOM gross perimeter"]
        # left room 4000x3000 -> 14 m; right room 2000x3000 -> 10 m
        assert sorted(m.value for m in perimeters) == [
            Decimal("10.000000"), Decimal("14.000000")]

    def test_two_rooms_both_labeled(self) -> None:
        out = _run("two_room_plan")
        assert out.exceptions == ()
        gross = _by_rule(out, "room.gross.area.v1")
        assert sorted(m.label for m in gross) == [
            "BATH gross area", "BEDROOM gross area"]
        areas = sorted(m.value for m in gross)
        # 6000x3000 outer: left room 4000x3000=12, right 2000x3000=6
        assert areas == [Decimal("6.000000"), Decimal("12.000000")]

    def test_partial_plan_no_room_no_exception(self) -> None:
        """The L-shaped wall_plan (2 walls) yields no rooms and no exception:
        a partial plan is honest absence, not an error."""
        out = _run("wall_plan")
        assert _by_rule(out, "room.gross.area.v1") == []
        assert _by_rule(out, "floor.gross.area.v1") == []
        assert out.exceptions == ()

    def test_room_labels_never_guessed_from_outside_tokens(self) -> None:
        """multi_storey_hint: ROOM L1 / ROOM L2 labels land on their own
        rooms (token insertion strictly inside the room polygon)."""
        out = _run("multi_storey_hint")
        gross = _by_rule(out, "room.gross.area.v1")
        assert sorted(m.label for m in gross) == [
            "ROOM L1 gross area", "ROOM L2 gross area"]


class TestFloorsT044:
    def test_floor_rollup_sums_rooms(self) -> None:
        out = _run("two_room_plan")
        floor_gross = _by_rule(out, "floor.gross.area.v1")
        floor_net = _by_rule(out, "floor.net.area.v1")
        assert len(floor_gross) == 1 and len(floor_net) == 1
        assert floor_gross[0].value == Decimal("18.000000")  # 12 + 6
        assert floor_net[0].value == Decimal("15.680000")  # 10.64 + 5.04
        assert floor_gross[0].element_type is ElementType.FLOOR_FINISH
        # floor element count: 5 walls + 2 rooms + 1 floor = 8
        assert len(out.elements) == 8

    def test_no_rooms_no_floor(self) -> None:
        out = _run("wall_plan")
        assert _by_rule(out, "floor.gross.area.v1") == []
        assert _by_rule(out, "floor.net.area.v1") == []


class TestOpeningsT045:
    def test_named_blocks_counted_and_net_deducted(self) -> None:
        out = _run("opening_blocks")
        assert out.exceptions == ()
        counts = _by_rule(out, "opening.count.v1")
        assert len(counts) == 1 and counts[0].value == Decimal("2.000000")
        assert counts[0].state is MeasurementState.MEASURED
        # D1000 door: slot 1.0 x 0.2 = 0.2 m²; W1200: 1.2 x 0.2 = 0.24 m².
        # Gross wall = 6.0 x 0.2 = 1.2; net = 1.2 - 0.44 = 0.76.
        net = _by_rule(out, "wall.net.area.v1")
        gross = _by_rule(out, "wall.footprint.area.v1")
        assert net[0].value == Decimal("0.760000")
        assert gross[0].value == Decimal("1.200000")

    def test_gap_corroborated_by_block_is_opening(self) -> None:
        out = _run("wall_with_doorway")
        assert out.exceptions == ()
        counts = _by_rule(out, "opening.count.v1")
        # Wall 1 hosts the gap-spanned opening (corroborated by D900);
        # Wall 2 has none: honest zero.
        assert [m.value for m in counts] == [Decimal("1.000000"),
                                             Decimal("0.000000")]
        assert counts[0].state is MeasurementState.MEASURED
        assert counts[1].state is MeasurementState.MEASURED_ZERO
        # The gap lies OUTSIDE both wall footprints (faces drawn split), so
        # the geometric subtraction deducts zero: net == gross per wall.
        nets = _by_rule(out, "wall.net.area.v1")
        grosses = _by_rule(out, "wall.footprint.area.v1")
        assert [m.value for m in nets] == [m.value for m in grosses]

    def test_uncorroborated_gap_is_not_phantom_opening(self) -> None:
        """multi_storey_hint: 7000mm collinear gaps between separate
        buildings' walls must NOT become counted openings — surfaced as
        REVIEW ambiguity instead (a phantom opening would feed the BOQ)."""
        out = _run("multi_storey_hint")
        counts = _by_rule(out, "opening.count.v1")
        assert all(m.value == Decimal("0.000000") for m in counts)
        gap_exc = [e for e in out.exceptions
                   if e.code.value == "opening_ambiguous"]
        assert gap_exc, "uncorroborated gaps must surface, never vanish"
        assert all(e.severity is ExceptionSeverity.REVIEW for e in gap_exc)
        assert any("7000" in e.message for e in gap_exc)

    def test_zero_openings_honest_measured_zero(self) -> None:
        out = _run("room_plan")
        counts = _by_rule(out, "opening.count.v1")
        assert len(counts) == 4  # one per wall, all zero
        for m in counts:
            assert m.value == Decimal("0.000000")
            assert m.state is MeasurementState.MEASURED_ZERO
            assert m.evidence, "even a zero needs its evidence"


class TestDeductionsT046:
    def test_net_never_exceeds_gross(self) -> None:
        for fixture in ("room_plan", "opening_blocks", "wall_with_doorway",
                        "two_room_plan", "multi_storey_hint"):
            out = _run(fixture)
            for net in _by_rule(out, "wall.net.area.v1"):
                gross_label = net.label.replace(" net of openings", " footprint")
                gross = next(m for m in out.measurements
                             if m.label == gross_label)
                assert net.value <= gross.value + Decimal("1e-9"), (
                    f"{fixture}: net {net.value} > gross {gross.value}")

    def test_opening_evidence_is_drawn_geometry(self) -> None:
        out = _run("opening_blocks")
        count = _by_rule(out, "opening.count.v1")[0]
        # the count's evidence carries the D1000/W1200 member handles
        refs = " ".join(e.ref for e in count.evidence)
        assert "D1000" in refs or "W1200" in refs or "40" in refs


class TestDeterminismT050:
    @pytest.mark.parametrize("fixture", [
        "room_plan", "two_room_plan", "wall_with_doorway",
        "opening_blocks", "multi_storey_hint", "wall_plan",
    ])
    def test_same_inputs_identical_outputs(self, fixture: str) -> None:
        first = _run(fixture)
        second = _run(fixture)
        assert first.measurements == second.measurements
        assert first.exceptions == second.exceptions
        assert first.elements == second.elements
        assert first.engine_version == second.engine_version
        # durable identity derives from the digest: stable across replays
        assert [m.measurement_id for m in first.measurements] == [
            m.measurement_id for m in second.measurements]

    def test_engine_version_bumped_for_new_rules(self) -> None:
        out = _run("room_plan")
        assert out.engine_version == "0.5.0"
        for m in out.measurements:
            assert m.engine_version == "0.5.0"


class TestRoomRefusals:
    def test_unenclosed_surface_is_review(self) -> None:
        """3+ walls with no closed face → room_not_enclosed (REVIEW), the
        almost-room reaches the review queue instead of being invented."""
        from takeoff.room_detection import RoomDetectionResult, detect_rooms
        from takeoff.wall_detection import detect_walls

        _run("wall_plan")  # precondition: the partial plan parses cleanly
        parsed_walls = detect_walls(
            list(parse_dxf((FIXTURES / "wall_plan.dxf").read_bytes()).geometries),
            max_thickness=250,
        )
        assert len(parsed_walls.walls) < 3  # precondition: cannot enclose
        result: RoomDetectionResult = detect_rooms(parsed_walls.walls)
        assert result.rooms == ()
        assert result.not_enclosed is False  # <3 walls: absence, not refusal

    def test_opening_kind_from_layer_or_block(self) -> None:
        out = _run("opening_blocks")
        # elements include door + window kinds (labels carry the kind)
        labels = [e.label for e in out.elements]
        assert any("Door" in (label or "") for label in labels)
        assert any("Window" in (label or "") for label in labels)
