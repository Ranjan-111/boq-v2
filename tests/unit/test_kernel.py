"""T040 — takeoff kernel tests: deterministic primitives, refusal doctrine.

The kernel is the determinism boundary (docs/architecture.md): pure functions,
no I/O, no AI. These tests pin:
  * length/area math over drawing-unit coordinates,
  * the refusal doctrine: open rings, <4 vertices, self-intersecting rings,
    multi-polygons and <2-vertex polylines raise NotMeasurable — never guessed,
  * zero-length edges raise,
  * count semantics.
"""
from __future__ import annotations

import pytest

from core.domain.enums import GeomType, SourceFormat
from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.kernel import NotMeasurable, area_of, count_of, length_of


def geom(
    gtype: GeomType,
    coords: list[tuple[float, float]] | list[list[tuple[float, float]]],
) -> NormalizedGeometry:
    """Minimal geometry with one source handle (evidence-grade)."""
    return NormalizedGeometry(
        geom_type=gtype,
        coordinates=coords,
        source_format=SourceFormat.DXF_ENTITY,
        source_handles=(
            SourceHandleRef(
                format=SourceFormat.DXF_ENTITY,
                sheet_ref="modelspace",
                entity_ref="TEST1",
                layer="WALL",
            ),
        ),
        layer="WALL",
    )


class TestLength:
    def test_line_length(self) -> None:
        g = geom(GeomType.POLYLINE, [(0.0, 0.0), (3.0, 4.0)])
        assert length_of(g) == pytest.approx(5.0)

    def test_closed_polyline_length(self) -> None:
        # a closed ring's perimeter still works: 3-4-5 triangle closed
        g = geom(GeomType.POLYLINE, [(0.0, 0.0), (3.0, 0.0), (3.0, 4.0), (0.0, 0.0)])
        assert length_of(g) == pytest.approx(12.0)

    def test_multi_vertex_polyline(self) -> None:
        g = geom(GeomType.POLYLINE, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
        assert length_of(g) == pytest.approx(2.0)

    def test_fewer_than_2_vertices_refused(self) -> None:
        g = geom(GeomType.POLYLINE, [(0.0, 0.0)])
        with pytest.raises(NotMeasurable):
            length_of(g)

    def test_zero_length_segment_measures_zero(self) -> None:
        # degenerate but well-formed input: deterministic 0. The state
        # machinery downstream marks it MEASURED_ZERO (never faked, never
        # silently dropped); wall-pairing skips it at extraction instead.
        g = geom(GeomType.POLYLINE, [(1.0, 1.0), (1.0, 1.0)])
        assert length_of(g) == 0.0


class TestArea:
    def test_rectangle_area(self) -> None:
        g = geom(
            GeomType.POLYGON,
            [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0), (0.0, 0.0)],
        )
        assert area_of(g) == pytest.approx(24.0)

    def test_signed_ring_orientation_absolute(self) -> None:
        # CW ring: shoelace is negative, magnitude is the truth
        g = geom(
            GeomType.POLYGON,
            [(0.0, 0.0), (0.0, 4.0), (6.0, 4.0), (6.0, 0.0), (0.0, 0.0)],
        )
        assert area_of(g) == pytest.approx(24.0)

    def test_area_requires_polygon(self) -> None:
        g = geom(GeomType.POLYLINE, [(0.0, 0.0), (3.0, 4.0)])
        with pytest.raises(NotMeasurable):
            area_of(g)

    def test_open_ring_refused(self) -> None:
        g = geom(
            GeomType.POLYGON,
            [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)],  # not closed
        )
        with pytest.raises(NotMeasurable):
            area_of(g)

    def test_fewer_than_4_ring_points_refused(self) -> None:
        g = geom(GeomType.POLYGON, [(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)])
        with pytest.raises(NotMeasurable):
            area_of(g)

    def test_self_intersecting_bowtie_refused(self) -> None:
        # bowtie: two triangles sharing a vertex; |shoelace| would understate
        g = geom(
            GeomType.POLYGON,
            [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0), (0.0, 0.0)],
        )
        with pytest.raises(NotMeasurable):
            area_of(g)

    def test_multi_polygon_refused(self) -> None:
        ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        g = geom(GeomType.MULTI_POLYGON, [ring, ring])
        with pytest.raises(NotMeasurable):
            area_of(g)


class TestCount:
    def test_count(self) -> None:
        g1 = geom(GeomType.POLYLINE, [(0.0, 0.0), (1.0, 0.0)])
        g2 = geom(GeomType.POLYLINE, [(0.0, 0.0), (1.0, 1.0)])
        assert count_of([g1, g2]) == 2
        assert count_of([]) == 0
