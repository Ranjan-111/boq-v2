"""Centerline junction completion (engine 0.9.0) — OUR build.

docs/reuse-matrix.md: nothing in the reference completes wall junctions;
this module is original work against docs/domain-model.md §"Engine 0.9.0 —
centerline junction completion".

Engine 0.8.0 windows truncate every wall to the span both faces draw, so on
real drawings centerlines stop at corners: each ends at the partner wall's
face, half a thickness short of the partner's centerline, and exact-endpoint
noding closes no ring. This module completes legitimate junctions IN THE
ROOM GRAPH ONLY:

  1. PERPENDICULAR COMPLETION (corner / T-junction): the junction point I is
     the intersection of the two walls' infinite centerlines. Both sides must
     stay inside the DRAWN thickness — A's extension |P→I| <= t_B/2 (A may
     reach at most B's centerline), and B either needs no extension (I within
     B's drawn span: a T-junction) or extends its own near endpoint by at
     most t_A/2 (a mutual corner). One unambiguous I per endpoint, or refusal.
  2. COLLINEAR DOORWAY BRIDGE: two walls on one line with a gap between
     their spans, bridged ONLY when drawn opening-layer evidence crosses the
     gap span and straddles the wall band (a door drawn in the gap). Bare
     gaps stay open — two separate structures is equally plausible.

Nothing here is ever measured: no wall row, element, or evidence derives
from a connector. Wall detection output is consumed read-only.

Refusal doctrine (never a guess):
  * an endpoint with multiple DISTINCT feasible junction points is
    `junction_ambiguous` (the endpoint stays open; walls all keep their
    measurements),
  * endpoints with no bounded partner, and bare collinear gaps, produce
    nothing — open is honest, `room_not_enclosed` still fires upstream.

All math is float64 over drawing units; determinism is total given the same
wall list order (walls arrive detection-sorted; all iteration is
order-stable and every output list is canonically sorted).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.openings import OPENING_LAYER_HINTS
from takeoff.wall_detection import Seg, WallCandidate, _is_wall_layer

# Reach tolerance in DRAWING UNITS beyond the drawn half-thickness — slack
# for endpoint coordinates rounded by the source CAD, bounded so a gap that
# is merely NEAR a wall's end never completes (a real doorway offset from
# the wall line stays open). Data, not magic: recorded into the result for
# the caller's digest (R3 doctrine: tolerances are replay inputs).
JUNCTION_REACH_EPS = 0.5
# Tolerance on matching centerline directions (parallel/anti-parallel).
JUNCTION_PARALLEL_EPS = 1e-6
# Perpendicular offset tolerance for "same line" (wall centerlines on one
# line) — strictly tighter than a wall thickness: two walls on one line
# draw a shared face corridor; walls merely nearby do not.
JUNCTION_COLLINEAR_PERP_EPS = 1e-3


@dataclass(frozen=True, slots=True)
class JunctionLink:
    """One completed junction in the room graph.

    point I (the junction) plus its connector endpoints: every wall
    centerline endpoint that must reach I gets its own connector segment
    (endpoint -> I). Room polygonization nodes the centerlines THROUGH I,
    so the ring runs along real wall centerlines and zero-length junction
    geometry — never along invented wall.

    closures: for each PERPENDICULAR connector, the rectangle swept by
    extending the owning wall's footprint from its endpoint to I — the
    corner nub (drawn material the 0.8.0 windows refuse to measure: outer
    faces meet, inner faces stop). NET room area subtracts these beside the
    wall footprints; the extension never exceeds the partner's drawn
    half-thickness (the same bound that admitted the junction).

    source_handles: the partner walls' face handles (derived geometry);
    corroborating_handles: doorway-bridge evidence handles (opening-layer
    lines) when the link is a bridge, else empty.
    """

    point: tuple[float, float]
    connectors: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    source_handles: tuple[SourceHandleRef, ...]
    corroborating_handles: tuple[SourceHandleRef, ...]
    closures: tuple[tuple[tuple[float, float], ...], ...] = ()


@dataclass(frozen=True, slots=True)
class JunctionCompletionResult:
    links: tuple[JunctionLink, ...] = ()
    ambiguous: tuple[str, ...] = ()  # junction_ambiguous messages (REVIEW)
    junction_eps: float = JUNCTION_REACH_EPS  # recorded for the digest


def _unit(v: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n)


def _frame(wall: WallCandidate) -> tuple[tuple[float, float], tuple[float, float], float]:
    """(unit direction along centerline, origin = first endpoint, length)."""
    (x0, y0), (x1, y1) = wall.centerline
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    if n == 0:  # pragma: no cover - walls are never zero-length
        raise ValueError("zero-length centerline")
    return (dx / n, dy / n), (x0, y0), n


def _project(
    u: tuple[float, float], origin: tuple[float, float], p: tuple[float, float]
) -> float:
    return (p[0] - origin[0]) * u[0] + (p[1] - origin[1]) * u[1]


def _perp(
    u: tuple[float, float], origin: tuple[float, float], p: tuple[float, float]
) -> float:
    return -(p[0] - origin[0]) * u[1] + (p[1] - origin[1]) * u[0]


def _near(p: tuple[float, float], q: tuple[float, float]) -> bool:
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= 1e-6


def _exact(p: tuple[float, float], q: tuple[float, float]) -> bool:
    return p[0] == q[0] and p[1] == q[1]


def _endpoint_at_proj(
    wall: WallCandidate,
    u: tuple[float, float],
    origin: tuple[float, float],
    gap_lo: float,
    gap_hi: float,
) -> tuple[float, float]:
    """The wall's own endpoint lying on a gap boundary (exact coordinates).

    A gap boundary is one of the wall's endpoints (or, under the 1e-6
    off-line tolerance, within a rounding of it); the bridge must reuse
    that exact endpoint so the room graph nodes there.
    """
    best: tuple[float, tuple[float, float]] | None = None
    for ep in wall.centerline:
        pr = _project(u, origin, ep)
        d = min(abs(pr - gap_lo), abs(pr - gap_hi))
        if best is None or d < best[0]:
            best = (d, ep)
    assert best is not None  # a wall has two endpoints
    return best[1]


# Bridge-evidence layer hints — the opening detector's hints plus "header":
# a header (the framing above a door, drawn on A-HEADER-class layers) is
# drawn evidence that a gap is a doorway, exactly like door/window/opening
# symbols. Used ONLY for junction bridging; the opening-count detector's
# own hints stay in takeoff.openings.
JUNCTION_EVIDENCE_LAYER_HINTS = (*OPENING_LAYER_HINTS, "header")


def _is_evidence_layer(layer: str | None) -> bool:
    if layer is None:
        return False
    low = layer.lower()
    return any(h in low for h in JUNCTION_EVIDENCE_LAYER_HINTS)


def _closure_rect(
    wall: WallCandidate, endpoint: tuple[float, float], jpt: tuple[float, float]
) -> tuple[tuple[float, float], ...] | None:
    """The owning wall's footprint extended from endpoint to the junction I.

    Bounded by construction: |endpoint -> I| never exceeds the partner's
    drawn half-thickness (the junction bound), so the rectangle covers
    exactly the corner nub the faces draw. Returns the closed ring or None
    when endpoint == I (nothing to extend).
    """
    if math.hypot(endpoint[0] - jpt[0], endpoint[1] - jpt[1]) <= 1e-9:
        return None
    (x0, y0), (x1, y1) = wall.centerline
    ux, uy = _unit((x1 - x0, y1 - y0))
    h = wall.thickness / 2
    nx, ny = -uy, ux
    ring = (
        (endpoint[0] + nx * h, endpoint[1] + ny * h),
        (jpt[0] + nx * h, jpt[1] + ny * h),
        (jpt[0] - nx * h, jpt[1] - ny * h),
        (endpoint[0] - nx * h, endpoint[1] - ny * h),
        (endpoint[0] + nx * h, endpoint[1] + ny * h),
    )
    if not all(math.isfinite(v) for p in ring for v in p):
        return None
    return ring


def _perpendicular_links(
    walls: list[WallCandidate], *, junction_eps: float
) -> tuple[list[JunctionLink], list[str]]:
    """Corner/T completion between non-parallel walls.

    For each wall endpoint, every partner whose centerline intersects ours
    forward of the endpoint within BOTH half-thickness bounds is a
    candidate; the candidates must agree on ONE junction point.
    """
    links_by_point: dict[tuple[float, float], list[JunctionLink]] = {}
    ambiguous: list[str] = []
    for i, wall in enumerate(walls):
        u = _frame(wall)[0]
        a0, a1 = wall.centerline
        for end_idx, (pt, d) in enumerate(
            ((a0, (-u[0], -u[1])), (a1, (u[0], u[1])))
        ):
            candidates: list[tuple[float, int, tuple[float, float], int]] = []
            # (extension, partner idx, I, partner end idx or -1 when I is
            # interior to the partner span)
            for j, other in enumerate(walls):
                if j == i:
                    continue
                ub, ob, lb = _frame(other)
                if abs(u[0] * ub[1] - u[1] * ub[0]) <= JUNCTION_PARALLEL_EPS:
                    continue  # parallel walls never corner-join
                # I = intersection of the two infinite centerlines:
                # P + s*d = B0 + r*ub   (s = A-side extension)
                px, py = pt
                denom = d[0] * (-ub[1]) - d[1] * (-ub[0])
                if abs(denom) < 1e-12:
                    continue
                s = ((ob[0] - px) * (-ub[1]) - (ob[1] - py) * (-ub[0])) / denom
                r = ((ob[0] - px) * (-d[1]) - (ob[1] - py) * (-d[0])) / denom
                if s <= 1e-9:
                    continue  # junction is behind the endpoint
                if s > other.thickness / 2 + junction_eps:
                    continue  # A would cross past B's drawn material
                partner_end = -1
                if not (-junction_eps <= r <= lb + junction_eps):
                    # I outside B's drawn span: mutual corner — B's near
                    # endpoint must extend by at most A's half-thickness.
                    b_ext = -r if r < 0 else r - lb
                    if b_ext > wall.thickness / 2 + junction_eps:
                        continue
                    partner_end = 0 if r < 0 else 1
                jpt = (px + s * d[0], py + s * d[1])
                candidates.append((s, j, jpt, partner_end))
            if not candidates:
                continue
            points = {
                (round(c[2][0], 6), round(c[2][1], 6)) for c in candidates
            }
            if len(points) > 1:
                ambiguous.append(
                    f"wall {i} end {end_idx} has {len(points)} distinct feasible "
                    "junction points - endpoint left open"
                )
                continue
            # One junction point; wire every bounded partner + our endpoint
            # into it (an endpoint may legitimately meet several walls at one
            # shared corner point). jpt is the FIRST candidate's value — all
            # candidates round to this point, and the merge below canonicalizes
            # every connector end onto the merged link's point so the room
            # graph nodes exactly (a junction computed in two walls' frames
            # differs in the last float bits; unary_union never snaps them).
            jpt = candidates[0][2]
            handles: dict[tuple[str, str, str], SourceHandleRef] = {}
            connectors: list[tuple[tuple[float, float], tuple[float, float]]] = [
                (pt, jpt)
            ]
            closures: list[tuple[tuple[float, float], ...]] = []
            own = _closure_rect(wall, pt, jpt)
            if own is not None:
                closures.append(own)
            for _s, j, _jpt_c, partner_end in candidates:
                for h in walls[j].source_handles:
                    handles[(h.format.value, h.sheet_ref, h.entity_ref)] = h
                if partner_end >= 0:
                    connectors.append((walls[j].centerline[partner_end], jpt))
                    partner_rect = _closure_rect(
                        walls[j], walls[j].centerline[partner_end], jpt
                    )
                    if partner_rect is not None:
                        closures.append(partner_rect)
            links_by_point.setdefault(
                (round(jpt[0], 6), round(jpt[1], 6)), []
            ).append(JunctionLink(
                point=jpt,
                connectors=tuple(connectors),
                source_handles=tuple(sorted(
                    handles.values(),
                    key=lambda h: (h.format.value, h.sheet_ref, h.entity_ref),
                )),
                corroborating_handles=(),
                closures=tuple(closures),
            ))
    # Merge per-endpoint links that resolved to the SAME junction point:
    # a corner is ONE junction shared by the walls meeting there, and the
    # room graph must not carry parallel duplicate connectors.
    links: list[JunctionLink] = []
    for key in sorted(links_by_point):
        group = links_by_point[key]
        merged_handles = {
            (h.format.value, h.sheet_ref, h.entity_ref): h
            for link in group for h in link.source_handles
        }
        seen_conn: set[tuple[tuple[float, ...], tuple[float, ...]]] = set()
        merged_conn: list[tuple[tuple[float, float], tuple[float, float]]] = []
        seen_rect: set[tuple[tuple[float, ...], ...]] = set()
        merged_rect: list[tuple[tuple[float, float], ...]] = []
        canon = group[0].point
        for link in group:
            for c in link.connectors:
                # Canonicalize the junction end onto the merged point: the
                # same junction computed in different walls' frames differs
                # in the last float bits, and the room graph must node at
                # ONE exact point (unary_union never snaps near-coincident
                # endpoints).
                ca = (c[0][0], c[0][1])
                cb = (c[1][0], c[1][1])
                if _near(ca, canon) and not _exact(ca, canon):
                    ca = canon
                if _near(cb, canon) and not _exact(cb, canon):
                    cb = canon
                c = (ca, cb)
                ck = (tuple(round(v, 9) for v in c[0]),
                      tuple(round(v, 9) for v in c[1]))
                if ck not in seen_conn:
                    seen_conn.add(ck)
                    merged_conn.append(c)
            for rect in link.closures:
                rk = tuple(tuple(round(v, 9) for v in p) for p in rect)
                if rk not in seen_rect:
                    seen_rect.add(rk)
                    merged_rect.append(rect)
        links.append(JunctionLink(
            point=group[0].point,
            connectors=tuple(merged_conn),
            source_handles=tuple(sorted(
                merged_handles.values(),
                key=lambda h: (h.format.value, h.sheet_ref, h.entity_ref),
            )),
            corroborating_handles=(),
            closures=tuple(merged_rect),
        ))
    return links, ambiguous


def _doorway_bridges(
    walls: list[WallCandidate],
    geometries: list[NormalizedGeometry],
    *,
    junction_eps: float,
) -> tuple[list[JunctionLink], list[str]]:
    """Collinear gap bridging, corroborated by drawn opening-layer lines.

    The bridge is a corridor segment on the shared centerline line between
    the two walls' finite spans. WITHOUT corroboration nothing is bridged
    (the multi-storey separation gap stays open).
    """
    bridges: list[JunctionLink] = []
    # Evidence on opening/header layers: any line-like geometry (LINE,
    # polyline, closed polyline) — corroborated via its VERTEX EXTENT (the
    # min/max projected span and band of all vertices), not just 2-point
    # segments: real drawings draw headers as closed 4-vertex polylines
    # spanning the doorway gap and jambs as jamb lines.
    evidence_pts: list[tuple[tuple[tuple[float, float], ...],
                             tuple[SourceHandleRef, ...]]] = []
    wall_segs: list[Seg] = []
    for g in geometries:
        if g.geom_type.value not in ("line", "polyline", "polygon"):
            continue
        pts = cast("list[tuple[float, float]]", g.coordinates)
        if len(pts) < 2 or not all(
            math.isfinite(v) for p in pts for v in p
        ):
            continue
        if _is_evidence_layer(g.layer):
            evidence_pts.append((tuple(pts), g.source_handles))
        elif (len(pts) == 2 and _is_wall_layer(g.layer)):
            wall_segs.append(Seg(a=pts[0], b=pts[1], geometry=g))
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, b = walls[i], walls[j]
            if abs(a.thickness - b.thickness) > 1e-6:
                continue
            ua, oa, la = _frame(a)
            ub, ob, lb = _frame(b)
            if abs(abs(ua[0] * ub[0] + ua[1] * ub[1]) - 1.0) > JUNCTION_PARALLEL_EPS:
                continue  # not collinear
            if abs(_perp(ua, oa, ob)) > JUNCTION_COLLINEAR_PERP_EPS:
                continue  # parallel but off-line: separate walls
            b_end = (ob[0] + ub[0] * lb, ob[1] + ub[1] * lb)
            projs = [_project(ua, oa, ob), _project(ua, oa, b_end)]
            lo_b, hi_b = min(projs), max(projs)
            if lo_b > la:
                gap_lo, gap_hi = la, lo_b
            elif hi_b < 0:
                gap_lo, gap_hi = hi_b, 0.0
            else:
                continue  # overlapping or touching — not a gap
            gap = gap_hi - gap_lo
            if gap <= 1e-6:
                continue
            # The gap must be EMPTY: a third collinear wall lying inside the
            # interval means i and j are not adjacent runs (the seam belongs
            # to the closer pair — pairing the outer walls would bridge past
            # a real wall and invent a corridor through it).
            if any(
                k not in (i, j)
                and abs(_perp(ua, oa, walls[k].centerline[0]))
                <= JUNCTION_COLLINEAR_PERP_EPS
                and (
                    gap_lo + 1e-6
                    <= _project(ua, oa, walls[k].centerline[0])
                    <= gap_hi - 1e-6
                    or gap_lo + 1e-6
                    <= _project(ua, oa, walls[k].centerline[1])
                    <= gap_hi - 1e-6
                )
                for k in range(len(walls))
            ):
                continue
            corroborators: list[SourceHandleRef] = []
            # SEAM corroboration first: the walls are two window measurements
            # of ONE drawn wall-run when a drawn wall-layer face extends
            # through the whole gap (its span covers gap_lo..gap_hi inside
            # the wall band) — the drawing supports continuity; the seam is a
            # pairing artifact, not a doorway. The evidence is the ORIGINAL
            # drawn line from the sheet geometries: the walls' own
            # edge_geometries are window-truncated at the seam by
            # construction (that is why the seam exists).
            seam_face = False
            for seg in wall_segs:
                s0, s1 = seg.a, seg.b
                span = [_project(ua, oa, s0), _project(ua, oa, s1)]
                if (min(span) > gap_lo + 1e-6
                        or max(span) < gap_hi - 1e-6):
                    continue  # does not cover the whole gap
                band = [_perp(ua, oa, s0), _perp(ua, oa, s1)]
                if max(abs(x) for x in band) > a.thickness / 2 + 1e-3:
                    continue  # the line leaves the wall band: not a face of it
                seam_face = True
                corroborators.extend(seg.handles)
                break
            corroborated_by = "seam_face" if seam_face else None
            if corroborated_by is None:
                # DOORWAY corroboration: an opening/header-layer geometry
                # whose VERTEX EXTENT covers the gap span (a doorway is drawn
                # across its full width — jambs, swing arcs' chord closes,
                # header rectangles) and stays in the wall band's corridor. A
                # short line merely touching inside a long gap corroborates
                # nothing — two separate structures with incidental evidence
                # stays open.
                for pts_ext, handles_in in evidence_pts:
                    span = [_project(ua, oa, p) for p in pts_ext]
                    if max(span) < gap_lo + 1e-6 or min(span) > gap_hi - 1e-6:
                        continue  # does not occupy the gap span at all
                    if (min(span) > gap_lo + junction_eps
                            or max(span) < gap_hi - junction_eps):
                        continue  # partial coverage only: not this doorway
                    band = [_perp(ua, oa, p) for p in pts_ext]
                    half = a.thickness / 2
                    if (min(band) <= half + junction_eps
                            and max(band) >= -half - junction_eps):
                        corroborators.extend(handles_in)
                        corroborated_by = "opening_evidence"
                        break
            if corroborated_by is None:
                continue  # bare gap: stays open (honest)
            # The bridge's ends are the WALLS' OWN endpoint coordinates (never
            # recomputed from the frame): a frame-recomputed end differs in
            # the last float bits from the wall centerline it must meet, and
            # the room graph would not node there. gap_lo sits at A's far
            # endpoint, gap_hi at B's near one (or vice versa on the other
            # side of A) — take each wall's endpoint that projects onto the
            # gap boundary.
            p_a = _endpoint_at_proj(a, ua, oa, gap_lo, gap_hi)
            p_b = _endpoint_at_proj(b, ua, oa, gap_lo, gap_hi)
            handles: dict[tuple[str, str, str], SourceHandleRef] = {}
            for h in (*a.source_handles, *b.source_handles):
                handles[(h.format.value, h.sheet_ref, h.entity_ref)] = h
            bridges.append(JunctionLink(
                point=p_a,
                connectors=((p_a, p_b),),
                source_handles=tuple(sorted(
                    handles.values(),
                    key=lambda h: (h.format.value, h.sheet_ref, h.entity_ref),
                )),
                corroborating_handles=tuple(sorted(
                    {h.entity_ref: h for h in corroborators}.values(),
                    key=lambda h: (h.format.value, h.sheet_ref, h.entity_ref),
                )),
            ))
    bridges.sort(key=lambda L: (L.point, L.connectors[0]))
    return bridges, []


def complete_junctions(
    walls: list[WallCandidate],
    geometries: list[NormalizedGeometry] | None = None,
    *,
    junction_eps: float = JUNCTION_REACH_EPS,
) -> JunctionCompletionResult:
    """Complete legitimate wall-centerline junctions for room polygonization.

    Pure: walls (+ all sheet geometries for doorway evidence) in → junction
    links + refusals out. Never mutates or extends the walls; never emits a
    measurement. Callers feed the links to room detection alongside the
    walls' own centerlines.
    """
    if len(walls) < 2:
        return JunctionCompletionResult(junction_eps=junction_eps)
    perp_links, perp_amb = _perpendicular_links(walls, junction_eps=junction_eps)
    bridges: list[JunctionLink] = []
    if geometries is not None:
        bridges, _ = _doorway_bridges(
            walls, geometries, junction_eps=junction_eps
        )
    links = sorted((*perp_links, *bridges), key=lambda L: (L.point, L.connectors[0]))
    return JunctionCompletionResult(
        links=tuple(links),
        ambiguous=tuple(sorted(set(perp_amb))),
        junction_eps=junction_eps,
    )
