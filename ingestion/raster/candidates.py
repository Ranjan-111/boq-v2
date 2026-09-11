"""Raster candidate detectors (T048) — advisory pixel-space proposals, NEVER measurements.

# DOCTRINE GUARD (quoted, no assert — the tests pin it):
#   1. Candidates NEVER become MEASURED rows. They surface for review only.
#   2. Converting pixels to real units REQUIRES a human-confirmed scale
#      (the human gate) plus a deterministic rule through the versioned
#      registry — NEITHER of which lives here. px→mm conversion is
#      therefore DELIBERATELY ABSENT: every output field is pixel-space
#      and named *_px; nothing in this module knows a millimeter exists.
#   3. This module registers no rules, emits no MeasurementState, touches
#      no database, and converts nothing to drawing units.
#   4. Confidence is capped: cv-only detection NEVER exceeds 0.75, because
#      pixel walls under perspective, skew, or lens distortion are not
#      evidence — a straight dark stroke in a photo of a building looks
#      exactly like one in an orthographic plan. The cap is enforced by
#      the candidate dataclasses themselves (construction above 0.75
#      raises), not by convention.

Detection honesty, mirroring the R5 candidate pattern (takeoff/pdf_candidates):

  * `detect_wall_candidates`: Hough line segments over the binarized
    image, merged when collinear within a small perpendicular tolerance so
    the two edges of one thick wall stroke surface as ONE candidate. Each
    candidate label carries "(verify)". Sub-minimum strokes (shorter than
    `MIN_WALL_SEGMENT_PX`) are not surfaced — they are below the detector's
    declared evidence threshold, documented here rather than silently
    dropped in code. Wall thickness is deliberately NOT estimated: Hough
    endpoint jitter makes px thickness numbers unreliable, and the
    DXF-style reciprocal face-pairing doctrine needs exact vector geometry,
    not noisy pixels.
  * `detect_room_candidates`: closed-contour regions — connected white
    (non-ink) components fully enclosed by dark strokes. A region touching
    the image border is NOT a room candidate: it is indistinguishable from
    the sheet margin, and guessing would be exactly the silent fabrication
    this package refuses. Sub-`MIN_ROOM_AREA_PX` regions are not surfaced
    (same declared-threshold doctrine as walls).
  * Both detectors accept only decodable images and refuse identically to
    the parser (`ingestion.raster.decode_image` is the shared gate: empty,
    unidentifiable, truncated, zero-pixel, and over-`MAX_IMAGE_PIXELS`
    inputs all raise RasterParseError before a single Hough vote runs).

Determinism: same bytes → identical candidates. cv2's Hough/CCA are
deterministic for identical input, input segments are canonicalized
(left/top-most endpoint first) and processed in sorted order, and the
output lists are ordered by GEOMETRY (segment endpoints / bbox), never by
discovery order. Multi-frame images are decoded to frame 1 only (the
parser's one-sheet-per-image doctrine).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ingestion.raster import decode_image

# Declared detector parameters (pixel-space constants, V1 scan-assumption:
# plan-like rasters around 300+ px per drawing meter).
_HOUGH_RHO = 1.0
_HOUGH_THETA = math.pi / 360.0  # 0.5° resolution
_HOUGH_VOTES = 30
_HOUGH_MIN_LINE = 40.0
_HOUGH_MAX_GAP = 8.0
_MERGE_PERP_TOL_PX = 8.0  # merges the two edges of one ≤8 px wall stroke

MIN_WALL_SEGMENT_PX = 40.0  # below this a stroke is not wall evidence
MIN_ROOM_AREA_PX = 100  # below this an enclosed region is noise

# Confidence is a DECLARED constant, not a computed score (the R5 candidate
# idiom): cv-only pixel evidence is inherently unproven, so proposals sit
# well below the cap a human could mistake for near-certainty.
MAX_CV_CONFIDENCE = 0.75
WALL_CANDIDATE_CONFIDENCE = 0.6
ROOM_CANDIDATE_CONFIDENCE = 0.6

_WALL_LABEL = "wall candidate (verify)"
_ROOM_LABEL = "room candidate (verify)"

# bbox_px = (x, y, w, h) in PIXELS, top-left origin — the PIL image frame.
BboxPx = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class WallCandidate:
    """Advisory wall-segment proposal in PIXEL SPACE (never a measurement).

    Every numeric field is pixel-space and named *_px; px→mm conversion is
    deliberately absent (see module docstring). The "(verify)" note is
    embedded in the label because a pixel wall under perspective/skew is a
    review item, not evidence.
    """

    label: str
    segment_px: tuple[tuple[float, float], tuple[float, float]]
    length_px: float
    bbox_px: BboxPx
    confidence: float
    why: str

    def __post_init__(self) -> None:
        if "(verify)" not in self.label:
            raise ValueError("wall candidate label must embed '(verify)'")
        if not 0.0 < self.confidence <= MAX_CV_CONFIDENCE:
            raise ValueError(
                f"cv-only wall confidence {self.confidence} "
                f"must be in (0, {MAX_CV_CONFIDENCE}]"
            )
        if not math.isfinite(self.length_px) or self.length_px <= 0:
            raise ValueError("wall length_px must be finite and positive")
        (x0, y0), (x1, y1) = self.segment_px
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            raise ValueError("wall segment_px must be finite")


@dataclass(frozen=True, slots=True)
class RoomCandidate:
    """Advisory enclosed-region proposal in PIXEL SPACE (never a measurement).

    area_px is the pixel count of the white region fully enclosed by dark
    strokes (a connected-component fact, not a survey).
    """

    label: str
    bbox_px: BboxPx
    area_px: float
    centroid_px: tuple[float, float]
    confidence: float
    why: str

    def __post_init__(self) -> None:
        if "(verify)" not in self.label:
            raise ValueError("room candidate label must embed '(verify)'")
        if not 0.0 < self.confidence <= MAX_CV_CONFIDENCE:
            raise ValueError(
                f"cv-only room confidence {self.confidence} "
                f"must be in (0, {MAX_CV_CONFIDENCE}]"
            )
        if not math.isfinite(self.area_px) or self.area_px <= 0:
            raise ValueError("room area_px must be finite and positive")


def _canonical(seg: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    """Endpoint-ordered ((x1,y1),(x2,y2)) with the lexicographically smaller first."""
    x1, y1, x2, y2 = (float(v) for v in seg)
    p1, p2 = (x1, y1), (x2, y2)
    return (p1, p2) if p1 <= p2 else (p2, p1)


def _perp_distance(point: tuple[float, float], a: tuple[float, float],
                   u: tuple[float, float]) -> float:
    """Distance from `point` to the line through `a` with unit direction `u`."""
    return abs((point[0] - a[0]) * u[1] - (point[1] - a[1]) * u[0])


def _merge_collinear(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    perp_tol: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Greedy collinear merge in canonical order (deterministic).

    Two segments merge when all four cross-distances between their lines
    are within `perp_tol` — near-parallel and nearly coincident, i.e. two
    Hough readings of the SAME stroke (or the two edges of one thick
    stroke). The merged segment spans the union along the kept line's
    axis. Greedy over sorted input: same bytes → same result.
    """
    merged: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for seg in sorted(segments):
        placed = False
        for i, (a, b) in enumerate(merged):
            (ax, ay), (bx, by) = a, b
            (px, py), (qx, qy) = seg
            length = math.hypot(bx - ax, by - ay)
            seg_len = math.hypot(qx - px, qy - py)
            if length == 0 or seg_len == 0:
                continue
            ux, uy = (bx - ax) / length, (by - ay) / length
            vx, vy = (qx - px) / seg_len, (qy - py) / seg_len
            d = max(
                _perp_distance(seg[0], a, (ux, uy)), _perp_distance(seg[1], a, (ux, uy)),
                _perp_distance(a, seg[0], (vx, vy)), _perp_distance(b, seg[0], (vx, vy)),
            )
            if d < perp_tol:
                projections = sorted(
                    (((p[0] - ax) * ux + (p[1] - ay) * uy, p) for p in (a, b, seg[0], seg[1])),
                    key=lambda t: t[0],
                )
                merged[i] = (projections[0][1], projections[-1][1])
                placed = True
                break
        if not placed:
            merged.append(seg)
    return merged


def _gray_array(data: bytes) -> np.ndarray:
    """Decoded grayscale array (the shared decode gate runs first)."""
    image = decode_image(data)
    gray = image.convert("L")  # luminance; walls are dark strokes on light paper
    return np.asarray(gray)


def _bbox_of(p1: tuple[float, float], p2: tuple[float, float]) -> BboxPx:
    xs = (p1[0], p2[0])
    ys = (p1[1], p2[1])
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def detect_wall_candidates(image: bytes) -> list[WallCandidate]:
    """Hough-line wall segment candidates in PIXEL SPACE, ordered by geometry.

    Refuses (RasterParseError, identical to the parser) on empty,
    unidentifiable, truncated, zero-pixel, or over-50-MP inputs.
    """
    gray = _gray_array(image)
    # Binarize with Otsu (deterministic; no magic gray threshold), inverted
    # so dark wall strokes are foreground.
    _t, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    raw = cv2.HoughLinesP(
        ink, _HOUGH_RHO, _HOUGH_THETA, threshold=_HOUGH_VOTES,
        minLineLength=_HOUGH_MIN_LINE, maxLineGap=_HOUGH_MAX_GAP,
    )
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if raw is not None:
        segments = [_canonical(seg) for seg in raw]
    candidates: list[WallCandidate] = []
    for (a, b) in _merge_collinear(segments, _MERGE_PERP_TOL_PX):
        length = math.dist(a, b)
        if length < MIN_WALL_SEGMENT_PX:
            continue  # below the declared evidence threshold (module docstring)
        candidates.append(WallCandidate(
            label=_WALL_LABEL,
            segment_px=(a, b),
            length_px=length,
            bbox_px=_bbox_of(a, b),
            confidence=WALL_CANDIDATE_CONFIDENCE,
            why=(
                f"merged collinear Hough segment(s), {length:.1f} px long in the "
                "binarized image; pixel-space proposal, scale unconfirmed — verify"
            ),
        ))
    candidates.sort(key=lambda c: c.segment_px)  # order by geometry, not discovery
    return candidates


def detect_room_candidates(image: bytes) -> list[RoomCandidate]:
    """Closed-contour region candidates in PIXEL SPACE, ordered by geometry.

    A room candidate is a white (non-ink) connected component fully
    enclosed by dark strokes and not touching the image border (a border-
    touching region is indistinguishable from the sheet margin — honest
    absence, not a guess).
    """
    gray = _gray_array(image)
    height, width = gray.shape[0], gray.shape[1]
    _t, paper = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(paper, connectivity=4)
    candidates: list[RoomCandidate] = []
    for i in range(1, count):  # label 0 is the background of the mask itself
        x, y, w, h, area = (int(v) for v in stats[i])
        if x == 0 or y == 0 or x + w == width or y + h == height:
            continue  # touches the image border: the sheet margin, not a room
        if area < MIN_ROOM_AREA_PX:
            continue  # below the declared evidence threshold (module docstring)
        centroid = (float(centroids[i][0]), float(centroids[i][1]))
        candidates.append(RoomCandidate(
            label=_ROOM_LABEL,
            bbox_px=(float(x), float(y), float(w), float(h)),
            area_px=float(area),
            centroid_px=centroid,
            confidence=ROOM_CANDIDATE_CONFIDENCE,
            why=(
                f"white region of {area} px fully enclosed by dark strokes "
                "(connected-component fact); pixel-space proposal, scale "
                "unconfirmed — verify"
            ),
        ))
    candidates.sort(key=lambda c: (c.bbox_px, c.area_px))  # order by geometry
    return candidates


__all__ = [
    "MAX_CV_CONFIDENCE",
    "MIN_ROOM_AREA_PX",
    "MIN_WALL_SEGMENT_PX",
    "ROOM_CANDIDATE_CONFIDENCE",
    "WALL_CANDIDATE_CONFIDENCE",
    "RoomCandidate",
    "WallCandidate",
    "detect_room_candidates",
    "detect_wall_candidates",
]
