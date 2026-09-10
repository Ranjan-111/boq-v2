"""T032/T034 — PDF parser tests: stable path refs, honest units, refusals.

The provenance contract under test (the PDF equivalent of the DXF suite):
  * every normalized geometry carries a path-index handle ("p0:rect:0"),
    stable across re-parses of the same bytes,
  * a rect becomes an exact closed 4-corner POLYGON ring; a multi-segment
    straight path a POLYLINE — pdfplumber-native (x, top) coordinates,
    never converted,
  * drawing_units is ALWAYS "unknown" (PDF has no INSUNITS; T034 never
    guesses) and unit_code is None on every sheet,
  * bezier paths refuse whole-path with a warning; corrupt/encrypted/empty
    files raise PdfParseError; raster-only pages surface with the scanned
    warning instead of crashing,
  * text tokens carry text/insertion/height/handle for the scale-proposal
    regex layer.

Unit-marked: pure parsing, no DB. pdfplumber is a real dependency, never
mocked. Fixtures are the committed hand-authored PDFs (regenerate with
tests/fixtures/generate_pdf_fixtures.py).
"""
from __future__ import annotations

import hashlib
import io
from decimal import Decimal
from pathlib import Path

import pytest

from core.domain.enums import GeomType
from core.geometry import ParseResult
from ingestion.pdf import (
    PdfParseError,
    parse_pdf,
    propose_scale_from_text,
    sheets_of,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"

# Letter page 612x792 pt, so pdfplumber top = 792 - pdf_y. Fixture geometry is
# asserted in pdfplumber-native (x, top) — the frame the parser captures.
PAGE_HEIGHT = 792.0


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse(name: str) -> ParseResult:
    return parse_pdf(load(name))


@pytest.mark.unit
class TestSheets:
    def test_one_sheet_per_page_in_order(self) -> None:
        result = parse("twopage.pdf")
        assert [s.sheet_ref for s in result.sheets] == ["page:0", "page:1"]
        assert [s.layout_name for s in result.sheets] == ["Page 1", "Page 2"]
        assert all(s.is_modelspace for s in result.sheets)

    def test_sheet_counts_and_measurable_flags(self) -> None:
        sheet = parse("vector_rects.pdf").sheets[0]
        # Entities on the page: 3 rects + 1 curve path + 4 words = 8;
        # measurable vector geometry: 3 rects + 1 straight path = 4.
        assert sheet.entity_count == 8
        assert sheet.measurable_count == 4
        assert sheet.measurable is True

    def test_raster_only_page_is_a_sheet_with_zero_measurable(self) -> None:
        result = parse("raster_only.pdf")
        sheet = result.sheets[0]
        assert sheet.sheet_ref == "page:0"
        assert sheet.measurable_count == 0
        assert sheet.measurable is False
        assert any("no vector content (scanned?)" in w for w in result.warnings)

    def test_title_and_unit_code_never_guessed(self) -> None:
        for name in ("vector_rects.pdf", "twopage.pdf", "raster_only.pdf"):
            for sheet in parse(name).sheets:
                assert sheet.title is None
                assert sheet.unit_code is None, "PDF has no INSUNITS — never guessed"


@pytest.mark.unit
class TestGeometry:
    def test_rect_is_exact_closed_four_corner_ring(self) -> None:
        # Fixture rect A: `72 640 240 120 re` spans PDF y 640..760.
        geom = parse("vector_rects.pdf").geometries[0]
        assert list(geom.coordinates) == [
            (72.0, PAGE_HEIGHT - 760.0), (312.0, PAGE_HEIGHT - 760.0),
            (312.0, PAGE_HEIGHT - 640.0), (72.0, PAGE_HEIGHT - 640.0),
            (72.0, PAGE_HEIGHT - 760.0),  # explicit closing point
        ]
        assert geom.geom_type is GeomType.POLYGON

    def test_rects_are_three_distinct_rings_with_known_areas(self) -> None:
        geoms = [g for g in parse("vector_rects.pdf").geometries
                 if g.geom_type is GeomType.POLYGON]
        assert len(geoms) == 3
        areas = sorted(
            (g.coordinates[1][0] - g.coordinates[0][0])
            * (g.coordinates[2][1] - g.coordinates[0][1])
            for g in geoms
        )
        assert areas == [120.0 * 60.0, 180.0 * 90.0, 240.0 * 120.0]

    def test_straight_multisegment_path_is_polyline(self) -> None:
        # Fixture polyline: (300,300)-(480,300)-(480,420) in PDF space.
        polys = [g for g in parse("vector_rects.pdf").geometries
                 if g.geom_type is GeomType.POLYLINE]
        assert len(polys) == 1
        assert [tuple(p) for p in polys[0].coordinates] == [
            (300.0, PAGE_HEIGHT - 300.0), (480.0, PAGE_HEIGHT - 300.0),
            (480.0, PAGE_HEIGHT - 420.0),
        ]

    def test_every_geometry_has_stable_pdf_handle(self) -> None:
        result = parse("vector_rects.pdf")
        refs = [h.entity_ref for g in result.geometries for h in g.source_handles]
        assert refs == ["p0:rect:0", "p0:rect:1", "p0:rect:2", "p0:curve:0"]
        for geom in result.geometries:
            handle = geom.source_handles[0]
            assert handle.format.value == "pdf_vector"
            assert handle.sheet_ref == "page:0"
            assert handle.layer is None

    def test_handles_stable_across_reparse(self) -> None:
        r1, r2 = parse("vector_rects.pdf"), parse("vector_rects.pdf")
        h1 = [h.entity_ref for g in r1.geometries for h in g.source_handles]
        h2 = [h.entity_ref for g in r2.geometries for h in g.source_handles]
        assert h1 == h2

    def test_two_pages_keep_page_scoped_handles(self) -> None:
        refs = [h.entity_ref for g in parse("twopage.pdf").geometries
                for h in g.source_handles]
        assert refs == ["p0:rect:0", "p1:rect:0"]


@pytest.mark.unit
class TestTextTokens:
    def test_tokens_carry_text_position_height_handle(self) -> None:
        tokens = parse("vector_rects.pdf").text_tokens
        assert [t.text for t in tokens] == ["ROOM", "A", "SCALE", "1:100"]
        token = tokens[0]
        assert token.insertion[0] == pytest.approx(80.0)
        # Baseline at PDF y=730; pdfplumber top shifts by the font ascent
        # (metric-derived — approx, not exact, across library versions).
        assert token.insertion[1] == pytest.approx(PAGE_HEIGHT - 730.0, abs=12.0)
        assert token.height == pytest.approx(12.0)  # Tf size, bottom-top
        assert token.handle.entity_ref == "p0:text:0"
        assert token.handle.sheet_ref == "page:0"

    def test_scale_token_is_captured_for_proposals(self) -> None:
        texts = [t.text for t in parse("vector_rects.pdf").text_tokens]
        assert "1:100" in texts, "fixture must feed the T034 proposal test"


@pytest.mark.unit
class TestRefusals:
    def test_curved_path_skipped_with_warning(self) -> None:
        result = parse("curves.pdf")
        assert len(result.geometries) == 1, "only the rect may survive"
        assert "unsupported path p0:curve:0: curved segment" in result.warnings
        assert all(g.source_handles[0].entity_ref == "p0:rect:0"
                   for g in result.geometries)

    def test_corrupt_pdf_raises(self) -> None:
        with pytest.raises(PdfParseError):
            parse("corrupt.pdf")

    def test_empty_bytes_raise(self) -> None:
        with pytest.raises(PdfParseError):
            parse_pdf(b"")

    def test_garbage_bytes_raise(self) -> None:
        with pytest.raises(PdfParseError):
            parse_pdf(b"not a pdf at all")

    def test_encrypted_pdf_raises(self) -> None:
        """A password-locked PDF refuses loudly.

        Built in-test with pypdf (user password "secret"): the empty
        password cannot decrypt it, so parse_pdf must raise rather than
        guess at unreadable content. Not a committed fixture — an encrypted
        blob is binary, which would defeat the ASCII-fixture doctrine; the
        assertion under test is the refusal, not the bytes.
        """
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(load("vector_rects.pdf"))))
        buf = io.BytesIO()
        writer.encrypt(user_password="secret")
        writer.write(buf)
        with pytest.raises(PdfParseError, match="encrypted"):
            parse_pdf(buf.getvalue())

    def test_zero_page_pdf_raises(self) -> None:
        """A PDF with no pages cannot yield a sheet — refuse, don't guess."""
        data = load("vector_rects.pdf")
        _head, sep, _tail = data.partition(b"/Count 1 >>")
        zero_page = (b"%PDF-1.4\n"
                     b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                     b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
                     b"trailer\n<< /Size 3 /Root 1 0 R >>\n")
        assert sep and zero_page != data  # sanity: the fixture had pages
        with pytest.raises(PdfParseError):
            parse_pdf(zero_page)

    def test_whitespace_only_content_stream_is_a_scanned_page(self) -> None:
        """A page with NO ops at all (not even an image Do) still surfaces:
        zero measurable + the scanned warning — pdfplumber handles a
        whitespace-only stream fine (probed against the pinned version),
        so the honest outcome is the warning, not a crash. This is the
        degenerate corner under the raster_only fixture (which HAS an image)."""
        from tests.fixtures.generate_pdf_fixtures import _assemble, _page, _stream

        data = _assemble([
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            _page("4 0 R", "<< >>"),
            _stream("   \n  "),
        ])
        result = parse_pdf(data)
        assert result.geometries == ()
        assert result.text_tokens == ()
        assert result.sheets[0].measurable_count == 0
        assert "page 0 has no vector content (scanned?)" in result.warnings


@pytest.mark.unit
class TestDeterminismAndUnits:
    def test_two_parses_are_equal(self) -> None:
        assert parse("vector_rects.pdf") == parse("vector_rects.pdf")
        assert parse("twopage.pdf") == parse("twopage.pdf")

    def test_units_always_unknown(self) -> None:
        for name in ("vector_rects.pdf", "curves.pdf", "raster_only.pdf",
                     "twopage.pdf"):
            assert parse(name).drawing_units == "unknown"

    def test_source_digest_binds_original_bytes(self) -> None:
        data = load("vector_rects.pdf")
        result = parse_pdf(data)
        assert result.source_sha256 == hashlib.sha256(data).hexdigest()
        assert parse_pdf(data).source_sha256 == result.source_sha256

    def test_sheets_of_convenience(self) -> None:
        assert [s.sheet_ref for s in sheets_of(load("twopage.pdf"))] == [
            "page:0", "page:1",
        ]


@pytest.mark.unit
class TestScaleProposal:
    """T034: regex over text, PROPOSED-only, exact Decimal math.

    For a 1:N annotation, one drawing unit (1 PDF point = 1/72 inch paper)
    depicts N*25.4/72 mm of the real-world object; the factor is the
    PROPOSED units_per_drawing_unit, quantized to 10 decimal places.
    """

    @pytest.mark.parametrize(("text", "denominator"), [
        ("1:100", Decimal(100)),
        ("1:50", Decimal(50)),
        ("SCALE 1:250", Decimal(250)),
        ("scale drawing 1:1000 (rev A)", Decimal(1000)),
    ])
    def test_ratio_proposes_exact_mm_factor(
        self, text: str, denominator: Decimal,
    ) -> None:
        factor_str, method = propose_scale_from_text([text])
        expected = denominator * Decimal("25.4") / Decimal(72)
        assert factor_str == str(expected.quantize(Decimal(1).scaleb(-10)))
        assert method == "bar_scale_detected"

    def test_known_denominators_round_to_ten_places(self) -> None:
        assert propose_scale_from_text(["1:100"])[0] == "35.2777777778"
        assert propose_scale_from_text(["1:50"])[0] == "17.6388888889"
        assert propose_scale_from_text(["SCALE 1:250"])[0] == "88.1944444444"

    def test_first_match_wins_in_order(self) -> None:
        assert propose_scale_from_text(["1:100", "1:50"])[0] == "35.2777777778"

    def test_no_scale_text_proposes_nothing(self) -> None:
        assert propose_scale_from_text(["no scale text"]) == (None, None)
        assert propose_scale_from_text(["ROOM", "KITCHEN"]) == (None, None)
        assert propose_scale_from_text([]) == (None, None)

    def test_spacing_around_colon_is_tolerated(self) -> None:
        assert propose_scale_from_text(["1 : 100"])[0] == "35.2777777778"

    def test_proposal_never_changes_parse_units(self) -> None:
        """The proposal is a candidate; the parser stays unit-agnostic."""
        result = parse("vector_rects.pdf")
        propose_scale_from_text([t.text for t in result.text_tokens])
        assert result.drawing_units == "unknown"
