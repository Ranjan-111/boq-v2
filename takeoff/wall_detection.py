"""Wall detection from parallel line pairs (T042) — OUR build.

docs/reuse-matrix.md "What OCErp does NOT have": nothing in the reference
polygonizes walls from line pairs; this module is original work against
docs/domain-model.md.

Algorithm (deterministic, explainable, no AI):
  1. Collect candidate wall edges: LINE/POLYLINE geometries on WALL-ish layers
     (or any layer the caller allows; layer heuristics are explicit inputs).
  2. For each ordered pair of straight 2-point segments, test parallelism
     (cross product ~ 0) and opposite-offset (each endpoint of A is at the
     same perpendicular distance from line B) within tolerance.
  3. Accepted pairs become WallCandidates: centerline endpoints = midpoints of
     the two offset vectors, thickness = perpendicular distance, length =
     mean edge length.
  4. Require finite congruent support and an explicit maximum thickness.
     Accept only unique reciprocal pairs; ambiguous candidates are refused.

Everything is float64 over drawing units; determinism is total given the same
geometry list order (stable via sort by first source handle).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import isfinite
from typing import cast

from core.domain.enums import GeomType
from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.kernel import NotMeasurable, length_of

# Tolerances in DRAWING UNITS. DXF files are usually drawn at true scale, so
# 1e-6 parallelism and 1e-3 offset tolerance are safe. These are data, not
# magic: passed in by callers, defaulted here.
PARALLEL_EPS = 1e-6
OFFSET_EPS = 1e-3

# Layer names we treat as wall edges (case-insensitive substring match).
WALL_LAYER_HINTS = ("wall", "muro", "wand", "mur", "стена")


@dataclass(frozen=True, slots=True)
class Seg:
    """A straight 2-point edge extracted from normalized geometry."""

    a: tuple[float, float]
    b: tuple[float, float]
    geometry: NormalizedGeometry

    @property
    def handles(self) -> tuple[SourceHandleRef, ...]:
        return self.geometry.source_handles

    @property
    def dx(self) -> float:
        return self.b[0] - self.a[0]

    @property
    def dy(self) -> float:
        return self.b[1] - self.a[1]

    @property
    def length(self) -> float:
        return float((self.dx**2 + self.dy**2) ** 0.5)

    def unit(self) -> tuple[float, float]:
        n = self.length
        if n == 0:
            raise NotMeasurable("zero-length edge")
        return (self.dx / n, self.dy / n)


def _is_wall_layer(layer: str | None) -> bool:
    if layer is None:
        return False
    low = layer.lower()
    return any(h in low for h in WALL_LAYER_HINTS)


def extract_segs(
    geometries: list[NormalizedGeometry], *, wall_layers_only: bool = True
) -> list[Seg]:
    """Straight edges eligible for pairing. Straight = exactly 2 vertices.

    Longer polylines are skipped for wall pairing in V1 (they become
    length-rule inputs instead, via the rules registry).
    """
    segs: list[Seg] = []
    for geom in geometries:
        if wall_layers_only and not _is_wall_layer(geom.layer):
            continue
        if geom.geom_type is not GeomType.POLYLINE:
            continue
        pts = cast("list[tuple[float, float]]", geom.coordinates)
        if len(pts) == 2:
            a = (float(pts[0][0]), float(pts[0][1]))
            b = (float(pts[1][0]), float(pts[1][1]))
            segs.append(Seg(a=a, b=b, geometry=geom))
    # Full provenance plus coordinates makes placement ordering reproducible.
    def sort_key(s: Seg) -> tuple[tuple[tuple[str, str, str], ...], tuple[float, ...]]:
        return (tuple((h.format.value, h.sheet_ref, h.entity_ref) for h in s.handles),
                (*s.a, *s.b))

    return sorted(segs, key=sort_key)


def _cross(u: tuple[float, float], v: tuple[float, float]) -> float:
    return u[0] * v[1] - u[1] * v[0]


def _perp_distance(p: tuple[float, float], line: Seg) -> float:
    """Perpendicular distance of point p from line A's infinite line."""
    ux, uy = line.dx, line.dy
    n = line.length
    if n == 0:
        return float("inf")
    return abs(_cross((ux, uy), (p[0] - line.a[0], p[1] - line.a[1]))) / n


def are_parallel_pair(s1: Seg, s2: Seg, *, parallel_eps: float = PARALLEL_EPS) -> bool:
    """Two segments are parallel when their unit vectors cross ~ 0."""
    u1, u2 = s1.unit(), s2.unit()
    c = _cross(u1, u2)
    return abs(c) <= parallel_eps


def are_offset_pair(
    s1: Seg, s2: Seg, *, offset_eps: float = OFFSET_EPS, parallel_eps: float = PARALLEL_EPS
) -> bool:
    """True when s1/s2 are the two faces of one wall: parallel, opposite offset.

    Conditions (each endpoint of s1 sits at distance `t` from s2's line, and
    vice versa — the two faces straddle a shared centerline).
    Zero-length edges never pair (degenerate drawing anomalies surface as
    unmatched edges instead of crashing the pairing math).
    """
    if not all(isfinite(v) for v in (*s1.a, *s1.b, *s2.a, *s2.b)):
        return False
    if s1.length == 0 or s2.length == 0:
        return False
    if not are_parallel_pair(s1, s2, parallel_eps=parallel_eps):
        return False
    d_a = _perp_distance(s1.a, s2)
    d_b = _perp_distance(s1.b, s2)
    if abs(d_a - d_b) > offset_eps:
        return False
    # s2's endpoints must also be at the SAME distance from s1's line
    e_a = _perp_distance(s2.a, s1)
    e_b = _perp_distance(s2.b, s1)
    if not (abs(e_a - e_b) <= offset_eps and d_a > offset_eps):
        return False
    # Require support over the entire finite segment, in either orientation.
    # Numerical tolerance is for congruence, never permission to extend a face.
    u = s1.unit()
    projections = sorted((p[0]-s1.a[0])*u[0] + (p[1]-s1.a[1])*u[1]
                         for p in (s2.a, s2.b))
    tolerance = max(s1.length, s2.length) * 1e-9 + 1e-9
    return (abs(projections[0]) <= tolerance
            and abs(projections[1]-s1.length) <= tolerance
            and abs(s1.length-s2.length) <= tolerance)


@dataclass(frozen=True, slots=True)
class WallCandidate:
    """One detected wall: centerline + thickness + full provenance."""

    centerline: tuple[tuple[float, float], tuple[float, float]]
    thickness: float
    edge_geometries: tuple[NormalizedGeometry, ...]
    rule_id: str = "wall.centerline.length.v1"

    @property
    def source_handles(self) -> tuple[SourceHandleRef, ...]:
        return tuple(h for g in self.edge_geometries for h in g.source_handles)

    @property
    def length(self) -> float:
        (x0, y0), (x1, y1) = self.centerline
        return float(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)

    def measure(self) -> float:
        """Deterministic centerline length in drawing units (replayable)."""
        return self.length


@dataclass(slots=True)
class WallDetectionResult:
    walls: list[WallCandidate] = field(default_factory=list)
    overlaps: list[tuple[str, str]] = field(default_factory=list)  # (handleA, handleB)
    unmatched_edges: list[str] = field(default_factory=list)  # handles w/o pair
    considered_edges: int = 0


def detect_walls(
    geometries: list[NormalizedGeometry],
    *,
    wall_layers_only: bool = True,
    parallel_eps: float = PARALLEL_EPS,
    offset_eps: float = OFFSET_EPS,
    max_thickness: float | None = None,
) -> WallDetectionResult:
    """Only unique reciprocal supported pairs are walls; ambiguity is refused.

    max_thickness is an explicit drawing-unit selection input, not an inferred
    construction dimension. With no selection limit all edges remain unmatched.
    """
    if not (isfinite(parallel_eps) and 0 <= parallel_eps <= PARALLEL_EPS):
        raise ValueError("invalid parallel tolerance")
    if not (isfinite(offset_eps) and 0 < offset_eps <= OFFSET_EPS):
        raise ValueError("invalid offset tolerance")
    if max_thickness is not None and (
        not isfinite(max_thickness) or max_thickness <= offset_eps
    ):
        raise ValueError("maximum thickness must be finite and greater than tolerance")
    segs = extract_segs(geometries, wall_layers_only=wall_layers_only)
    result = WallDetectionResult(considered_edges=len(segs))
    partners: dict[int, set[int]] = {i: set() for i in range(len(segs))}
    if max_thickness is not None:
        for i, j in combinations(range(len(segs)), 2):
            s1, s2 = segs[i], segs[j]
            if (are_offset_pair(s1, s2, offset_eps=offset_eps, parallel_eps=parallel_eps)
                    and _perp_distance(s1.a, s2) <= max_thickness):
                partners[i].add(j)
                partners[j].add(i)
    used: set[int] = set()
    for i, js in partners.items():
        for j in sorted(js):
            if j <= i:
                continue
            if len(js) != 1 or len(partners[j]) != 1:
                result.overlaps.append((_first_handle(segs[i]), _first_handle(segs[j])))
                continue
            wall = _make_wall(segs[i], segs[j])
            if wall is not None:
                result.walls.append(wall)
                used.update((i,j))
    result.unmatched_edges = [_first_handle(s) for i,s in enumerate(segs) if i not in used]
    return result


def _first_handle(seg: Seg) -> str:
    return seg.handles[0].entity_ref if seg.handles else "?"


def _make_wall(s1: Seg, s2: Seg) -> WallCandidate | None:
    """Centerline endpoints from the two offset faces (perpendicular midpoint rule)."""
    u = s1.unit()
    # midpoint of the thickness at each end of s1
    t = _perp_distance(s1.a, s2)
    # sign: which side is s2 on?
    cross_val = _cross(u, (s2.a[0] - s1.a[0], s2.a[1] - s1.a[1]))
    sign = 1.0 if cross_val >= 0 else -1.0
    # normal vector
    nx, ny = -u[1], u[0]

    def mid_on_centerline(p: tuple[float, float]) -> tuple[float, float]:
        # p is on face 1; the centerline is t/2 toward face 2
        return (p[0] + sign * nx * t / 2, p[1] + sign * ny * t / 2)

    c0 = mid_on_centerline(s1.a)
    c1 = mid_on_centerline(s1.b)
    if t <= 0:
        return None
    return WallCandidate(
        centerline=(c0, c1),
        thickness=t,
        edge_geometries=(s1.geometry, s2.geometry),
    )


# ---------------------------------------------------------------------------
# Derived geometry: wall footprint polygon (centerline x thickness)
# ---------------------------------------------------------------------------


def wall_footprint(wall: WallCandidate) -> NormalizedGeometry:
    """The wall's footprint polygon (centerline expanded by thickness/2).

    Derived geometry (docs/domain-model.md Geometry.derived_from): keeps the
    full source-handle chain of both edges; AI never appears here.
    """
    (x0, y0), (x1, y1) = wall.centerline
    dx, dy = x1 - x0, y1 - y0
    n = (dx * dx + dy * dy) ** 0.5
    if n == 0:
        raise NotMeasurable("zero-length centerline")
    ux, uy = dx / n, dy / n
    nx, ny = -uy, ux
    h = wall.thickness / 2
    ring = [
        (x0 + nx * h, y0 + ny * h),
        (x1 + nx * h, y1 + ny * h),
        (x1 - nx * h, y1 - ny * h),
        (x0 - nx * h, y0 - ny * h),
        (x0 + nx * h, y0 + ny * h),
    ]
    return NormalizedGeometry(
        geom_type=GeomType.POLYGON,
        coordinates=ring,
        source_format=wall.edge_geometries[0].source_format,
        source_handles=wall.source_handles,
        layer=wall.edge_geometries[0].layer,
    )


def wall_edge_length(wall: WallCandidate) -> float:
    """Mean of the two face lengths — the honest centerline length estimate."""
    return (length_of(wall.edge_geometries[0]) + length_of(wall.edge_geometries[1])) / 2
