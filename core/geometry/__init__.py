"""Normalized geometry — the lingua franca between ingestion and takeoff.

docs/domain-model.md §Geometry: every normalized shape carries
  * coordinates in DRAWING UNITS (never pre-scaled — scale is human-gated),
  * a GeomType,
  * source handles: stable anchors back to raw file entities
    (DXF dxf.handle — our provenance differentiator),
  * an optional derived_from chain (geometry built from other geometry).

Pure data + pure functions. No framework, no I/O (determinism boundary).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any, cast

from core.domain.enums import GeomType, SourceFormat


def polygon_is_closed(ring: list[tuple[float, float]]) -> bool:
    """A ring is closed when first==last (explicitly, not implicitly)."""
    return len(ring) >= 4 and ring[0] == ring[-1]


def polygon_ring_area(ring: list[tuple[float, float]]) -> float:
    """Shoelace area of a ring in drawing units² (signed; abs() for magnitude).

    Zero-area rings are degenerate and never measured. Raises on open rings —
    area is refused, not guessed.
    """
    if not polygon_is_closed(ring):
        raise ValueError("open ring has no area (compute refused, not guessed)")
    s = 0.0
    for (x1, y1), (x2, y2) in pairwise(ring):
        s += x1 * y2 - x2 * y1
    return s / 2.0


@dataclass(frozen=True, slots=True)
class SourceHandleRef:
    """Flat mirror of core.provenance.SourceHandle for JSON serialization."""

    format: SourceFormat
    sheet_ref: str  # sheet id the entity lives on
    entity_ref: str  # DXF handle / path index / region id
    layer: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {
            "format": self.format.value,
            "sheet_ref": self.sheet_ref,
            "entity_ref": self.entity_ref,
            "layer": self.layer,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SourceHandleRef:
        return cls(
            format=SourceFormat(str(data["format"])),
            sheet_ref=str(data["sheet_ref"]),
            entity_ref=str(data["entity_ref"]),
            layer=data.get("layer"),
        )


def _pairs_to_json(coords: list[tuple[float, float]]) -> list[list[float]]:
    return [[x, y] for x, y in coords]


def _pairs_from_json(raw: Any) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in raw]


@dataclass(frozen=True, slots=True)
class NormalizedGeometry:
    """docs/domain-model.md §Geometry — one measured/measureable shape.

    coordinates: for point ([single]), line/polyline (open vertex list),
    polygon (closed ring), multi_polygon (list of rings).
    """

    geom_type: GeomType
    coordinates: list[tuple[float, float]] | list[list[tuple[float, float]]]
    source_format: SourceFormat
    source_handles: tuple[SourceHandleRef, ...]
    layer: str | None = None
    derived_from: tuple[str, ...] = ()  # geometry ids this was derived from

    def to_json(self) -> dict[str, Any]:
        """JSONB-ready representation (lists, not tuples)."""
        coords: Any
        if self.geom_type is GeomType.MULTI_POLYGON:
            rings = cast("list[list[tuple[float, float]]]", self.coordinates)
            coords = [_pairs_to_json(ring) for ring in rings]
        else:
            pts = cast("list[tuple[float, float]]", self.coordinates)
            coords = _pairs_to_json(pts)
        return {
            "geom_type": self.geom_type.value,
            "coordinates": coords,
            "source_format": self.source_format.value,
            "source_handles": [h.to_json() for h in self.source_handles],
            "layer": self.layer,
            "derived_from": list(self.derived_from),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> NormalizedGeometry:
        gtype = GeomType(str(data["geom_type"]))
        handles = tuple(
            SourceHandleRef.from_json(h) for h in data.get("source_handles", [])
        )
        coords: Any
        if gtype is GeomType.MULTI_POLYGON:
            coords = [_pairs_from_json(ring) for ring in data["coordinates"]]
        else:
            coords = _pairs_from_json(data["coordinates"])
        return cls(
            geom_type=gtype,
            coordinates=coords,
            source_format=SourceFormat(str(data["source_format"])),
            source_handles=handles,
            layer=data.get("layer"),
            derived_from=tuple(str(d) for d in data.get("derived_from", [])),
        )


@dataclass(frozen=True, slots=True)
class SheetSummary:
    """One parsed sheet: what is on it and whether it is measurable.

    Measurability rules (T031, pattern-inspired by OCErp's honest-unit stance —
    reimplemented fresh):
      * modelspace-first: the modelspace layout is the measurable sheet for V1;
        paper-space layouts are parsed but marked ambiguous (refuse to guess
        which viewport is the drawing),
      * a sheet with zero measurable entities is not measurable.
    """

    sheet_ref: str
    layout_name: str
    entity_count: int
    measurable_count: int
    is_modelspace: bool
    title: str | None = None
    unit_code: str | None = None  # from $INSUNITS, never guessed
    measurable: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "sheet_ref": self.sheet_ref,
            "layout_name": self.layout_name,
            "entity_count": self.entity_count,
            "measurable_count": self.measurable_count,
            "is_modelspace": self.is_modelspace,
            "title": self.title,
            "unit_code": self.unit_code,
            "measurable": self.measurable,
        }


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Complete output of parsing one drawing file.

    geometries are in drawing units with stable source handles.
    warnings: surfaced problems (skipped entities, ambiguous sheets) — never
    swallowed; the run engine converts them to exception records.
    """

    drawing_units: str  # mm|cm|m|in|ft — from the file, or "unknown"
    geometries: tuple[NormalizedGeometry, ...]
    sheets: tuple[SheetSummary, ...]
    warnings: tuple[str, ...] = ()
    source_sha256: str = ""  # immutable raw-file version, populated by ingestion
