"""Takeoff kernel — deterministic primitives (T040).

docs/architecture.md determinism boundary: pure functions, no I/O, no clock,
no AI. Shapely is the geometry engine (BSD); all public math is exact where
it matters (length/area in drawing units as float64; project-unit conversion
happens in core.units with Decimal).

Refusal doctrine (reuse-matrix #6 / testing-strategy §4): a self-intersecting
or invalid polygon is NEVER silently understated — the kernel raises
NotMeasurable and the engine converts it into a NOT_MEASURABLE measurement
with a SELF_INTERSECTING exception.
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString, Polygon

from core.geometry import NormalizedGeometry, polygon_ring_area


class NotMeasurable(ValueError):
    """The kernel refuses to compute this geometry (never guesses)."""


@dataclass(frozen=True, slots=True)
class KernelResult:
    """A deterministic quantity in DRAWING UNITS (unconverted)."""

    length: float | None = None  # drawing units
    area: float | None = None  # drawing units squared
    closed: bool = False
    vertex_count: int = 0


def _ring_of(geom: NormalizedGeometry) -> list[tuple[float, float]]:
    coords = geom.coordinates
    if geom.geom_type.value == "multi_polygon":
        raise NotMeasurable("multi-polygon not measurable as one ring")
    return [tuple(p) for p in coords]  # type: ignore[misc]


def length_of(geom: NormalizedGeometry) -> float:
    """Polyline length in drawing units. Works open or closed."""
    ring = _ring_of(geom)
    if len(ring) < 2:
        raise NotMeasurable("fewer than 2 vertices")
    return float(LineString(ring).length)


def area_of(geom: NormalizedGeometry) -> float:
    """Polygon area in drawing units squared. REFUSES open/self-intersecting rings.

    This is the honesty gate: a bowtie's |shoelace| would silently understate
    the true enclosed area, so instead we raise and let the engine emit
    SELF_INTERSECTING / OPEN_POLYLINE exceptions for human review.
    """
    ring = _ring_of(geom)
    if geom.geom_type.value != "polygon":
        raise NotMeasurable("area requires a polygon")
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise NotMeasurable("ring not closed")
    poly = Polygon(ring)
    if not poly.is_valid:
        raise NotMeasurable("self-intersecting ring")
    return float(abs(polygon_ring_area(ring)))


def count_of(geoms: list[NormalizedGeometry]) -> int:
    """Count of distinct geometries (e.g. door blocks). Deterministic by nature."""
    return len(geoms)
