"""PDF vector candidate detectors (T047) — advisory, never measured.

docs/domain-model.md §Measurement doctrine, applied to PDF vector sheets:
a PDF has no unit header and no semantic layer, so a closed ring here is
geometrically exact but semantically unproven — a rectangle is a room only
after a human says so. These detectors therefore emit CANDIDATES: pure
advisory outputs carrying their evidence (source handles) and an honest
confidence, surfaced for review.

# DOCTRINE GUARD (quoted, no assert — the tests pin it):
#   1. Candidates NEVER become MEASURED rows. They surface for review only.
#   2. An authoritative quantity requires a human-confirmed scale (the
#      human gate) plus a deterministic rule executed through the
#      versioned rule registry — neither of which lives here.
#   3. This module registers no rules, emits no MeasurementState, and
#      presents no quantity as authoritative: every value is in DRAWING
#      UNITS (PDF points / pt²) and labeled candidate-only.

Refusals are honest, never silent: rings the measurement kernel refuses
(open, <3 distinct vertices, self-intersecting / "bowtie") are refused as
candidates too — the same gate measurements use (takeoff.kernel), so a
candidate can never claim an area the engine itself would refuse. The
engine independently surfaces those refusals as exception records, so a
skipped candidate is a review surfaced elsewhere, not a dropped fact.

Confidence is a declared constant, not a computed score: the geometry is
exact but "is this a room?" is not provable from bytes, so area/length
carry 0.5 and count-by-example 0.7 (a matched pattern is stronger evidence
than a lone shape, but still heuristic). Deterministic: same inputs →
identical outputs (pure functions, no I/O, no clock, no randomness).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from core.domain.enums import GeomType
from core.geometry import NormalizedGeometry, SourceHandleRef
from takeoff.kernel import NotMeasurable, area_of, length_of

# Arbitrary-but-declared confidence constants (see module docstring):
# vector geometry is exact, whether a PDF shape is a *room* is NOT.
AREA_LENGTH_CANDIDATE_CONFIDENCE = 0.5
COUNT_CANDIDATE_CONFIDENCE = 0.7
# count-by-example tolerance: bbox width AND height equal to 1e-6 pt.
BBOX_MATCH_TOLERANCE_PT = 1e-6

# bbox = (x_min, y_min, x_max, y_max) in pdfplumber-native (x, top)
# coordinates — the frame ingestion captured, never converted.
Bbox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PdfAreaCandidate:
    """Advisory area candidate in DRAWING UNITS² (PDF points squared)."""

    area_pt2: float
    bbox: Bbox
    source_handles: tuple[SourceHandleRef, ...]
    confidence: float
    why: str


@dataclass(frozen=True, slots=True)
class PdfLengthCandidate:
    """Advisory length candidate in DRAWING UNITS (PDF points)."""

    length_pt: float
    source_handles: tuple[SourceHandleRef, ...]
    confidence: float
    why: str


@dataclass(frozen=True, slots=True)
class PdfCountCandidate:
    """Advisory count-by-example candidate: N shapes matching one seed."""

    count: int
    pattern_ref: str  # the seed geometry's handle — count-by-example
    source_handles: tuple[SourceHandleRef, ...]
    confidence: float
    why: str


def _ring_of(geom: NormalizedGeometry) -> list[tuple[float, float]] | None:
    """Vertex list of a non-multi geometry; None for multi_polygons.

    Multi-polygons are refused as candidates (one candidate = one shape,
    the same stance the kernel takes for measurements).
    """
    if geom.geom_type is GeomType.MULTI_POLYGON:
        return None
    coords = cast("list[tuple[float, float]]", geom.coordinates)
    return [(float(p[0]), float(p[1])) for p in coords]


def _bbox(points: list[tuple[float, float]]) -> Bbox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _handles_of(geom: NormalizedGeometry) -> tuple[SourceHandleRef, ...]:
    return geom.source_handles


def detect_closed_area_candidates(
    geometries: list[NormalizedGeometry],
) -> list[PdfAreaCandidate]:
    """Closed polygons → area candidates (shoelace, drawing units²).

    Kernel-refused rings (open, degenerate, self-intersecting) produce NO
    candidate — the engine surfaces those refusals as exceptions, so this
    never understates a bowtie's true area. Output order follows input
    order (deterministic: callers pass the parser's tuple order).
    """
    out: list[PdfAreaCandidate] = []
    for geom in geometries:
        if geom.geom_type is not GeomType.POLYGON:
            continue
        try:
            area = area_of(geom)
        except NotMeasurable:
            continue  # refused by the same gate measurements use — see above
        ring = _ring_of(geom)
        if ring is None:  # pragma: no cover - POLYGON is never multi here
            continue
        out.append(PdfAreaCandidate(
            area_pt2=area,
            bbox=_bbox(ring),
            source_handles=_handles_of(geom),
            confidence=AREA_LENGTH_CANDIDATE_CONFIDENCE,
            why=(
                f"closed polygon ring with {len(ring)} points; shoelace area "
                "in drawing units (pt squared), candidate-only"
            ),
        ))
    return out


def detect_length_candidates(
    geometries: list[NormalizedGeometry],
) -> list[PdfLengthCandidate]:
    """Straight open polylines → length candidates (drawing units).

    Closed polygons are deliberately not length candidates: a ring's
    perimeter is a different quantity than a run length, and conflating
    them would smuggle a semantic guess into an advisory layer.
    """
    out: list[PdfLengthCandidate] = []
    for geom in geometries:
        if geom.geom_type is not GeomType.POLYLINE:
            continue
        try:
            length = length_of(geom)
        except NotMeasurable:
            continue
        out.append(PdfLengthCandidate(
            length_pt=length,
            source_handles=_handles_of(geom),
            confidence=AREA_LENGTH_CANDIDATE_CONFIDENCE,
            why=(
                f"straight polyline with {len(_ring_of(geom) or [])} vertices; "
                "segment-sum length in drawing units (pt), candidate-only"
            ),
        ))
    return out


def count_by_example(
    geometries: list[NormalizedGeometry], seed_handle: str,
) -> PdfCountCandidate | None:
    """Count shapes whose bbox matches the seed's (exact width AND height).

    "Count-by-example": the user marks one seed geometry (its handle —
    e.g. one door symbol) and every geometry whose bounding box has the
    same width AND height (within BBOX_MATCH_TOLERANCE_PT) is counted,
    seed included. Returns None when the seed handle matches no geometry —
    the caller surfaces that refusal; this module never guesses a seed.
    source_handles is ordered by entity_ref (deterministic across calls).
    """
    seed = next(
        (g for g in geometries
         if any(h.entity_ref == seed_handle for h in g.source_handles)),
        None,
    )
    if seed is None:
        return None
    seed_ring = _ring_of(seed)
    if seed_ring is None:
        return None
    seed_bbox = _bbox(seed_ring)
    seed_w = seed_bbox[2] - seed_bbox[0]
    seed_h = seed_bbox[3] - seed_bbox[1]
    matched: list[NormalizedGeometry] = []
    for geom in geometries:
        ring = _ring_of(geom)
        if ring is None:
            continue
        box = _bbox(ring)
        width_matches = abs((box[2] - box[0]) - seed_w) <= BBOX_MATCH_TOLERANCE_PT
        height_matches = abs((box[3] - box[1]) - seed_h) <= BBOX_MATCH_TOLERANCE_PT
        if width_matches and height_matches:
            matched.append(geom)
    handles = sorted(
        (h for g in matched for h in g.source_handles), key=lambda h: h.entity_ref,
    )
    return PdfCountCandidate(
        count=len(matched),
        pattern_ref=seed_handle,
        source_handles=tuple(handles),
        confidence=COUNT_CANDIDATE_CONFIDENCE,
        why=(
            f"count-by-example: {len(matched)} geometries match the seed bbox "
            f"width {seed_w} and height {seed_h} (pt, within "
            f"{BBOX_MATCH_TOLERANCE_PT}, seed included), candidate-only"
        ),
    )


__all__ = [
    "AREA_LENGTH_CANDIDATE_CONFIDENCE",
    "BBOX_MATCH_TOLERANCE_PT",
    "COUNT_CANDIDATE_CONFIDENCE",
    "Bbox",
    "PdfAreaCandidate",
    "PdfCountCandidate",
    "PdfLengthCandidate",
    "count_by_example",
    "detect_closed_area_candidates",
    "detect_length_candidates",
]
