"""Opening detection (T045) — doors/windows in detected walls — OUR build.

docs/reuse-matrix.md: the reference detects openings inside its proprietary
DWG path; our detector is original work against docs/domain-model.md.

Two deterministic sources, both honest:

  1. NAMED BLOCKS: geometry exploded from an INSERT of a door/window-named
     block (ingestion.dxf.is_opening_block_name — Worker B contract). The
     placed member geometry's bounding box spans a wall footprint. A FULL
     span (bbox covers the wall thickness at that position) is a measured
     opening; a PARTIAL span (bbox crosses the centerline but pokes out, or
     fails to cross) is surfaced, never silently under-deducted.

  2. FACE GAPS: a wall whose drawn face pair carries a gap at the same
     longitudinal span on both faces (the honest drawing of a doorway).
     Round 3 requires congruent support for pairing; the wall_with_doorway
     fixture draws the faces SPLIT into two segments each, so the pairing
     yields two shorter walls — the gap between them (aligned on both
     faces within tolerance) is the opening.

Refusal doctrine (never a guess):
  * an opening whose extent does not resolve to one wall → OPENING_AMBIGUOUS
    (surfaced, not assigned to the nearest wall by hope),
  * a named-block bbox that only partially spans the wall →
    OPENING_PARTIAL_SPAN (the deduction is still computed from the drawn
    bbox — the geometry is evidence — but the state surfaces for review),
  * zero-thickness or non-finite extents are refused, never clamped.

All math is float64 over drawing units; determinism is total given the same
wall list + geometry list order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.wall_detection import WallCandidate

# How far a block bbox edge may sit from the wall face plane and still count
# as spanning it (drawing units; generous because block origins vary).
SPAN_EPS = 1.0
# Longitudinal alignment tolerance between the two faces' gap edges (units).
GAP_ALIGN_EPS = 1.0
# How far the block bbox may extend beyond the gap edges (units).
GAP_MATCH_EPS = 1.0

# Layer names we treat as opening members (case-insensitive substring).
OPENING_LAYER_HINTS = ("door", "window", "opening", "ventilator", "shutter")

# Named-block pattern (mirror of ingestion.dxf.OPENING_BLOCK_PATTERNS — kept
# here so the detector does not depend on ingestion at runtime; the parser is
# the authority on which INSERTs these names came from via source handles).
BLOCK_NAME_HINTS = ("D", "W", "DOOR", "WINDOW", "DR", "WIN")


@dataclass(frozen=True, slots=True)
class OpeningRecord:
    """One detected opening inside one wall — with full provenance.

    width/height are the OPENING's clear dimensions in drawing units
    (bbox-derived for blocks; gap extent for face gaps). area = width x
    min(height, wall thickness) — the deductible area can never exceed the
    wall's own footprint depth. kind: door | window (from layer/block hint)
    or "opening" when honestly unknown.
    """

    wall_index: int
    kind: str
    width: float
    height: float
    area: float
    center_distance: float  # opening center along the wall centerline (units)
    source_handles: tuple[SourceHandleRef, ...]
    full_span: bool
    method: str  # "named_block" | "face_gap"


@dataclass(frozen=True, slots=True)
class OpeningDetectionResult:
    openings: tuple[OpeningRecord, ...] = ()
    ambiguous: tuple[str, ...] = ()  # OPENING_AMBIGUOUS messages (REVIEW)
    partial_span: tuple[str, ...] = ()  # OPENING_PARTIAL_SPAN messages (REVIEW)
    slots: tuple[NormalizedGeometry, ...] = ()
    """slot geometries, index-aligned with openings (the deducted rectangles
    clipped to the wall footprint) — the replayable deduction inputs (T046)."""


def _slot_geometry(
    opening: OpeningRecord, wall: WallCandidate
) -> NormalizedGeometry | None:
    """The deductible rectangle: opening span on the wall's line, full thickness.

    Pure geometry: the rectangle at [center_distance ± width/2] along the
    wall's infinite line, spanning the full thickness. This is the replay
    input the wall.net.area.v1 rule subtracts — deterministic from wall +
    opening. Returns None for degenerate extents (never a guess).

    The span is NOT clipped to this wall's own extent: a face-gap opening
    lives BETWEEN two collinear walls (past the host wall's end), and its
    slot overlaps the gap region the drawn faces leave empty. The net-area
    rule subtracts slot area from the HOST wall's footprint; for face gaps
    the slot lies outside the host footprint, so the subtraction is honest
    (area outside the footprint subtracts zero — the geometry itself
    encodes this, no special-casing).
    """
    u, origin, _length = _centerline_params(wall)
    half_w = opening.width / 2
    cx = origin[0] + u[0] * opening.center_distance
    cy = origin[1] + u[1] * opening.center_distance
    h = wall.thickness / 2
    nx, ny = -u[1], u[0]
    p1 = (cx - u[0] * half_w + nx * h, cy - u[1] * half_w + ny * h)
    p2 = (cx + u[0] * half_w + nx * h, cy + u[1] * half_w + ny * h)
    p3 = (cx + u[0] * half_w - nx * h, cy + u[1] * half_w - ny * h)
    p4 = (cx - u[0] * half_w - nx * h, cy - u[1] * half_w - ny * h)
    ring = [p1, p2, p3, p4, p1]
    if not all(math.isfinite(v) for p in ring for v in p):
        return None
    from core.domain.enums import GeomType

    return NormalizedGeometry(
        geom_type=GeomType.POLYGON,
        coordinates=ring,
        source_format=opening.source_handles[0].format
        if opening.source_handles else wall.edge_geometries[0].source_format,
        source_handles=opening.source_handles or wall.source_handles,
        derived_from=(),
    )


def _is_opening_layer(layer: str | None) -> bool:
    if layer is None:
        return False
    low = layer.lower()
    return any(h in low for h in OPENING_LAYER_HINTS)


def is_opening_block_name(name: str) -> bool:
    """Mirror of ingestion.dxf.is_opening_block_name (runtime-independent)."""
    low = name.lower()
    return any(
        low == h.lower() or low.startswith(h.lower()) for h in BLOCK_NAME_HINTS
    )


def _bbox(geom: NormalizedGeometry) -> tuple[float, float, float, float]:
    if geom.geom_type.value == "multi_polygon":
        rings = cast("list[list[tuple[float, float]]]", geom.coordinates)
        pts = [p for ring in rings for p in ring]
    else:
        pts = cast("list[tuple[float, float]]", geom.coordinates)
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _opening_kind(layer: str | None, block_names: dict[str, str]) -> str:
    """door | window | opening — honest: unknown when neither hints."""
    sources = " ".join([layer or "", *block_names.values()]).lower()
    if "door" in sources:
        return "door"
    if "window" in sources:
        return "window"
    if any(k in sources for k in ("ventilator", "shutter", "opening")):
        return "opening"
    return "opening"


def _centerline_params(
    wall: WallCandidate,
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """(unit direction, origin, length) of the wall centerline."""
    (x0, y0), (x1, y1) = wall.centerline
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    if n == 0:
        raise ValueError("zero-length centerline")
    return (dx / n, dy / n), (x0, y0), n


def _project(u: tuple[float, float], origin: tuple[float, float], p: tuple[float, float]) -> float:
    return (p[0] - origin[0]) * u[0] + (p[1] - origin[1]) * u[1]


def _perp(u: tuple[float, float], origin: tuple[float, float], p: tuple[float, float]) -> float:
    return -(p[0] - origin[0]) * u[1] + (p[1] - origin[1]) * u[0]


def _deduct_area(width: float, height: float, thickness: float) -> float:
    """Deductible area: width x min(height, thickness) — never more wall
    than the wall itself."""
    return width * min(height, thickness)


@dataclass(frozen=True, slots=True)
class _BlockPlacement:
    """A door/window-named block's placed bbox, in wall-line frames.

    line_index: the wall whose infinite LINE the block sits on (may exceed
    the wall's finite extent — a block in a collinear gap between two walls
    sits on both walls' shared line). lon_a: (lo, hi) longitudinal span of
    the bbox measured along that wall's frame. perp extent: (0-or-lo, hi) as
    the perpendicular band the bbox covers off that centerline.
    """

    insert_handle: str
    block_name: str
    kind: str
    members: tuple[NormalizedGeometry, ...]
    x0: float
    y0: float
    x1: float
    y1: float
    line_index: int
    lon: tuple[float, float]
    perp_band: tuple[float, float]
    full_span: bool


def _perp_band(
    u: tuple[float, float], origin: tuple[float, float],
    x0: float, y0: float, x1: float, y1: float,
    thickness: float,
) -> tuple[float, float] | None:
    """The perpendicular band [lo, hi] the bbox covers off the centerline.

    None when the bbox does not touch the wall's line corridor at all.
    A bbox straddling the centerline covers band [0, max]; one wholly on one
    side covers [min, max] of its signed distances.
    """
    signed = [_perp(u, origin, (px, py))
              for px in (x0, x1) for py in (y0, y1)]
    if min(signed) > 0:  # wholly on one side
        return (min(abs(s) for s in signed), max(abs(s) for s in signed))
    if max(signed) < 0:  # wholly on the other side
        return (min(abs(s) for s in signed), max(abs(s) for s in signed))
    # Straddles the centerline: band starts at 0.
    return (0.0, max(abs(s) for s in signed))


def _named_block_openings(
    walls: list[WallCandidate],
    geometries: list[NormalizedGeometry],
    block_names: dict[str, str],
) -> tuple[
    list[OpeningRecord], list[str], list[str], list[_BlockPlacement]
]:
    """Openings from door/window-named block placements spanning walls.

    One opening unit = ALL members of one INSERT (its leading source-handle
    chain), unioned into one bounding box. Individual members are lines with
    degenerate 1-D bboxes; the block as placed is the drawn opening.

    Returns additionally the placements that sit on a wall LINE but outside
    every wall's finite extent — the gap corroborators (a door block drawn
    in a collinear gap between two wall runs).
    """
    openings: list[OpeningRecord] = []
    ambiguous: list[str] = []
    partial: list[str] = []
    corroborators: list[_BlockPlacement] = []
    # Group member geometries by their INSERT handle (first in the chain).
    by_insert: dict[str, list[NormalizedGeometry]] = {}
    for geom in geometries:
        if not geom.source_handles:
            continue
        insert_handle = geom.source_handles[0].entity_ref
        by_insert.setdefault(insert_handle, []).append(geom)
    for insert_handle, members in sorted(by_insert.items()):
        block_name = block_names.get(insert_handle)
        if block_name is None or not is_opening_block_name(block_name):
            continue
        xs: list[float] = []
        ys: list[float] = []
        for geom in members:
            bx0, by0, bx1, by1 = _bbox(geom)
            xs.extend((bx0, bx1))
            ys.extend((by0, by1))
        if not xs or not all(math.isfinite(v) for v in (*xs, *ys)):
            ambiguous.append(f"block {block_name!r} insert {insert_handle} non-finite bbox")
            continue
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            ambiguous.append(
                f"block {block_name!r} insert {insert_handle} has degenerate bbox"
            )
            continue
        # Candidate walls: evaluate the bbox in each wall's local frame.
        candidates: list[tuple[float, int, bool]] = []
        on_line: list[tuple[int, tuple[float, float], tuple[float, float], bool]] = []
        for wi, wall in enumerate(walls):
            u, origin, length = _centerline_params(wall)
            band = _perp_band(u, origin, x0, y0, x1, y1, wall.thickness)
            if band is None:
                continue
            lo_band, hi_band = band
            if lo_band > wall.thickness / 2 + SPAN_EPS:
                continue  # bbox never touches this wall's band
            projs = [
                _project(u, origin, (x0, y0)), _project(u, origin, (x1, y0)),
                _project(u, origin, (x0, y1)), _project(u, origin, (x1, y1)),
            ]
            lo, hi = min(projs), max(projs)
            # Longitudinal overlap with the wall's finite extent [0, length].
            ov_lo, ov_hi = max(lo, 0.0), min(hi, length)
            if ov_hi > ov_lo:
                covers = lo_band <= SPAN_EPS
                beyond = hi_band >= wall.thickness / 2 - SPAN_EPS
                full_span = covers and beyond
                candidates.append((ov_hi - ov_lo, wi, full_span))
                on_line.append((wi, (lo, hi), band, full_span))
            elif hi > -GAP_MATCH_EPS and lo < length + GAP_MATCH_EPS:
                # On the wall's line but beyond its extent (a gap block).
                on_line.append((wi, (lo, hi), band, True))
        if candidates:
            candidates.sort(key=lambda c: (-c[0], c[1]))
            if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < GAP_MATCH_EPS:
                ambiguous.append(
                    f"block {block_name!r} insert {insert_handle} ambiguously spans walls "
                    f"{candidates[0][1]} and {candidates[1][1]}"
                )
                continue
            _, wi, full_span = candidates[0]
            wall = walls[wi]
            if not full_span:
                partial.append(
                    f"block {block_name!r} insert {insert_handle} only partially spans wall {wi}"
                )
            u, origin, _ = _centerline_params(wall)
            # Opening extents in the HOST wall's local frame: longitudinal
            # = the bbox's projected span; perp = the band it covers.
            projs = [
                _project(u, origin, (x0, y0)), _project(u, origin, (x1, y0)),
                _project(u, origin, (x0, y1)), _project(u, origin, (x1, y1)),
            ]
            lon_lo, lon_hi = min(projs), max(projs)
            band = _perp_band(u, origin, x0, y0, x1, y1, wall.thickness) or (0.0, 0.0)
            width = lon_hi - lon_lo
            height = band[1] - band[0]
            if width <= 0 or height <= 0:  # pragma: no cover - degenerate guard
                ambiguous.append(
                    f"block {block_name!r} insert {insert_handle} degenerate wall-frame extent"
                )
                continue
            center_distance = (lon_lo + lon_hi) / 2
            openings.append(OpeningRecord(
                wall_index=wi,
                kind=_opening_kind(members[0].layer, {insert_handle: block_name}),
                width=width,
                height=height,
                area=_deduct_area(width, height, wall.thickness),
                center_distance=center_distance,
                source_handles=tuple(h for g in members for h in g.source_handles),
                full_span=full_span,
                method="named_block",
            ))
            continue
        # No wall hosts it; if it sits on some wall's LINE (beyond extent),
        # it is a gap corroborator. The nearest line wins (deterministic).
        if on_line:
            on_line.sort(key=lambda t: (t[0], t[1]))
            wi, lon, band, full_span = on_line[0]
            corroborators.append(_BlockPlacement(
                insert_handle=insert_handle,
                block_name=block_name,
                kind=_opening_kind(members[0].layer, {insert_handle: block_name}),
                members=tuple(members),
                x0=x0, y0=y0, x1=x1, y1=y1,
                line_index=wi,
                lon=lon,
                perp_band=band,
                full_span=full_span,
            ))
        else:
            ambiguous.append(
                f"block {block_name!r} insert {insert_handle} spans no detected wall"
            )
    return openings, ambiguous, partial, corroborators


def _face_gap_openings(
    walls: list[WallCandidate],
    corroborators: list[_BlockPlacement],
) -> tuple[list[OpeningRecord], list[str], list[str]]:
    """Openings from collinear wall-face gaps CORROBORATED by named blocks.

    Doctrine: a bare collinear gap between two wall runs is NOT an opening —
    two separate buildings' walls is equally plausible (the multi_storey
    fixture demonstrates it), and counting a phantom opening would feed the
    BOQ a wrong quantity. A gap becomes a counted face-gap opening ONLY when
    a door/window-named block sits in the gap span (drawn evidence, not
    geometry hope). Bare gaps surface as ambiguous (REVIEW).
    """
    openings: list[OpeningRecord] = []
    ambiguous: list[str] = []
    partial: list[str] = []
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, b = walls[i], walls[j]
            if abs(a.thickness - b.thickness) > 1e-6:
                continue
            ua, oa, la = _centerline_params(a)
            ub, ob, lb = _centerline_params(b)
            # Collinear: same direction (either orientation) and B's origin
            # on A's line.
            aligned = abs(abs(ua[0] * ub[0] + ua[1] * ub[1]) - 1.0) < 1e-6
            if not aligned:
                continue
            on_line = abs(_perp(ua, oa, ob)) < a.thickness
            if not on_line:
                continue
            # Longitudinal gap between A and B along A's direction.
            b_end = (ob[0] + ub[0] * lb, ob[1] + ub[1] * lb)
            projs_b = [_project(ua, oa, ob), _project(ua, oa, b_end)]
            lo_b, hi_b = min(projs_b), max(projs_b)
            if lo_b > la:  # B starts after A ends: gap = lo_b - la
                gap_lo, gap_hi = la, lo_b
            elif hi_b < 0:  # B ends before A starts
                gap_lo, gap_hi = hi_b, 0.0
            else:
                continue  # overlapping or touching — not a gap
            gap = gap_hi - gap_lo
            if gap <= 1e-6:
                continue
            # Corroboration: a gap block placement on either wall's line,
            # whose longitudinal span overlaps this gap span. Corroborators
            # carry lon in their OWN host line's frame; both walls share the
            # line (collinear), and A's frame measures the same axis.
            corroborated = None
            for cor in corroborators:
                if cor.line_index not in (i, j):
                    continue
                if cor.lon[0] < gap_hi - GAP_MATCH_EPS and cor.lon[1] > gap_lo + GAP_MATCH_EPS:
                    corroborated = cor
                    break
            mid = (gap_lo + gap_hi) / 2
            if corroborated is None:
                ambiguous.append(
                    f"uncorroborated collinear gap {gap:.6g} between walls {i} and {j}"
                    " (no door/window block in the gap span)"
                )
                continue
            fa = list(a.edge_geometries)
            fb = list(b.edge_geometries)
            if len(fa) != 2 or len(fb) != 2:
                continue
            openings.append(OpeningRecord(
                wall_index=i,
                kind=corroborated.kind,
                width=float(gap),
                height=float(a.thickness),
                area=_deduct_area(float(gap), float(a.thickness), a.thickness),
                center_distance=mid,
                source_handles=tuple(
                    h for g in (*fa, *fb, *corroborated.members)
                    for h in g.source_handles
                ),
                full_span=True,
                method="face_gap",
            ))
    # De-duplicate: the same gap discovered from both directions (i<->j).
    seen: set[tuple[int, float]] = set()
    unique: list[OpeningRecord] = []
    for o in openings:
        key = (o.wall_index, round(o.center_distance, 6))
        if key in seen:
            continue
        seen.add(key)
        unique.append(o)
    return unique, ambiguous, partial


def detect_openings(
    walls: list[WallCandidate],
    geometries: list[NormalizedGeometry],
    *,
    block_names: dict[str, str] | None = None,
) -> OpeningDetectionResult:
    """Detect openings in detected walls. Pure; refusals always surfaced.

    block_names: INSERT handle -> block name (from the parser; required for
    named-block detection — an honest empty dict means only face gaps run).
    """
    named = block_names or {}
    n_openings, n_amb, n_partial, corroborators = _named_block_openings(
        walls, geometries, named
    )
    g_openings, g_amb, g_partial = _face_gap_openings(walls, corroborators)
    # Named-block openings whose bbox spans a wall are the opening; gap-
    # corroborated collinear gaps ALSO produce a face_gap record whose slot
    # lies outside both footprints (the geometric subtraction in the rule
    # keeps the accounting honest — no double-counting).
    ordered_openings: list[OpeningRecord] = sorted(
        (*n_openings, *g_openings),
        key=lambda o: (o.wall_index, o.center_distance, o.method),
    )
    # Slot geometries, index-aligned with openings (replayable deductions).
    slot_list: list[NormalizedGeometry] = []
    for o in ordered_openings:
        if o.wall_index >= len(walls):  # pragma: no cover - bound by detector
            continue
        slot = _slot_geometry(o, walls[o.wall_index])
        if slot is not None:
            slot_list.append(slot)
    return OpeningDetectionResult(
        openings=tuple(ordered_openings),
        ambiguous=(*n_amb, *g_amb),
        partial_span=(*n_partial, *g_partial),
        slots=tuple(slot_list),
    )
