"""T042 — wall detection tests: parallel-pair → WallCandidate, overlaps.

These tests pin the V1 deterministic detector:
  * two parallel wall faces at offset t → one wall with centerline
    midpoints, thickness t,
  * non-parallel / non-offset / collinear pairs are NOT walls,
  * an edge already consumed is reported as overlap (never silently dropped),
  * unmatched edges are listed,
  * layer filtering: only WALL-ish layers by default; any layer if disabled,
  * stable result order regardless of input order,
  * wall_footprint ring is closed, valid, and carries the full handle chain.
"""
from __future__ import annotations

import pytest

from core.domain.enums import GeomType, SourceFormat
from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.wall_detection import detect_walls, extract_segs, wall_footprint


def edge(
    a: tuple[float, float],
    b: tuple[float, float],
    handle: str = "H1",
    layer: str = "WALL",
) -> NormalizedGeometry:
    return NormalizedGeometry(
        geom_type=GeomType.POLYLINE,
        coordinates=[a, b],
        source_format=SourceFormat.DXF_ENTITY,
        source_handles=(
            SourceHandleRef(
                format=SourceFormat.DXF_ENTITY,
                sheet_ref="modelspace",
                entity_ref=handle,
                layer=layer,
            ),
        ),
        layer=layer,
    )


# one wall: two parallel faces, thickness 200, length 1000
F_A = edge((0.0, 0.0), (1000.0, 0.0), handle="AA")
F_B = edge((0.0, 200.0), (1000.0, 200.0), handle="AB")


class TestDetectWalls:
    def test_parallel_pair_becomes_wall(self) -> None:
        result = detect_walls([F_A, F_B], max_thickness=250)
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.thickness == pytest.approx(200.0)
        assert wall.length == pytest.approx(1000.0)
        # centerline endpoints = midpoints between the faces
        (x0, y0), (x1, y1) = wall.centerline
        assert (x0, y0) == pytest.approx((0.0, 100.0))
        assert (x1, y1) == pytest.approx((1000.0, 100.0))

    def test_detection_is_order_independent(self) -> None:
        r1 = detect_walls([F_A, F_B], max_thickness=250)
        r2 = detect_walls([F_B, F_A], max_thickness=250)
        assert len(r1.walls) == len(r2.walls) == 1
        assert r1.walls[0].centerline == r2.walls[0].centerline
        assert r1.walls[0].thickness == r2.walls[0].thickness

    def test_non_parallel_pair_not_wall(self) -> None:
        s2 = edge((0.0, 200.0), (1000.0, 300.0), handle="BB")
        result = detect_walls([F_A, s2], max_thickness=250)
        assert result.walls == []
        assert len(result.unmatched_edges) == 2

    def test_collinear_pair_not_wall(self) -> None:
        # same line continued: distance 0 → not a wall
        s2 = edge((1000.0, 0.0), (2000.0, 0.0), handle="CC")
        result = detect_walls([F_A, s2], max_thickness=250)
        assert result.walls == []

    def test_offset_varies_pair_not_wall(self) -> None:
        # one end 200 away, the other 400 away → not parallel faces of one wall
        s2 = edge((0.0, 200.0), (1000.0, 400.0), handle="DD")
        result = detect_walls([F_A, s2], max_thickness=250)
        assert result.walls == []

    def test_two_walls_four_edges(self) -> None:
        c = edge((0.0, 500.0), (1000.0, 500.0), handle="EE")
        d = edge((0.0, 700.0), (1000.0, 700.0), handle="FF")
        result = detect_walls([F_A, F_B, c, d], max_thickness=250)
        assert len(result.walls) == 2
        assert result.unmatched_edges == []
        thicknesses = sorted(w.thickness for w in result.walls)
        assert thicknesses == pytest.approx([200.0, 200.0])

    def test_layer_filtering_default_wall_only(self) -> None:
        # same pair on a non-wall layer is ignored by default
        a = edge((0.0, 0.0), (1000.0, 0.0), handle="GG", layer="DIM")
        b = edge((0.0, 200.0), (1000.0, 200.0), handle="HH", layer="DIM")
        result = detect_walls([a, b], max_thickness=250)
        assert result.walls == []
        assert result.unmatched_edges == []

    def test_layer_filtering_disabled_pairs_any_layer(self) -> None:
        a = edge((0.0, 0.0), (1000.0, 0.0), handle="GG", layer="DIM")
        b = edge((0.0, 200.0), (1000.0, 200.0), handle="HH", layer="DIM")
        result = detect_walls([a, b], max_thickness=250, wall_layers_only=False)
        assert len(result.walls) == 1

    def test_layer_hints_case_insensitive(self) -> None:
        # "Wall-External" contains "wall" case-insensitively → eligible
        a = edge((0.0, 0.0), (1000.0, 0.0), handle="II", layer="Wall-External")
        b = edge((0.0, 200.0), (1000.0, 200.0), handle="JJ", layer="Wall-External")
        result = detect_walls([a, b], max_thickness=250)
        assert len(result.walls) == 1

    def test_zero_length_edge_never_pairs(self) -> None:
        # a degenerate edge is never a wall face: unit() raises internally and
        # the pair test returns False; the edge lands in unmatched, not walls.
        z = edge((5.0, 5.0), (5.0, 5.0), handle="ZZ")
        result = detect_walls([z, F_B], max_thickness=250)
        assert result.walls == []
        assert "ZZ" in result.unmatched_edges


class TestOverlaps:
    def test_consumed_edge_reported_as_overlap(self) -> None:
        # three mutually parallel edges at equal spacing: OA-OB pair first; OB is
        # then consumed, so OB-OC can never pair → overlap recorded when it tries
        e1 = edge((0.0, 0.0), (1000.0, 0.0), handle="OA")
        e2 = edge((0.0, 200.0), (1000.0, 200.0), handle="OB")
        e3 = edge((0.0, 400.0), (1000.0, 400.0), handle="OC")
        result = detect_walls([e1, e2, e3], max_thickness=250)
        # OA-OB and OB-OC are both valid pairs but OB is consumed by the first
        assert result.walls == []
        assert result.overlaps != []


class TestFootprint:
    def test_footprint_is_closed_rectangle(self) -> None:
        result = detect_walls([F_A, F_B], max_thickness=250)
        fp = wall_footprint(result.walls[0])
        assert fp.geom_type is GeomType.POLYGON
        ring = fp.coordinates
        assert len(ring) == 5  # 4 corners + explicit closure
        assert ring[0] == ring[-1]
        # handles of BOTH faces survive on the derived geometry
        refs = {h.entity_ref for h in fp.source_handles}
        assert {"AA", "AB"} <= refs

    def test_footprint_area_matches_length_times_thickness(self) -> None:
        from takeoff.kernel import area_of

        result = detect_walls([F_A, F_B], max_thickness=250)
        fp = wall_footprint(result.walls[0])
        assert area_of(fp) == pytest.approx(1000.0 * 200.0)


class TestExtractSegs:
    def test_only_two_vertex_polylines_are_edges(self) -> None:
        three = edge((0.0, 0.0), (500.0, 0.0), handle="T1")
        three = NormalizedGeometry(
            geom_type=GeomType.POLYLINE,
            coordinates=[(0.0, 0.0), (500.0, 0.0), (500.0, 300.0)],
            source_format=SourceFormat.DXF_ENTITY,
            source_handles=three.source_handles,
            layer="WALL",
        )
        segs = extract_segs([F_A, three])
        assert len(segs) == 1  # the 3-vertex polyline is not a pairing candidate


class TestOverlapWindowPairing:
    """Engine 0.8.0 — junction-splitting: walls form over the drawn
    intersection of parallel offset faces; leftover spans are fragments,
    never walls; contested spans refuse all claimants together."""

    def test_l_corner_measures_both_walls_over_drawn_spans(self) -> None:
        # Real CAD convention: outer faces meet at the corner; inner faces
        # stop at it — non-congruent face pairs the 0.7.0 engine refused.
        bottom_full = edge((0.0, 0.0), (6000.0, 0.0), handle="L1")
        top_short = edge((0.0, 200.0), (5800.0, 200.0), handle="L2")
        left_inner = edge((5800.0, 200.0), (5800.0, 5000.0), handle="L3")
        right_full = edge((6000.0, 0.0), (6000.0, 5000.0), handle="L4")
        result = detect_walls([bottom_full, top_short, left_inner, right_full],
                              max_thickness=250)
        assert len(result.walls) == 2
        by_len = sorted(result.walls, key=lambda w: -w.length)
        assert by_len[0].length == pytest.approx(5800.0)  # horizontal window
        assert by_len[1].length == pytest.approx(4800.0)  # vertical window
        # The 200mm corner nubs are NOT walls and NOT silently dropped:
        assert result.unmatched_edges == []

    def test_split_face_doorway_yields_two_walls_no_phantom(self) -> None:
        # One continuous face beside two fragments: two disjoint windows,
        # each a wall; the doorway span is nobody's wall.
        cont = edge((0.0, 0.0), (6000.0, 0.0), handle="C1")
        f1 = edge((0.0, 200.0), (3000.0, 200.0), handle="C2")
        f2 = edge((4200.0, 200.0), (6000.0, 200.0), handle="C3")
        result = detect_walls([cont, f1, f2], max_thickness=250)
        assert len(result.walls) == 2
        lens = sorted(w.length for w in result.walls)
        assert lens == [pytest.approx(1800.0), pytest.approx(3000.0)]
        # Continuous face participated in both windows (disjoint spans) —
        # no conflict, no phantom wall over the 1200 doorway span.

    def test_contested_span_refuses_all_claimants_together(self) -> None:
        # Two fragments overlapping the SAME span of the continuous face:
        # each fragment's window covers the other's span on the shared face
        # → genuine ambiguity → both refused, order-independent.
        cont = edge((0.0, 0.0), (6000.0, 0.0), handle="K1")
        f1 = edge((1000.0, 200.0), (5000.0, 200.0), handle="K2")
        f2 = edge((2000.0, 200.0), (6000.0, 200.0), handle="K3")
        a = detect_walls([cont, f1, f2], max_thickness=250)
        b = detect_walls([f2, cont, f1], max_thickness=250)
        assert a.walls == []
        assert a == b

    def test_disjoint_windows_on_shared_face_coexist(self) -> None:
        # Same continuous face, two fragments in DISJOINT spans: no overlap
        # on the shared face → both windows are walls (the doorway case).
        cont = edge((0.0, 0.0), (6000.0, 0.0), handle="D1")
        f1 = edge((0.0, 200.0), (2000.0, 200.0), handle="D2")
        f2 = edge((4000.0, 200.0), (6000.0, 200.0), handle="D3")
        result = detect_walls([cont, f1, f2], max_thickness=250)
        assert len(result.walls) == 2

    def test_window_never_extends_beyond_either_face(self) -> None:
        # The wall's length is exactly the drawn intersection — the 500mm
        # undrawn span of the longer face is never measured.
        short = edge((0.0, 0.0), (5000.0, 0.0), handle="S1")
        long_face = edge((0.0, 200.0), (6000.0, 200.0), handle="S2")
        result = detect_walls([short, long_face], max_thickness=250)
        assert len(result.walls) == 1
        assert result.walls[0].length == pytest.approx(5000.0)

    def test_truncated_faces_replay_from_own_inputs(self) -> None:
        from takeoff.rules import run_rule

        # The wall's edge_geometries are the WINDOW faces — replay computes
        # the window length from them alone (no hidden span constants).
        short = edge((0.0, 0.0), (5000.0, 0.0), handle="R1")
        long_face = edge((0.0, 200.0), (6000.0, 200.0), handle="R2")
        wall = detect_walls([short, long_face], max_thickness=250).walls[0]
        assert run_rule("wall.centerline.length.v1",
                       list(wall.edge_geometries)) == pytest.approx(5000.0)

    def test_sliver_alignment_is_not_a_wall(self) -> None:
        # A 2-unit fragment beside a 6000-unit face: coincidental touch,
        # below the pairing ratio bound — never a wall.
        long_face = edge((0.0, 0.0), (6000.0, 0.0), handle="P1")
        sliver = edge((100.0, 200.0), (102.0, 200.0), handle="P2")
        result = detect_walls([long_face, sliver], max_thickness=250)
        assert result.walls == []
