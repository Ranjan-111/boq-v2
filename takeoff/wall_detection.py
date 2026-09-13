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
  4. OVERLAP-WINDOW PAIRING (engine 0.8.0): a parallel constant-separation
     face pair is a wall over the INTERSECTION of the two faces' drawn
     longitudinal spans — never over undrawn extents. One face may pair with
     several partners over DISJOINT windows (the split-face doorway: one
     continuous face beside two face fragments). Windows that overlap on a
     shared face are a true ambiguity: every claimant of the conflict is
     refused together, independent of order (the Round-3 doctrine generalized
     from whole faces to windows). Unpaired leftover spans surface as
     fragments, never walls.

Everything is float64 over drawing units; determinism is total given the same
geometry list order (stable via sort by first source handle).
"""
from __future__ import annotations

from dataclasses import dataclass, field
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

    Engine 0.8.0: this gate is now applied to WINDOW faces (a pair truncated
    to their common drawn span), so congruence is BY CONSTRUCTION for every
    wall the detector accepts. Whole-face congruence (the 0.7.0 semantics)
    remains the same predicate — callers without a window pass full faces.
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


def _window(
    s1: Seg, s2: Seg, *, offset_eps: float = OFFSET_EPS, parallel_eps: float = PARALLEL_EPS
) -> tuple[float, float] | None:
    """The common drawn span [lo, hi] of two parallel constant-separation faces.

    None when the faces are not parallel/opposite-offset within tolerance, are
    collinear (no thickness), share no longitudinal overlap (disjoint
    extents — the 0.7.0 refusal, kept: support must be DRAWN, both faces
    present across the whole window), or when the overlap is a measurement-
    noise sliver (below offset tolerance, or shorter than 1/1000 of either
    face — a coincidental touch beside a long face is not a drawn wall
    segment; the ratio bound keeps pairing deterministic and scale-free).

    The returned window never extends either face: it is the exact
    intersection of both faces' drawn spans, so no wall is ever measured over
    undrawn geometry.
    """
    if not all(isfinite(v) for v in (*s1.a, *s1.b, *s2.a, *s2.b)):
        return None
    if s1.length == 0 or s2.length == 0:
        return None
    if not are_parallel_pair(s1, s2, parallel_eps=parallel_eps):
        return None
    d_a = _perp_distance(s1.a, s2)
    d_b = _perp_distance(s1.b, s2)
    if abs(d_a - d_b) > offset_eps or d_a <= offset_eps:
        return None
    e_a = _perp_distance(s2.a, s1)
    e_b = _perp_distance(s2.b, s1)
    if abs(e_a - e_b) > offset_eps:
        return None
    u = s1.unit()
    p_a = (s2.a[0]-s1.a[0])*u[0] + (s2.a[1]-s1.a[1])*u[1]
    p_b = (s2.b[0]-s1.a[0])*u[0] + (s2.b[1]-s1.a[1])*u[1]
    lo, hi = min(p_a, p_b), max(p_a, p_b)
    ov_lo, ov_hi = max(lo, 0.0), min(hi, s1.length)
    if ov_hi - ov_lo <= offset_eps:
        return None  # disjoint or a zero-width touch
    if ov_hi - ov_lo < 1e-3 * max(s1.length, s2.length):
        return None
    return ov_lo, ov_hi


def _window_seg(s: Seg, lo: float, hi: float) -> Seg:
    """The sub-segment of s over longitudinal span [lo, hi] in s's own frame,
    carrying its OWN geometry record (the window span as drawn) while
    inheriting the original entity's source handles and layer — provenance is
    the original drawn line; the coordinates are the window both faces draw.

    Deterministic point-in-space computation: lo/hi come from projecting the
    partner face onto s, so the truncated face is exactly the geometry both
    faces actually draw. This geometry record is what the wall's replay rule
    consumes: window length is re-derivable from the truncated pair alone.
    """
    u = s.unit()
    a = (s.a[0] + u[0]*lo, s.a[1] + u[1]*lo)
    b = (s.a[0] + u[0]*hi, s.a[1] + u[1]*hi)
    geom = NormalizedGeometry(
        geom_type=GeomType.POLYLINE,
        coordinates=[a, b],
        source_format=s.geometry.source_format,
        source_handles=s.geometry.source_handles,
        layer=s.geometry.layer,
    )
    return Seg(a=a, b=b, geometry=geom)


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
    paired_windows: int = 0  # windows whose truncated faces became walls
    refused_windows: int = 0  # windows lost to a conflict on one of their faces


@dataclass(frozen=True, slots=True)
class _FaceWindow:
    """One wall-window claim: faces i/j paired over their common drawn span.

    lo_i/hi_i are the window's longitudinal span in face i's own frame;
    lo_j/hi_j the same geometric span in face j's frame (parallel frames may
    run in opposite orientations — each span is computed by projecting the
    OTHER face's endpoints into this face's frame and intersecting). The two
    spans describe the same window in space; both are carried so conflict
    checks on either face read a span in that face's consistent frame.
    """

    i: int
    j: int
    lo_i: float
    hi_i: float
    lo_j: float
    hi_j: float
    thickness: float


def _spans(
    s1: Seg, s2: Seg, *, offset_eps: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The common drawn span of two offset-consistent faces, in both frames.

    Returns ((lo, hi) in s1's frame, (lo, hi) in s2's frame), or None under
    the same refusals as _window. Both spans are the same geometric interval
    (the faces' longitudinal intersection), expressed per face.
    """
    u1 = s1.unit()
    p_a = (s2.a[0]-s1.a[0])*u1[0] + (s2.a[1]-s1.a[1])*u1[1]
    p_b = (s2.b[0]-s1.a[0])*u1[0] + (s2.b[1]-s1.a[1])*u1[1]
    lo1, hi1 = max(min(p_a, p_b), 0.0), min(max(p_a, p_b), s1.length)
    if hi1 - lo1 <= offset_eps or hi1 - lo1 < 1e-3 * max(s1.length, s2.length):
        return None
    u2 = s2.unit()
    q_a = (s1.a[0]-s2.a[0])*u2[0] + (s1.a[1]-s2.a[1])*u2[1]
    q_b = (s1.b[0]-s2.a[0])*u2[0] + (s1.b[1]-s2.a[1])*u2[1]
    lo2, hi2 = max(min(q_a, q_b), 0.0), min(max(q_a, q_b), s2.length)
    if hi2 - lo2 <= offset_eps:
        return None  # pragma: no cover - symmetric with the s1-frame check
    return (lo1, hi1), (lo2, hi2)


def _build_windows(
    segs: list[Seg], *, max_thickness: float,
    offset_eps: float, parallel_eps: float,
) -> list[_FaceWindow]:
    """Every pairwise face-window within the thickness selection limit."""
    windows: list[_FaceWindow] = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            s_i, s_j = segs[i], segs[j]
            if _window(s_i, s_j, offset_eps=offset_eps,
                       parallel_eps=parallel_eps) is None:
                continue
            thickness = _perp_distance(s_i.a, s_j)
            if thickness > max_thickness:
                continue
            spans = _spans(s_i, s_j, offset_eps=offset_eps)
            if spans is None:
                continue  # pragma: no cover - _window already filtered
            windows.append(_FaceWindow(
                i=i, j=j,
                lo_i=spans[0][0], hi_i=spans[0][1],
                lo_j=spans[1][0], hi_j=spans[1][1],
                thickness=thickness,
            ))
    return windows


def _conflict_groups(windows: list[_FaceWindow]) -> list[list[_FaceWindow]]:
    """Windows partitioned into singleton groups (accepted candidates) and
    conflict groups (refused together).

    A conflict is LOCAL: two windows sharing a face whose spans overlap on
    that face. Every window touching that contested span is refused — the
    Round-3 three-parallel-faces doctrine generalized to windows. The
    refusal never cascades transitively through independent windows: a
    window whose own faces' spans are uncontested survives even if one of
    its faces participates in a DIFFERENT, disjoint contested span (that
    fragment is a separate wall — the split-face doorway case).

    Order-independent: the verdict for each window depends only on the
    window set, never on iteration order.
    """
    def span_on(w: _FaceWindow, face: int) -> tuple[float, float]:
        return (w.lo_i, w.hi_i) if w.i == face else (w.lo_j, w.hi_j)

    by_face: dict[int, list[int]] = {}
    for w_idx, w in enumerate(windows):
        by_face.setdefault(w.i, []).append(w_idx)
        by_face.setdefault(w.j, []).append(w_idx)

    conflicted: set[int] = set()
    for idxs in by_face.values():
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                wa, wb = windows[idxs[a_pos]], windows[idxs[b_pos]]
                shared = {wa.i, wa.j} & {wb.i, wb.j}
                if not shared:
                    continue
                f = min(shared)  # deterministic; two faces share at most one
                sa, sb = span_on(wa, f), span_on(wb, f)
                if sa[1] > sb[0] and sb[1] > sa[0]:
                    conflicted.update((idxs[a_pos], idxs[b_pos]))

    groups: dict[int, list[_FaceWindow]] = {}
    for w_idx, w in enumerate(windows):
        if w_idx not in conflicted:
            groups.setdefault(-1 - w_idx, []).append(w)  # singleton candidates
    # every conflicted window forms one refusal group entry (kept separate so
    # callers can report each contested pair honestly)
    current_group: list[_FaceWindow] = []
    for w_idx, w in enumerate(windows):
        if w_idx in conflicted:
            current_group.append(w)
    if current_group:
        groups[len(windows)] = current_group
    return list(groups.values())


def detect_walls(
    geometries: list[NormalizedGeometry],
    *,
    wall_layers_only: bool = True,
    parallel_eps: float = PARALLEL_EPS,
    offset_eps: float = OFFSET_EPS,
    max_thickness: float | None = None,
) -> WallDetectionResult:
    """Overlap-window wall pairing (engine 0.8.0).

    A parallel constant-separation face pair is a wall over the intersection
    of the two faces' drawn spans — never over undrawn extents. One face may
    pair with several partners over DISJOINT windows (a continuous face
    beside face fragments split by a doorway). Windows that overlap on a
    shared face are a true ambiguity: ALL claimants of the conflict group
    are refused together, independent of order. Unpaired leftover spans
    surface as fragments, never walls.

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

    windows: list[_FaceWindow] = []
    if max_thickness is not None:
        windows = _build_windows(
            segs, max_thickness=max_thickness,
            offset_eps=offset_eps, parallel_eps=parallel_eps,
        )

    accepted: list[_FaceWindow] = []
    for group in (_conflict_groups(windows) if windows else []):
        if len(group) == 1:
            accepted.append(group[0])
        else:
            result.refused_windows += len(group)
            for w in group:
                result.overlaps.append(
                    (_first_handle(segs[w.i]), _first_handle(segs[w.j]))
                )

    used: set[int] = set()
    for w in sorted(accepted, key=lambda w: (w.i, w.j, w.lo_i, w.hi_i)):
        s_i = _window_seg(segs[w.i], w.lo_i, w.hi_i)
        s_j = _window_seg(segs[w.j], w.lo_j, w.hi_j)
        # The truncated pair must satisfy the SAME gate the replay rule
        # applies — detection can never accept what the rule would refuse.
        if not are_offset_pair(s_i, s_j, offset_eps=offset_eps,
                               parallel_eps=parallel_eps):
            continue
        wall = _make_wall(s_i, s_j)
        if wall is None:
            continue
        result.walls.append(wall)
        result.paired_windows += 1
        used.update((w.i, w.j))
    result.unmatched_edges = [_first_handle(s) for i, s in enumerate(segs) if i not in used]
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
