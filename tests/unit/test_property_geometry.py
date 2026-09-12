"""T121 — property-based tests (hypothesis): geometry kernel invariants.

docs/testing-strategy.md §1: "Rounding: banker's rounding on money/quantity
edges via hypothesis property tests" and §pyramid: "Property-based
(hypothesis: geometry, rounding) — hunts edge cases we can't imagine."

Every property here is a MATHEMATICALLY TRUE statement about the kernel's
contracts (takeoff/kernel.py), tested by importing the real kernel — never a
reimplementation:

  * area_of: closed valid-ring polygon area is translation-invariant,
    invariant under vertex-order reversal (CW/CCW — kernel takes abs()), and
    >= 0.
  * length_of: polyline length is reversal-invariant and >= the endpoint
    distance (triangle inequality; equality for collinear orderings).
  * perimeter_of: closed-ring perimeter >= 2 * (max x-span + max y-span)/2
    — the honest, provable form is perimeter >= 2 * max(span) since a closed
    ring must cross the full span in both directions (see property docstring).
  * area_of == perimeter-compatible: a ring the area gate accepts, the
    perimeter gate accepts too, with identical refusal behavior (the Round 8
    docstring promise: "refuses ... through EXACTLY the same gate").
  * Refusal determinism: a kernel refusal (open ring, bowtie, <4 points,
    multi-polygon) is STABLE — the same refusal raises for the same input
    across calls, and the two gates (area/perimeter) refuse identically.
    A nondeterministic refusal would poison the exception rows.

Strategies generate VALID kernel inputs (closed rings with >=4 points,
explicit closing vertex, finite coordinates in float32-representable space)
plus adversarial malformed variants for the refusal properties.

Determinism: the hypothesis profile is registered in tests/conftest.py
(derandomized) — CI runs are reproducible.
"""
from __future__ import annotations

import math
from itertools import pairwise

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from core.domain.enums import GeomType, SourceFormat
from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.kernel import NotMeasurable, area_of, length_of, perimeter_of

# The kernel consumes float coordinates; bound magnitudes keep every
# computed length/area within float64's comfortable range.
COORD = st.floats(min_value=-10_000.0, max_value=10_000.0, allow_nan=False,
                 allow_infinity=False, width=32)

# The kernel computes in float64 (docs: "length/area in drawing units as
# float64"). Reversal/rotation invariance is EXACT mathematics, but the
# shoelace sum's floating-point ORDER changes with vertex order, and — more
# importantly — rings where large cross-products CANCEL (terms of ~3.3e4
# summing to ~1.3 in a real generated example) lose precision proportional to
# the CONDITION of the ring, not to the area itself. So the honest comparison
# scales the tolerance with the coordinate magnitudes: |x*y| products bound
# the round-off that can appear when summation order changes. Translation
# invariance with integer offsets stays bitwise-exact.


def _max_coord(points: list[tuple[float, float]]) -> float:
    return max((max(abs(x), abs(y)) for x, y in points), default=0.0)


def _shoelace_scale(ring_a: list[tuple[float, float]],
                    ring_b: list[tuple[float, float]]) -> float:
    """Round-off scale for shoelace sums: cancellation-heavy rings (large
    cross-products summing to a small area) lose precision proportional to
    the TERM magnitude (observed: terms ~3.3e4 -> area ~1.3, reversal
    difference ~1.5e-12), not to the area."""
    return max(_max_coord(ring_a) ** 2, _max_coord(ring_b) ** 2)


def _shoelace_close(ring_a: list[tuple[float, float]],
                    ring_b: list[tuple[float, float]],
                    a: float, b: float, rel: float = 1e-12) -> bool:
    """Area-equality tolerance: float64 round-off bounded by the ring's
    cross-product magnitude (the condition), floored by the values and 1.0
    so well-conditioned small rings compare tightly."""
    bound = rel * max(_shoelace_scale(ring_a, ring_b), abs(a), abs(b), 1.0)
    return abs(a - b) <= bound


def _rel_close(a: float, b: float, rel: float = 1e-12) -> bool:
    """Plain relative closeness for sums of positive terms (lengths,
    well-conditioned areas) where no cancellation occurs."""
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def _translated_length_close(points_a: list[tuple[float, float]],
                              points_b: list[tuple[float, float]],
                              a: float, b: float) -> bool:
    """Length-equality tolerance across a translate: each endpoint moves by
    at most ~1 ulp of the coordinate magnitude, so each segment gains ~2 ulps
    and the honest bound scales with coordinate magnitude, not with the
    (possibly tiny) total length."""
    coord_ulps = 1e-11 * max(_max_coord(points_a), _max_coord(points_b), 1.0)
    return abs(a - b) <= 1e-12 * max(abs(a), abs(b), 1.0) + coord_ulps


def _geom(gtype: GeomType, coords: list[tuple[float, float]]) -> NormalizedGeometry:
    return NormalizedGeometry(
        geom_type=gtype,
        coordinates=coords,
        source_format=SourceFormat.DXF_ENTITY,
        source_handles=(
            SourceHandleRef(format=SourceFormat.DXF_ENTITY, sheet_ref="modelspace",
                            entity_ref="PROP1", layer="WALL"),
        ),
        layer="WALL",
    )


def _ring_is_simple(ring: list[tuple[float, float]]) -> bool:
    """Shapely-validity oracle (the same engine the kernel itself calls):
    used as a strategy safety net, never as the assertion target."""
    from shapely.geometry import Polygon

    return Polygon(ring).is_valid


@st.composite
def simple_rings(draw: st.DrawFn) -> list[tuple[float, float]]:
    """A VALID kernel input by construction (the task's design rule): vertices
    at DISTINCT SORTED angles around a center with positive radii form a
    star-shaped ring, which is always simple. The shapely oracle below is a
    belt-and-braces net, not the generator."""
    k = draw(st.integers(min_value=3, max_value=12))
    cx, cy = draw(st.tuples(COORD, COORD))
    angles = draw(st.lists(
        st.floats(min_value=0.0, max_value=2.0 * math.pi,
                  exclude_max=True, allow_nan=False, allow_infinity=False),
        min_size=k, max_size=k, unique=True,
    ))
    radii = draw(st.lists(
        st.floats(min_value=0.01, max_value=5_000.0, allow_nan=False,
                  allow_infinity=False),
        min_size=k, max_size=k,
    ))
    angles.sort()
    points = [
        (cx + r * math.cos(t), cy + r * math.sin(t))
        for t, r in zip(angles, radii, strict=True)
    ]
    ring = [*points, points[0]]
    assume(_ring_is_simple(ring))  # safety net: radial rings are simple
    return ring


# ---------------------------------------------------------------------------
# area_of properties
# ---------------------------------------------------------------------------


class TestAreaProperties:
    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_area_translation_invariant(self, ring: list[tuple[float, float]]) -> None:
        """Area does not depend on where the shape sits. Offsets are integers
        so the translate is exact in float64; a vertex so close to another
        that the offset absorbs it makes the MOVED ring genuinely different
        geometry (the kernel honestly refuses it), so validity of both rings
        is a precondition, not an assertion target. The tolerance scales with
        the ring's cross-product magnitude (cancellation condition), not the
        area — see _shoelace_close."""
        dx, dy = 64.0, -32.0  # integer offsets: exact in float64
        moved = [(x + dx, y + dy) for x, y in ring]
        assume(_ring_is_simple(moved))
        assert _shoelace_close(
            ring, moved,
            area_of(_geom(GeomType.POLYGON, moved)),
            area_of(_geom(GeomType.POLYGON, ring)),
        )

    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_area_reversal_invariant(self, ring: list[tuple[float, float]]) -> None:
        """Vertex-order reversal (CW<->CCW) leaves the area invariant — the
        kernel takes abs() of the signed shoelace (TestArea pin #2). Equality
        holds to float64 round-off whose size scales with the ring's
        CROSS-PRODUCT magnitude (cancellation-heavy rings lose more: terms
        ~3.3e4 cancelling to ~1.3 were observed to differ by ~1.5e-12), not
        with the area itself — see _shoelace_close."""
        reversed_ring = [ring[0], *list(reversed(ring[1:-1])), ring[-1]]
        assert _shoelace_close(
            ring, reversed_ring,
            area_of(_geom(GeomType.POLYGON, reversed_ring)),
            area_of(_geom(GeomType.POLYGON, ring)),
        )

    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_area_nonnegative(self, ring: list[tuple[float, float]]) -> None:
        assert area_of(_geom(GeomType.POLYGON, ring)) >= 0.0


# ---------------------------------------------------------------------------
# length_of properties
# ---------------------------------------------------------------------------


class TestLengthProperties:
    @settings(max_examples=50)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=2, max_size=12))
    def test_length_reversal_invariant(self, points: list[tuple[float, float]]) -> None:
        """Traversing a polyline backwards covers the same segments: length
        is reversal-invariant. shapely sums segment lengths in order, so
        reversal equality holds to float64 round-off, not bitwise."""
        assume(all(p != q for p, q in pairwise(points)))  # no zero segments
        forward = length_of(_geom(GeomType.POLYLINE, points))
        backward = length_of(_geom(GeomType.POLYLINE, list(reversed(points))))
        assert _rel_close(forward, backward)

    @settings(max_examples=50)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=2, max_size=12))
    def test_length_at_least_endpoint_distance(
        self, points: list[tuple[float, float]]
    ) -> None:
        assume(all(p != q for p, q in pairwise(points)))
        (x0, y0), (x1, y1) = points[0], points[-1]
        direct = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        assert length_of(_geom(GeomType.POLYLINE, points)) >= direct - 1e-6

    @settings(max_examples=50)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=4, max_size=12))
    def test_closed_polyline_length_at_least_twice_span(
        self, points: list[tuple[float, float]]
    ) -> None:
        """A CLOSED polyline traverses every span and comes back: its length
        is >= 2 * max(x-span, y-span) — it must cross the widest span in each
        direction at least once, and return. (We compare against max span, not
        the sum: that is the provable statement for arbitrary simple loops.)"""
        assume(all(p != q for p, q in pairwise(points)))
        closed = [*points, points[0]]
        assume(all(p != q for p, q in pairwise(closed)))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        assume(span > 0.0)  # degenerate all-collinear span: skip, not assert
        assert length_of(_geom(GeomType.POLYLINE, closed)) >= 2 * span * (1 - 1e-9)


# ---------------------------------------------------------------------------
# perimeter_of properties (Round 8 rule room.gross.perimeter.v1)
# ---------------------------------------------------------------------------


class TestPerimeterProperties:
    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_perimeter_translation_invariant(
        self, ring: list[tuple[float, float]]
    ) -> None:
        """Perimeter does not depend on absolute position (integer offsets:
        exact in float64; each translated endpoint shifts ~1 ulp of its
        magnitude, so the tolerance scales with coordinate magnitude — see
        _translated_length_close)."""
        dx, dy = -128.0, 64.0
        moved = [(x + dx, y + dy) for x, y in ring]
        assume(_ring_is_simple(moved))  # offset may absorb a near-twin vertex
        assert _translated_length_close(
            ring, moved,
            perimeter_of(_geom(GeomType.POLYGON, moved)),
            perimeter_of(_geom(GeomType.POLYGON, ring)),
        )

    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_perimeter_reversal_invariant(
        self, ring: list[tuple[float, float]]
    ) -> None:
        """Ring traversal direction does not change the perimeter — segment
        lengths sum identically up to float64 round-off under reversal."""
        reversed_ring = [ring[0], *list(reversed(ring[1:-1])), ring[-1]]
        assert _rel_close(
            perimeter_of(_geom(GeomType.POLYGON, reversed_ring)),
            perimeter_of(_geom(GeomType.POLYGON, ring)),
        )

    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_perimeter_at_least_twice_max_span(
        self, ring: list[tuple[float, float]]
    ) -> None:
        """A closed ring crosses its widest span twice (once each way), so the
        perimeter is >= 2 * max(x-span, y-span) — provable for any simple ring."""
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        assume(span > 0.0)  # degenerate all-collinear span: skip, not assert
        assert perimeter_of(_geom(GeomType.POLYGON, ring)) >= 2 * span * (1 - 1e-9)

    @settings(max_examples=50)
    @given(ring=simple_rings())
    def test_perimeter_at_least_2d_area_feasibility(
        self, ring: list[tuple[float, float]]
    ) -> None:
        """Perimeter is at least 4*sqrt(area) (isoperimetric inequality, the
        circle being the optimum) — a TRUE bound for any simple closed curve."""
        import math

        area = area_of(_geom(GeomType.POLYGON, ring))
        assume(area > 0.0)  # degenerate zero-area ring: skip, not assert
        assert perimeter_of(_geom(GeomType.POLYGON, ring)) >= (
            2 * math.sqrt(math.pi * area) * (1 - 1e-9)
        )


# ---------------------------------------------------------------------------
# Gate-equivalence + refusal determinism (the honesty contract)
# ---------------------------------------------------------------------------


class TestRefusalDeterminism:
    """A refusal is deterministic data (exception rows are derived from it):
    same input, same refusal, every time — and area_of/perimeter_of share the
    SAME gate (takeoff/kernel.py perimeter_of docstring: 'EXACTLY the same
    gate as area_of')."""

    BOWTIES = st.integers(min_value=1, max_value=1000).map(
        lambda k: [(0.0, 0.0), (float(k), float(k)), (float(k), 0.0),
                   (0.0, float(k)), (0.0, 0.0)]
    )

    @settings(max_examples=30)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=3, max_size=8))
    def test_open_ring_refusal_is_stable_and_shared(
        self, points: list[tuple[float, float]]
    ) -> None:
        """A genuinely open ring (first != last, >=3 vertices) is ALWAYS
        refused by BOTH gates, with the same message, on every call."""
        assume(all(p != q for p, q in pairwise(points)))
        open_ring = points if points[0] != points[-1] else points[:-1]
        assume(len(open_ring) >= 3 and open_ring[0] != open_ring[-1])
        g = _geom(GeomType.POLYGON, open_ring)
        for _ in range(3):  # stability across repeated calls
            try:
                area_of(g)
            except NotMeasurable as exc:
                assert "closed" in str(exc)
            else:
                raise AssertionError("open ring must always be refused")
            try:
                perimeter_of(g)
            except NotMeasurable:
                pass
            else:
                raise AssertionError("perimeter gate must refuse what area refuses")

    @settings(max_examples=30)
    @given(ring=BOWTIES)
    def test_bowtie_refusal_is_stable_and_shared(
        self, ring: list[tuple[float, float]]
    ) -> None:
        g = _geom(GeomType.POLYGON, ring)
        for _ in range(2):
            try:
                area_of(g)
            except NotMeasurable as exc:
                assert "self-inter" in str(exc)
            else:
                raise AssertionError("bowtie must always be refused")
            try:
                perimeter_of(g)
            except NotMeasurable:
                pass
            else:
                raise AssertionError("perimeter gate must refuse the bowtie")

    @settings(max_examples=30)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=1, max_size=2))
    def test_short_ring_refusal_is_stable(self, points: list[tuple[float, float]]) -> None:
        ring = [*points, points[0]] if points else points  # <4 points incl. closure
        g = _geom(GeomType.POLYGON, ring)
        try:
            area_of(g)
        except NotMeasurable:
            pass
        else:
            raise AssertionError("short ring must be refused")
        try:
            perimeter_of(g)
        except NotMeasurable:
            pass
        else:
            raise AssertionError("short ring must be refused by the perimeter gate")

    @settings(max_examples=30)
    @given(points=st.lists(st.tuples(COORD, COORD), min_size=3, max_size=6))
    def test_multi_polygon_refusal_is_stable(
        self, points: list[tuple[float, float]]
    ) -> None:
        """A multi_polygon is refused as one ring by BOTH gates — the kernel
        never guesses which ring to measure (pinned by TestArea too)."""
        assume(all(p != q for p, q in pairwise(points)))
        ring: list[tuple[float, float]] = [*points, points[0]]
        # multi_polygon coordinates are a list OF rings (core.geometry docstring)
        g = NormalizedGeometry(
            geom_type=GeomType.MULTI_POLYGON,
            coordinates=[ring, ring],
            source_format=SourceFormat.DXF_ENTITY,
            source_handles=(
                SourceHandleRef(format=SourceFormat.DXF_ENTITY,
                                sheet_ref="modelspace", entity_ref="PROP1"),
            ),
        )
        try:
            area_of(g)
        except NotMeasurable as exc:
            assert "multi-polygon" in str(exc)
        else:
            raise AssertionError("multi-polygon must be refused as one ring")
        try:
            perimeter_of(g)
        except NotMeasurable:
            pass
        else:
            raise AssertionError("multi-polygon must be refused by the perimeter gate")
