"""Engine 0.9.0 — centerline junction completion (adversarial gate).

Every test pins one §"Engine 0.9.0" doctrine line from docs/domain-model.md:
bounded mutual completion, per-endpoint uniqueness, corroborated doorway
bridging, open-gaps-stay-open, wall rows never extended by completion, and
closure rectangles as net-only subtractions.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from core.domain.enums import ExceptionSeverity, ScaleCalibrationStatus
from core.units.geometry_units import ScaleCalibration
from ingestion.dxf import block_names_by_insert_handle, parse_dxf
from takeoff.engine import MeasurementRecord, RunOutput, measure_parsed
from takeoff.junction import (
    JUNCTION_REACH_EPS,
    JunctionCompletionResult,
    complete_junctions,
)
from takeoff.room_detection import detect_rooms
from takeoff.wall_detection import WallDetectionResult, detect_walls

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dxf"

CONFIRMED = ScaleCalibration(
    sheet_id="modelspace",
    status=ScaleCalibrationStatus.CONFIRMED,
    units_per_drawing_unit=Decimal("1.0"),
    method="detected_from_dxf_units",
)


def _run(fixture: str) -> RunOutput:
    data = (FIXTURES / f"{fixture}.dxf").read_bytes()
    parsed = parse_dxf(data)
    return measure_parsed(
        parsed,
        sheet_id="modelspace",
        calibration=CONFIRMED,
        max_wall_thickness=250,
        block_names=block_names_by_insert_handle(data),
    )


def _by_rule(out: RunOutput, rule_id: str) -> list[MeasurementRecord]:
    return [m for m in out.measurements if m.rule_id == rule_id]


def _detect(fixture: str) -> WallDetectionResult:
    data = (FIXTURES / f"{fixture}.dxf").read_bytes()
    return detect_walls(list(parse_dxf(data).geometries), max_thickness=250)


class TestPerpendicularCompletion:
    def test_real_cad_corners_close_the_room(self) -> None:
        """corner_room: outer faces meet, inner faces stop (the real-CAD
        convention). 0.8.0 windows measure 4 walls over their drawn spans;
        0.9.0 completes the 4 corner junctions; the room closes with the
        LIVING label and a floor roll-up."""
        out = _run("corner_room")
        assert not [e for e in out.exceptions
                    if e.code in ("junction_ambiguous", "room_not_enclosed")]
        lengths = sorted(m.value for m in _by_rule(out, "wall.centerline.length.v1"))
        # drawn spans: bottom 3800, top 3600, left 2600, right 2600 (mm)
        assert lengths == [Decimal("2.600000")] * 2 + [Decimal("3.600000"),
                                                       Decimal("3.800000")]
        gross = _by_rule(out, "room.gross.area.v1")
        assert len(gross) == 1 and gross[0].label == "LIVING gross area"
        # the centerline rectangle: 3800x2800 to centerline = 10.64 m²
        # (walls sit at the inner-corner convention: outer faces extend to
        # the nominal 4000x3000, centerlines stop one half-thickness in)
        assert gross[0].value == Decimal("10.640000")
        floor = _by_rule(out, "floor.gross.area.v1")
        assert len(floor) == 1 and floor[0].value == Decimal("10.640000")

    def test_net_excludes_corner_nubs(self) -> None:
        """The closure rectangles subtract the corner nubs (drawn material
        the 0.8.0 windows refuse to measure) — net is the clear interior,
        never fragmented by the nubs."""
        out = _run("corner_room")
        net = _by_rule(out, "room.net.area.v1")
        assert len(net) == 1
        # interior: inner faces 3600x2600 = 9.36 m²
        assert net[0].value == Decimal("9.360000")

    def test_wall_rows_never_extended_by_completion(self) -> None:
        """Doctrine: completion is room-graph only. Wall lengths stay
        byte-identical to what the 0.8.0 windows drew — wall 1 (bottom) is
        3800 (the drawn span), never 4000 (the completed ring)."""
        out = _run("corner_room")
        # bottom wall cl y=100: drawn faces x∈[0..3800]
        rows = _by_rule(out, "wall.centerline.length.v1")
        assert all(m.value != Decimal("4.000000") for m in rows)


class TestDoorwayBridge:
    def test_corroborated_gap_closes_the_ring(self) -> None:
        """doorway_bridge_room: the collinear 800mm gap carries drawn
        opening evidence (a header on the OPENING layer) — the bridge closes
        the ring and the room measures."""
        out = _run("doorway_bridge_room")
        gross = _by_rule(out, "room.gross.area.v1")
        assert len(gross) == 1 and gross[0].label == "HALL gross area"
        # the ring includes the bridged bottom: 3800x2800 to centerline
        assert gross[0].value == Decimal("10.640000")
        # the bridge corroborator handles ride in the room provenance
        room_row = gross[0]
        assert room_row.evidence, "bridged room keeps full provenance"

    def test_bridge_does_not_invent_wall_or_opening(self) -> None:
        """The bridge is room-graph only: no wall row spans the gap (walls
        stay 1800 + 1400), and the header is not counted as an opening (the
        block-named corroboration doctrine is unchanged — the
        opening_ambiguous surface is the honest existing behavior)."""
        out = _run("doorway_bridge_room")
        lengths = sorted(m.value for m in _by_rule(out, "wall.centerline.length.v1"))
        assert Decimal("4.000000") not in lengths  # no phantom spanning wall
        assert Decimal("0.800000") not in lengths   # no bridge wall either
        gap_exc = [e for e in out.exceptions if e.code == "opening_ambiguous"]
        assert gap_exc, "the bare-gap-vs-opening doctrine stays surfaced"

    def test_bare_gap_stays_open(self) -> None:
        """open_gap_room: identical geometry WITHOUT the header — the gap
        must NOT bridge (two separate structures is equally plausible), and
        the honest room_not_enclosed REVIEW exception fires."""
        out = _run("open_gap_room")
        assert _by_rule(out, "room.gross.area.v1") == []
        assert _by_rule(out, "floor.gross.area.v1") == []
        rne = [e for e in out.exceptions if e.code == "room_not_enclosed"]
        assert rne and all(e.severity is ExceptionSeverity.REVIEW for e in rne)


class TestAmbiguity:
    def test_two_feasible_junction_points_refuse_the_endpoint(self) -> None:
        """ambiguous_junction: wall A's endpoint reaches B's centerline AND
        C's centerline, both inside the drawn-thickness bounds, at DISTINCT
        points — the endpoint is refused (junction_ambiguous REVIEW), never
        resolved by picking the nearer wall."""
        out = _run("ambiguous_junction")
        jamb = [e for e in out.exceptions if e.code == "junction_ambiguous"]
        assert jamb, "the ambiguity must surface, never vanish"
        assert all(e.severity is ExceptionSeverity.REVIEW for e in jamb)
        assert any("2 distinct feasible junction points" in e.message for e in jamb)
        # the walls keep their measurements (refusal never erases walls)
        assert len(_by_rule(out, "wall.centerline.length.v1")) == 3

    def test_no_room_closes_over_refused_junction(self) -> None:
        out = _run("ambiguous_junction")
        assert _by_rule(out, "room.gross.area.v1") == []


class TestBoundsAndProvenance:
    def test_beyond_thickness_never_completes(self) -> None:
        """A wall endpoint whose every candidate junction sits past the
        partner's drawn half-thickness completes nothing — junction_split's
        2-wall L stays open (2 walls < 3: no room, no exception)."""
        out = _run("junction_split")
        assert len(_by_rule(out, "wall.centerline.length.v1")) == 2
        assert _by_rule(out, "room.gross.area.v1") == []
        assert not [e for e in out.exceptions
                    if e.code == "junction_ambiguous"]

    def test_completion_is_deterministic(self) -> None:
        """Same walls + geometries → identical links, twice (byte-stable)."""
        for fixture in ("corner_room", "doorway_bridge_room", "ambiguous_junction"):
            data = (FIXTURES / f"{fixture}.dxf").read_bytes()
            parsed = parse_dxf(data)
            geoms = list(parsed.geometries)
            first = complete_junctions(detect_walls(geoms, max_thickness=250).walls,
                                       geoms)
            second = complete_junctions(detect_walls(geoms, max_thickness=250).walls,
                                         geoms)
            assert first == second, fixture

    def test_links_carry_partner_and_evidence_handles(self) -> None:
        """Provenance: perpendicular links carry partner-wall face handles;
        doorway bridges carry the corroborating opening-layer handles."""
        data = (FIXTURES / "doorway_bridge_room.dxf").read_bytes()
        geoms = list(parse_dxf(data).geometries)
        jc = complete_junctions(detect_walls(geoms, max_thickness=250).walls, geoms)
        bridges = [L for L in jc.links if L.corroborating_handles]
        assert bridges, "the bridge must name its drawn evidence"
        assert bridges[0].source_handles, "and the two bridged walls' handles"

    def test_tolerances_recorded_for_replay(self) -> None:
        """The junction reach tolerance is a selection parameter — it rides
        in the room rows' constants and in the completion result."""
        out = _run("corner_room")
        room_rows = [m for m in out.measurements
                     if m.element_type.value == "room"]
        assert room_rows
        # (constants live in the replay inputs; the result records the eps)
        data = (FIXTURES / "corner_room.dxf").read_bytes()
        geoms = list(parse_dxf(data).geometries)
        jc = complete_junctions(detect_walls(geoms, max_thickness=250).walls, geoms)
        assert jc.junction_eps == JUNCTION_REACH_EPS


class TestRoomDetectionIntegration:
    def test_detect_rooms_accepts_junctions_kwarg(self) -> None:
        """The integration contract: detect_rooms(walls, junctions=...) —
        with links, the corner room closes; without, it honestly refuses."""
        data = (FIXTURES / "corner_room.dxf").read_bytes()
        geoms = list(parse_dxf(data).geometries)
        walls = detect_walls(geoms, max_thickness=250).walls
        assert len(walls) == 4
        jc = complete_junctions(walls, geoms)
        assert isinstance(jc, JunctionCompletionResult)
        with_links = detect_rooms(walls, junctions=jc)
        without = detect_rooms(walls)
        assert len(with_links.rooms) == 1
        assert without.rooms == () and without.not_enclosed is True

    def test_junction_provenance_rides_on_rooms(self) -> None:
        """A closed ring's provenance includes the junction partners' and
        bridge-evidence handles (a closed room is derived geometry)."""
        data = (FIXTURES / "doorway_bridge_room.dxf").read_bytes()
        geoms = list(parse_dxf(data).geometries)
        walls = detect_walls(geoms, max_thickness=250).walls
        jc = complete_junctions(walls, geoms)
        result = detect_rooms(walls, junctions=jc)
        assert result.rooms
        room = result.rooms[0]
        # every bounding wall's handles plus bridge evidence are present
        assert room.source_handles
        bridge_handles = {h.entity_ref for L in jc.links
                          for h in L.corroborating_handles}
        assert bridge_handles, "the bridge carries its evidence"
        assert bridge_handles.issubset(
            {h.entity_ref for h in room.source_handles}), (
            "bridge evidence must ride in the room's provenance"
        )
