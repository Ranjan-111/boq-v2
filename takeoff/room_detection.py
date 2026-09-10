"""Room/space polygonization from wall centerlines (T043) — OUR build.

docs/reuse-matrix.md: nothing in the reference polygonizes rooms; this module
is original work against docs/domain-model.md §Element (room, label from
drawing text with label_evidence) and the roadmap risk register:

  "Room polygonization edge cases (bowties, gaps) — self-intersection refusal
   (MEASURED never silently wrong); exceptions route to review."

Algorithm (deterministic, explainable, no AI):
  1. Node all detected wall CENTERLINES into a planar graph (shapely
     unary_union). Exact shared endpoints node; near-misses never do — the
     engine never snaps, extends, or repairs a wall network to close a room.
  2. polygonize the noded graph. Bounded faces are rooms; a face is bounded
     by wall CENTERLINES by construction, so its area is the GROSS room
     area (to centerline) — the standard QS gross definition.
  3. NET room area = gross region minus the wall footprints that sit inside
     it (the clear interior region, standard QS net/carpet definition).
  4. A face fully containing another face is a container (corridor around
     an enclosed room): its gross/net subtract the contained faces — the
     annulus, never the overstated bounding rectangle.

Refusal doctrine (never a guess):
  * fewer than 3 walls → no rooms and NO exception (a partial plan is normal
    input; absence is honest),
  * >=3 walls but zero closed faces → room_not_enclosed (the caller surfaces
    it REVIEW — an almost-room must reach the review queue),
  * polygonization/topology failures or degenerate faces → topology refusal
    strings the caller surfaces BLOCKING,
  * a net region that fragments or carries holes is refused, not simplified.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from shapely.errors import GEOSException
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from core.geometry import (
    NormalizedGeometry,
    SourceHandleRef,
    TextToken,
    polygon_ring_area,
)
from takeoff.wall_detection import WallCandidate, wall_footprint

# Tolerances in DRAWING UNITS — data, not magic: the caller binds them into
# the replay digest (R3 doctrine: selection parameters are recorded inputs).
BOUNDARY_OVERLAP_EPS = 1e-6  # min centerline-on-boundary overlap length
CONTAINMENT_EPS = 1e-9  # covers() tolerance for face-in-face nesting


class RoomTopologyError(ValueError):
    """The wall network cannot be polygonized honestly (never a guess)."""


@dataclass(frozen=True, slots=True)
class RoomRecord:
    """One enclosed room with its deterministic gross/net geometry.

    gross_ring / net_ring: closed rings (first == last) in drawing units.
    bounding_walls: indices into the caller's wall list, sorted ascending.
    label: from drawing text (label_evidence = label_token), else None.
    """

    gross_ring: tuple[tuple[float, float], ...]
    net_ring: tuple[tuple[float, float], ...]
    bounding_walls: tuple[int, ...]
    source_handles: tuple[SourceHandleRef, ...]
    label: str | None = None
    label_token: TextToken | None = None
    derived_from: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoomDetectionResult:
    rooms: tuple[RoomRecord, ...] = ()
    wall_count: int = 0
    not_enclosed: bool = False  # >=3 walls but zero closed faces
    topology_refusals: tuple[str, ...] = ()  # BLOCKING messages


def _canon_ring(ring: tuple[tuple[float, float], ...]) -> str:
    import json

    return json.dumps([[float(x), float(y)] for x, y in ring], separators=(",", ":"))


def _ring_of(polygon: Polygon) -> tuple[tuple[float, float], ...]:
    """Closed exterior ring as a tuple of pairs; holes are refused upstream."""
    coords = list(polygon.exterior.coords)
    ring = tuple((float(x), float(y)) for x, y in coords)
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise RoomTopologyError(f"polygonized face ring is not closed: {ring[:4]}…")
    return ring


def _polygon_from(ring: tuple[tuple[float, float], ...]) -> Polygon:
    return Polygon([(x, y) for x, y in ring])


def _area(ring: tuple[tuple[float, float], ...]) -> float:
    return abs(polygon_ring_area([tuple(p) for p in ring]))  # type: ignore[misc]


def detect_rooms(
    walls: list[WallCandidate],
    *,
    boundary_overlap_eps: float = BOUNDARY_OVERLAP_EPS,
    containment_eps: float = CONTAINMENT_EPS,
) -> RoomDetectionResult:
    """Polygonize wall centerlines into rooms; every refusal is surfaced.

    Pure: walls in → rooms + refusals out. No I/O, no clock, no AI.
    """
    if len(walls) < 3:
        return RoomDetectionResult(wall_count=len(walls))
    refusals: list[str] = []
    try:
        lines = [LineString(w.centerline) for w in walls]
        noded = unary_union(lines)
        parts = getattr(noded, "geoms", None)
        iterable = list(parts) if parts is not None else [noded]
        faces = [p for p in polygonize(iterable) if isinstance(p, Polygon)]
    except (GEOSException, ValueError) as exc:
        return RoomDetectionResult(
            wall_count=len(walls), topology_refusals=(f"polygonization failed: {exc}",)
        )

    raw_faces: list[Polygon] = []
    for face in faces:
        if not face.is_valid:
            refusals.append("polygonized face is invalid (self-intersecting noding)")
            continue
        if face.area <= 0 or not math.isfinite(face.area):
            refusals.append("polygonized face has zero or non-finite area")
            continue
        raw_faces.append(face)

    if not raw_faces:
        return RoomDetectionResult(
            wall_count=len(walls), not_enclosed=True, topology_refusals=tuple(refusals)
        )

    # Nesting: sort by area descending so containers resolve before contents;
    # ties broken by canonical ring so ordering is total and deterministic.
    ordered = sorted(raw_faces, key=lambda p: (-p.area, _canon_ring(_ring_of(p))))
    contained: dict[int, list[int]] = {i: [] for i in range(len(ordered))}
    for i, outer in enumerate(ordered):
        for j, inner in enumerate(ordered):
            if i == j or j in contained[i]:
                continue
            if outer.covers(inner.buffer(-containment_eps)):
                contained[i].append(j)

    bounding: dict[int, list[int]] = {}
    footprints: list[Polygon] = []
    for wi, wall in enumerate(walls):
        try:
            coords = cast(
                "list[tuple[float, float]]", wall_footprint(wall).coordinates
            )
            footprints.append(Polygon(coords))
        except (ValueError, GEOSException) as exc:  # pragma: no cover - wall invariant
            refusals.append(f"wall {wi} footprint unusable: {exc}")
            footprints.append(Polygon())  # sentinel; never intersects anything

    for fi, face in enumerate(ordered):
        hits: list[int] = []
        boundary = face.exterior
        for wi, wall in enumerate(walls):
            line = LineString(wall.centerline)
            try:
                if boundary.intersection(line).length > boundary_overlap_eps:
                    hits.append(wi)
            except GEOSException:  # pragma: no cover - defensive
                refusals.append(f"face {fi}/wall {wi} boundary test failed")
        bounding[fi] = sorted(hits)

    try:
        walls_union = unary_union([f for f in footprints if not f.is_empty])
    except GEOSException as exc:
        return RoomDetectionResult(
            wall_count=len(walls), topology_refusals=(*refusals, f"wall union failed: {exc}")
        )

    rooms: list[RoomRecord] = []
    for fi, face in enumerate(ordered):
        gross_region: BaseGeometry = face
        if contained[fi]:
            contained_polys = unary_union([ordered[cj] for cj in contained[fi]])
            gross_region = face.difference(contained_polys)
        net_region: BaseGeometry = gross_region.difference(walls_union)
        if gross_region.geom_type != "Polygon" or net_region.geom_type != "Polygon":
            refusals.append(
                f"room {fi} region fragmented ({gross_region.geom_type}/"
                f"{net_region.geom_type}) - refused, not simplified"
            )
            continue
        gross_poly = cast("Polygon", gross_region)
        net_poly = cast("Polygon", net_region)
        if net_poly.interiors or gross_poly.interiors:
            refusals.append(f"room {fi} region carries interior holes - refused")
            continue
        try:
            gross_ring = _ring_of(gross_poly)
            net_ring = _ring_of(net_poly)
        except RoomTopologyError as exc:
            refusals.append(str(exc))
            continue
        wall_indices = tuple(bounding[fi])
        if not wall_indices:
            refusals.append(f"room {fi} has no bounding walls (attribution refused)")
            continue
        handles: dict[tuple[str, str, str], SourceHandleRef] = {}
        for wi in wall_indices:
            for h in walls[wi].source_handles:
                handles[(h.format.value, h.sheet_ref, h.entity_ref)] = h
        rooms.append(RoomRecord(
            gross_ring=gross_ring,
            net_ring=net_ring,
            bounding_walls=wall_indices,
            source_handles=tuple(
                sorted(handles.values(), key=lambda h: (h.format.value, h.sheet_ref,
                                                        h.entity_ref))
            ),
            derived_from=tuple(sorted({h.entity_ref for wi in wall_indices
                                       for h in walls[wi].source_handles})),
        ))

    rooms.sort(key=lambda r: _canon_ring(r.gross_ring))
    return RoomDetectionResult(
        rooms=tuple(rooms),
        wall_count=len(walls),
        not_enclosed=False,
        topology_refusals=tuple(sorted(set(refusals))),
    )


def assign_room_labels(
    rooms: list[RoomRecord], text_tokens: tuple[TextToken, ...]
) -> None:
    """Label each room from the nearest text token strictly inside it.

    Deterministic tiebreak: (distance to room centroid, token text, handle).
    Rooms without an inside token stay unlabeled — a label is never guessed
    from tokens outside the room polygon (boundary tokens are excluded).
    Mutates the records via replacement (frozen dataclasses); returns None.
    """
    if not text_tokens:
        return
    for idx, room in enumerate(rooms):
        face = _polygon_from(room.gross_ring)
        centroid = face.centroid
        best: tuple[float, str, str, TextToken] | None = None
        for token in text_tokens:
            pt_x, pt_y = token.insertion
            if not all(math.isfinite(v) for v in (pt_x, pt_y)):
                continue
            from shapely.geometry import Point

            if not Point(pt_x, pt_y).within(face):
                continue
            d = Point(pt_x, pt_y).distance(centroid)
            key = (d, token.text, token.handle.entity_ref)
            if best is None or key < (best[0], best[1], best[2]):
                best = (d, token.text, token.handle.entity_ref, token)
        if best is not None:
            rooms[idx] = RoomRecord(
                gross_ring=room.gross_ring,
                net_ring=room.net_ring,
                bounding_walls=room.bounding_walls,
                source_handles=room.source_handles,
                label=best[1],
                label_token=best[3],
                derived_from=room.derived_from,
            )


def room_geometry(
    ring: tuple[tuple[float, float], ...],
    source_handles: tuple[SourceHandleRef, ...],
    *,
    source_format_value: str,
    sheet_ref: str,
    derived_from: tuple[str, ...],
) -> NormalizedGeometry:
    """Room ring → normalized POLYGON geometry (derived, full handle chain)."""
    from core.domain.enums import GeomType, SourceFormat

    coords: list[tuple[float, float]] = [(float(x), float(y)) for x, y in ring]
    return NormalizedGeometry(
        geom_type=GeomType.POLYGON,
        coordinates=coords,
        source_format=SourceFormat(source_format_value),
        source_handles=source_handles,
        derived_from=derived_from,
    )


def room_summary(record: RoomRecord) -> dict[str, Any]:
    """Detection-level summary for tests/reporting (not persisted)."""
    return {
        "label": record.label,
        "gross_area": round(_area(record.gross_ring), 6),
        "net_area": round(_area(record.net_ring), 6),
        "bounding_walls": list(record.bounding_walls),
    }
