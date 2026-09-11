"""T048 — raster candidate detectors: advisory px-space proposals, never measurements.

The doctrines under test (mirrors tests/unit/test_pdf_candidates.py):

  * candidates are heuristic ADVISORY outputs — they never become MEASURED
    rows, register no rules, touch no database, and present no quantity as
    authoritative,
  * EVERY output is pixel-space: fields are *_px bboxes / lengths / areas;
    px→mm conversion is DELIBERATELY ABSENT (converting px to real units
    requires the human-confirmed scale — a different layer), which the
    dataclass field set pins structurally,
  * confidence NEVER exceeds 0.75 for cv-only detection — enforced by the
    candidate dataclasses at construction, and the declared constants sit
    below the cap,
  * every candidate label embeds "(verify)": pixel walls under
    perspective/skew are review items, not evidence,
  * determinism: same bytes → identical candidates, ordered by GEOMETRY,
    not discovery order,
  * refusals share the parser's gate exactly: empty, corrupt, truncated,
    zero-pixel, and over-50-MP inputs raise RasterParseError from the
    detectors too,
  * honest absence: a blank image yields ZERO candidates of either kind,
    not an error and not a guess.

Unit-marked: pure cv2 functions over committed fixtures, no DB, no network.
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from ingestion.raster import RasterParseError
from ingestion.raster.candidates import (
    MAX_CV_CONFIDENCE,
    MIN_ROOM_AREA_PX,
    MIN_WALL_SEGMENT_PX,
    ROOM_CANDIDATE_CONFIDENCE,
    WALL_CANDIDATE_CONFIDENCE,
    RoomCandidate,
    WallCandidate,
    detect_room_candidates,
    detect_wall_candidates,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "raster"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.unit
class TestWallCandidates:
    def test_plan_finds_wall_candidates(self) -> None:
        candidates = detect_wall_candidates(load("plan.png"))
        # 6px ring (4 sides) + 6px divider: at least one candidate per
        # wall stroke; robust band instead of an exact count (Hough
        # internals are cv2's, the CONTRACT is ours).
        assert len(candidates) >= 5
        assert len(candidates) <= 24, "a simple plan must not explode into noise"

    def test_labels_embed_verify(self) -> None:
        candidates = detect_wall_candidates(load("plan.png"))
        assert candidates
        assert all("(verify)" in c.label for c in candidates)

    def test_confidence_capped_below_0_75(self) -> None:
        candidates = detect_wall_candidates(load("plan.png"))
        assert candidates
        assert all(0.0 < c.confidence <= MAX_CV_CONFIDENCE for c in candidates)
        assert all(c.confidence == WALL_CANDIDATE_CONFIDENCE for c in candidates)
        assert WALL_CANDIDATE_CONFIDENCE <= MAX_CV_CONFIDENCE
        assert MAX_CV_CONFIDENCE == 0.75

    def test_outputs_are_px_only(self) -> None:
        # The px→mm conversion is deliberately absent: every numeric field
        # of the public contract is pixel-space and named accordingly.
        names = {f.name for f in fields(WallCandidate)}
        assert names == {
            "label", "segment_px", "length_px", "bbox_px", "confidence", "why",
        }
        assert all(
            name.endswith("_px") for name in names
            if name not in {"label", "confidence", "why"}
        )

    def test_segments_within_image_bounds_and_min_length(self) -> None:
        candidates = detect_wall_candidates(load("plan.png"))
        for c in candidates:
            (x0, y0), (x1, y1) = c.segment_px
            assert 0 <= x0 <= 400 and 0 <= x1 <= 400
            assert 0 <= y0 <= 300 and 0 <= y1 <= 300
            assert c.length_px >= MIN_WALL_SEGMENT_PX
            assert c.length_px == pytest.approx(
                ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, abs=1e-6,
            )

    def test_ordering_is_geometric_not_discovery(self) -> None:
        candidates = detect_wall_candidates(load("plan.png"))
        assert candidates == sorted(candidates, key=lambda c: c.segment_px)

    def test_blank_image_yields_no_walls(self) -> None:
        assert detect_wall_candidates(load("blank.png")) == []

    def test_construction_refuses_bad_confidence_and_label(self) -> None:
        with pytest.raises(ValueError, match="verify"):
            WallCandidate(
                label="wall candidate",  # "(verify)" missing — refused
                segment_px=((0.0, 0.0), (50.0, 0.0)),
                length_px=50.0, bbox_px=(0.0, 0.0, 50.0, 0.0),
                confidence=0.6, why="",
            )
        with pytest.raises(ValueError, match="confidence"):
            WallCandidate(
                label="wall candidate (verify)",
                segment_px=((0.0, 0.0), (50.0, 0.0)),
                length_px=50.0, bbox_px=(0.0, 0.0, 50.0, 0.0),
                confidence=0.9, why="",  # over the cv-only cap
            )


@pytest.mark.unit
class TestRoomCandidates:
    def test_plan_finds_two_rooms(self) -> None:
        # The divider splits the ring interior into exactly two enclosed
        # regions — a connected-component fact, exact-pinnable.
        rooms = detect_room_candidates(load("plan.png"))
        assert len(rooms) == 2
        areas = sorted(r.area_px for r in rooms)
        assert all(a > 30_000 for a in areas)  # ~176x250 px each
        assert all(a < 60_000 for a in areas)

    def test_rooms_are_strictly_inside_the_ink(self) -> None:
        rooms = detect_room_candidates(load("plan.png"))
        for r in rooms:
            x, y, w, h = r.bbox_px
            assert x > 0 and y > 0, "border-touching regions are margin, not rooms"
            assert x + w < 400 and y + h < 300
            assert r.area_px >= MIN_ROOM_AREA_PX
            assert 0 < r.confidence <= MAX_CV_CONFIDENCE
            assert "(verify)" in r.label

    def test_border_rect_finds_exactly_one_room(self) -> None:
        rooms = detect_room_candidates(load("border_rect.png"))
        assert len(rooms) == 1
        room = rooms[0]
        assert room.area_px == pytest.approx(36036, abs=1)  # 234x154 enclosed px
        assert room.bbox_px == pytest.approx((43, 43, 234, 154), abs=2)

    def test_room_confidence_constant(self) -> None:
        rooms = detect_room_candidates(load("plan.png"))
        assert rooms
        assert all(r.confidence == ROOM_CANDIDATE_CONFIDENCE for r in rooms)
        assert ROOM_CANDIDATE_CONFIDENCE <= MAX_CV_CONFIDENCE

    def test_outputs_are_px_only(self) -> None:
        names = {f.name for f in fields(RoomCandidate)}
        assert names == {
            "label", "bbox_px", "area_px", "centroid_px", "confidence", "why",
        }
        assert all(
            name.endswith("_px") for name in names
            if name not in {"label", "confidence", "why"}
        )

    def test_ordering_is_geometric_not_discovery(self) -> None:
        rooms = detect_room_candidates(load("plan.png"))
        assert rooms == sorted(rooms, key=lambda r: (r.bbox_px, r.area_px))

    def test_blank_image_yields_no_rooms(self) -> None:
        # The whole blank page is one margin touching the border — honest
        # absence of rooms, not a guess.
        assert detect_room_candidates(load("blank.png")) == []


@pytest.mark.unit
class TestSharedRefusals:
    """Candidates refuse EXACTLY like the parser — the gate is shared."""

    @pytest.mark.parametrize("detector", [detect_wall_candidates, detect_room_candidates])
    def test_empty_refused(self, detector) -> None:
        with pytest.raises(RasterParseError, match="empty input"):
            detector(b"")

    @pytest.mark.parametrize("detector", [detect_wall_candidates, detect_room_candidates])
    def test_garbage_refused(self, detector) -> None:
        with pytest.raises(RasterParseError):
            detector(b"clearly not an image")

    @pytest.mark.parametrize("detector", [detect_wall_candidates, detect_room_candidates])
    def test_truncated_refused(self, detector) -> None:
        with pytest.raises(RasterParseError, match="truncated or corrupt"):
            detector(load("truncated.png"))

    @pytest.mark.parametrize("detector", [detect_wall_candidates, detect_room_candidates])
    def test_oversized_refused_before_decode(self, detector) -> None:
        data = load("oversized.png")  # 45-byte header stub declaring 60 MP
        with pytest.raises(RasterParseError, match="decompression guard"):
            detector(data)


@pytest.mark.unit
class TestDeterminism:
    def test_same_bytes_identical_walls(self) -> None:
        data = load("plan.png")
        assert detect_wall_candidates(data) == detect_wall_candidates(bytes(data))

    def test_same_bytes_identical_rooms(self) -> None:
        data = load("plan.png")
        assert detect_room_candidates(data) == detect_room_candidates(bytes(data))

    def test_multi_frame_image_decodes_frame_one(self) -> None:
        # One sheet per image (the parser doctrine) — candidates see frame 1.
        walls = detect_wall_candidates(load("animated.gif"))
        assert len(walls) >= 4  # frame-1 rectangle border lines


@pytest.mark.unit
class TestNoMeasurementDoctrines:
    def test_module_registers_no_rules(self) -> None:
        # Candidates must never reach the rule registry: importing the
        # module leaves takeoff's registry untouched.
        import takeoff.rules as rules

        before = {r.rule_id for r in rules.all_rules()}
        import importlib

        importlib.reload(__import__(
            "ingestion.raster.candidates", fromlist=["detect_wall_candidates"],
        ))
        after = {r.rule_id for r in rules.all_rules()}
        assert before == after

    def test_no_unit_conversion_in_module(self) -> None:
        # px→mm conversion is deliberately absent: no mm/m2/unit tokens in
        # the module's public surface.
        import ingestion.raster.candidates as candidates_module

        public = [name for name in dir(candidates_module) if not name.startswith("_")]
        assert not any(
            token in name.lower() for name in public
            for token in ("mm", "meter", "metre", "m2", "unit")
        ), f"unit-bearing public names leaked: {public}"
