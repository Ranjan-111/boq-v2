"""T033 — raster parser tests: honest sheets, absolute refusals, zero measurement.

The doctrines under test:

  * geometries is ALWAYS () and text_tokens is ALWAYS () — no
    auto-measurement from pixels, ever (the T033 contract),
  * drawing_units is always "unknown" and unit_code is None — pixels carry
    no units and NO scale proposal exists for raster (NULL calibration is
    the human gate's business),
  * one image = one sheet with sheet_ref "image:1", is_modelspace=False,
    measurable=False / measurable_count=0 (the scanned-page stance),
  * every parse carries the honest "AI-vision/manual takeoff only" notice,
  * refusals are loud RasterParseError, never silent: empty input,
    corrupt/unidentifiable bytes, truncated pixel data, zero-pixel images,
    and the >50 MP decompression guard — refused from the header pre-read
    WITHOUT materializing pixels,
  * deterministic: same bytes → identical ParseResult, sha256-anchored.

Unit-marked: pure parsing, no DB. Pillow is a real dependency, never
mocked. Fixtures are the committed byte-stable files in
tests/fixtures/raster/ (regenerate with
tests/fixtures/generate_raster_fixtures.py).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.geometry import ParseResult
from ingestion.raster import (
    MAX_IMAGE_PIXELS,
    RasterParseError,
    decode_image,
    parse_raster,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "raster"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse(name: str) -> ParseResult:
    return parse_raster(load(name))


@pytest.mark.unit
class TestSheetContract:
    def test_one_image_is_one_sheet(self) -> None:
        result = parse("plan.png")
        assert len(result.sheets) == 1
        sheet = result.sheets[0]
        assert sheet.sheet_ref == "image:1"
        assert sheet.layout_name == "Image 1"
        assert sheet.is_modelspace is False  # raster is never modelspace
        assert sheet.entity_count == 1  # the image itself
        assert sheet.title is None  # never guessed — no OCR in V1
        assert sheet.unit_code is None  # never guessed — pixels have no units

    def test_no_measurement_ever(self) -> None:
        # The absolute T033 contract: pixels are never auto-measured.
        result = parse("plan.png")
        assert result.geometries == ()
        assert result.text_tokens == ()
        assert result.sheets[0].measurable is False
        assert result.sheets[0].measurable_count == 0

    def test_units_always_unknown(self) -> None:
        result = parse("plan.png")
        assert result.drawing_units == "unknown"

    def test_no_scale_proposal_exists_for_raster(self) -> None:
        # Unlike PDF (propose_scale_from_text), raster has NO proposal API:
        # units are unknowable from pixels — the module surface proves it.
        import ingestion.raster as raster_pkg

        proposal_names = [n for n in dir(raster_pkg) if "scale" in n.lower()]
        assert proposal_names == [], (
            f"raster must expose no scale proposal API, found {proposal_names}"
        )

    def test_honest_notice_travels_with_every_parse(self) -> None:
        result = parse("blank.png")
        assert any(
            "AI-vision/manual takeoff only" in w and "no deterministic geometry" in w
            for w in result.warnings
        )
        assert any("scale proposal" in w or "calibration" in w for w in result.warnings)

    def test_blank_image_parses_honestly(self) -> None:
        # An all-white image is a real, parseable sheet with no content —
        # honest absence, never a crash, never a guess.
        result = parse("blank.png")
        assert len(result.sheets) == 1
        assert result.sheets[0].measurable_count == 0
        assert "mode RGB" in result.warnings[0]

    def test_multiframe_image_is_still_one_sheet(self) -> None:
        result = parse("animated.gif")
        assert len(result.sheets) == 1
        assert result.sheets[0].sheet_ref == "image:1"
        assert any("2 frames" in w for w in result.warnings)

    def test_source_sha256_anchors_the_bytes(self) -> None:
        data = load("plan.png")
        result = parse_raster(data)
        assert result.source_sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.unit
class TestRefusals:
    def test_empty_input_refused(self) -> None:
        with pytest.raises(RasterParseError, match="empty input"):
            parse_raster(b"")

    def test_garbage_bytes_refused(self) -> None:
        with pytest.raises(RasterParseError, match=r"unidentifiable image|could not open"):
            parse_raster(b"not an image at all, just text")

    def test_truncated_pixel_data_refused(self) -> None:
        # Valid signature, cut-off IDAT: the integrity decode must refuse.
        with pytest.raises(RasterParseError, match="truncated or corrupt"):
            parse_raster(load("truncated.png"))

    def test_oversized_header_refused_without_materializing(self) -> None:
        # 10000x6000 = 60 MP declared in a 45-byte header-only PNG: the
        # guard fires on the header pre-read — the fixture file itself is
        # tiny, proving no pixels were ever decoded.
        data = load("oversized.png")
        assert len(data) < 100, "oversized fixture must stay a header stub"
        with pytest.raises(RasterParseError) as excinfo:
            parse_raster(data)
        assert "50,000,000" in str(excinfo.value) or "decompression guard" in str(excinfo.value)

    def test_guard_threshold_is_50mp(self) -> None:
        assert MAX_IMAGE_PIXELS == 50_000_000

    def test_boundary_width_or_height_zero_refused(self) -> None:
        # Pillow refuses most zero-dimension headers at identification;
        # a crafted IHDR with width 0 must still never produce a sheet.
        import struct
        import zlib

        def chunk(tag: bytes, body: bytes) -> bytes:
            return (struct.pack(">I", len(body)) + tag + body
                    + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

        zero = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 8, 8, 2, 0, 0, 0))
            + chunk(b"IEND", b"")
        )
        with pytest.raises(RasterParseError):
            parse_raster(zero)

    def test_decode_image_shares_the_gate(self) -> None:
        # candidates.py reuses this gate; it must refuse identically.
        with pytest.raises(RasterParseError, match="empty input"):
            decode_image(b"")


@pytest.mark.unit
class TestDeterminism:
    def test_same_bytes_same_result(self) -> None:
        first = parse("border_rect.png")
        second = parse("border_rect.png")
        assert first == second  # frozen dataclasses: full-equality deterministic

    def test_result_is_pure_function_of_bytes(self) -> None:
        data = load("plan.png")
        assert parse_raster(data) == parse_raster(bytes(data))  # copy, same result


@pytest.mark.unit
class TestPurity:
    def test_parser_uses_no_measurement_packages(self) -> None:
        # Parsing is Pillow-only: cv2 must NOT be imported by the parser
        # module (OpenCV belongs to candidates), and no engine is reached.
        import ingestion.raster as raster_pkg

        source = (Path(raster_pkg.__file__)).read_text(encoding="utf-8")
        assert "import cv2" not in source
        assert "numpy" not in source
