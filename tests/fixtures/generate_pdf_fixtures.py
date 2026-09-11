"""Fixture generator for Round-5 PDF tests — hand-authored minimal PDFs.

Run:  .venv/bin/python tests/fixtures/generate_pdf_fixtures.py
Writes into tests/fixtures/pdf/ (the generated .pdf files ARE committed so CI
never depends on a PDF library to reproduce test inputs).

Why hand-author instead of a PDF library: reportlab is not installed (and
reportlab would pull exports/T102 territory). A minimal valid single-page PDF
is ~30 lines of ASCII; building the bytes ourselves (a) pins the exact path
operators so parser tests assert against known geometry, (b) keeps fixtures
byte-deterministic (no producer timestamps, no random /ID), (c) keeps them
human-readable in review. xref offsets are computed programmatically and the
generator self-checks every fixture by parsing it with pdfplumber and
asserting the geometry came through.

pdfplumber is used ONLY for the self-check assert (pure stdlib builds).
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pdfplumber

OUT = Path(__file__).parent / "pdf"

# Every fixture uses the same letter-size page (612x792 pt). Content-stream
# coordinates below are PDF user-space (bottom-left origin); pdfplumber
# converts them to top-left origin (top = 792 - y) — the parser and tests
# assert pdfplumber-native coordinates, never re-converting.


def _assemble(objects: list[bytes]) -> bytes:
    """1-indexed objects + computed xref offsets -> complete PDF bytes."""
    out = bytearray(b"%PDF-1.4\n%boq-v2 hand-authored Round-5 test fixture\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii")
        out += body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def _stream(content: str) -> bytes:
    body = content.encode("ascii")
    length = str(len(body)).encode("ascii")
    return b"<< /Length " + length + b" >>\nstream\n" + body + b"\nendstream"


def _page(contents_ref: str, resources: str, parent: int = 2) -> bytes:
    return (
        f"<< /Type /Page /Parent {parent} 0 R /MediaBox [0 0 612 792] "
        f"/Contents {contents_ref} /Resources {resources} >>"
    ).encode("ascii")


def _font() -> bytes:
    return b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


# ---------------------------------------------------------------------------
# Fixtures (T032/T034 inputs)
# ---------------------------------------------------------------------------

_VECTOR_RECTS_CONTENT = """1 0 0 RG
72 640 240 120 re S
360 640 180 90 re S
72 480 120 60 re S
300 300 m 480 300 l 480 420 l S
BT /F1 12 Tf 80 730 Td (ROOM A) Tj ET
BT /F1 12 Tf 80 60 Td (SCALE 1:100) Tj ET
"""


def build_vector_rects() -> bytes:
    """3 stroked rects + one multi-segment straight path + scale text.

    Known geometry (PDF user space): rect A (72,640) 240x120, rect B
    (360,640) 180x90, rect C (72,480) 120x60, polyline (300,300)-(480,300)-
    (480,420). Text tokens: ROOM / A / SCALE / 1:100 (the scale token feeds
    the T034 proposal test).
    """
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page("4 0 R", "<< /Font << /F1 5 0 R >> >>"),
        _stream(_VECTOR_RECTS_CONTENT),
        _font(),
    ])


def build_curves() -> bytes:
    """One rect + one bezier (c operator) path.

    The parser must accept the rect and refuse ONLY the curved path with a
    warning — never flatten the bezier into chords.
    """
    content = "72 640 240 120 re S\n100 100 m 200 100 200 200 100 200 c S\n"
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page("4 0 R", "<< /Font << /F1 5 0 R >> >>"),
        _stream(content),
        _font(),
    ])


def build_raster_only() -> bytes:
    """A page whose only content is one drawn image — no vector path ops.

    The honest "scanned sheet" simulation: an image Do, zero path operators.
    The parser must surface the sheet with measurable_count=0 plus a
    scanned?-warning instead of crashing or guessing.
    """
    content = "q 300 0 0 200 100 300 cm /Im0 Do Q\n"
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 "
        b"/Filter /ASCIIHexDecode /Length 3 >>\nstream\n80>\nendstream"
    )
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page("4 0 R", "<< /XObject << /Im0 5 0 R >> >>"),
        _stream(content),
        image,
    ])


def build_twopage() -> bytes:
    """Two pages, one distinct rect each -> two sheets page:0 / page:1."""
    page0 = "72 640 240 120 re S\n"
    page1 = "100 100 180 90 re S\n"
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        _page("4 0 R", "<< /Font << /F1 7 0 R >> >>"),
        _stream(page0),
        _page("6 0 R", "<< /Font << /F1 7 0 R >> >>"),
        _stream(page1),
        _font(),
    ])


_SCALE_ANNOTATION_CONTENT = """1 0 0 RG
72 640 240 120 re S
300 300 m 480 300 l 480 420 l S
BT /F1 12 Tf 80 730 Td (SCALE 1:100) Tj ET
"""


def build_scale_annotation() -> bytes:
    """Round 8 fixture: one closed rect + one open polyline + a 1:100 note.

    Known geometry (PDF user space): rect (72,640) 240x120 — a closed POLYGON
    ring; polyline (300,300)-(480,300)-(480,420) — an open two-segment path
    (300pt total). The single text run "SCALE 1:100" sits top-left, so
    pdfplumber's WordExtractor yields ONE token matching the 1:N regex (the
    whole annotation is one drawn word — no grouping ambiguity). This is the
    slice-1/slice-2 end-to-end input: parse -> PROPOSED BAR_SCALE_DETECTED
    calibration at 1:100 (100*25.4/72 mm/pt) -> confirmed run -> one area
    candidate + one length candidate in NEEDS_REVIEW.
    """
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page("4 0 R", "<< /Font << /F1 5 0 R >> >>"),
        _stream(_SCALE_ANNOTATION_CONTENT),
        _font(),
    ])


def build_corrupt() -> bytes:
    """Truncated bytes of the good fixture — parse must fail loudly."""
    good = build_vector_rects()
    return good[: len(good) // 2]


FIXTURES: dict[str, Any] = {
    "vector_rects.pdf": build_vector_rects,
    "curves.pdf": build_curves,
    "raster_only.pdf": build_raster_only,
    "twopage.pdf": build_twopage,
    "scale_annotation.pdf": build_scale_annotation,
    "corrupt.pdf": build_corrupt,
}


# ---------------------------------------------------------------------------
# Self-check: roundtrip through pdfplumber (the CI-pinned parser)
# ---------------------------------------------------------------------------


def _open(data: bytes) -> Any:
    return pdfplumber.open(io.BytesIO(data))


def _check_all() -> None:
    with _open(build_vector_rects()) as pdf:
        page = pdf.pages[0]
        assert len(page.rects) == 3, f"expected 3 rects, got {len(page.rects)}"
        assert len(page.curves) == 1, "multi-segment path must be a curve object"
        words = [w["text"] for w in page.extract_words()]
        assert "1:100" in words and "ROOM" in words, f"text lost: {words}"
    with _open(build_curves()) as pdf:
        page = pdf.pages[0]
        assert len(page.rects) == 1 and len(page.curves) == 1
        ops = [op[0] for op in page.curves[0]["path"]]
        assert "c" in ops, f"bezier must reach pdfplumber as a curve op: {ops}"
    with _open(build_raster_only()) as pdf:
        page = pdf.pages[0]
        vector = len(page.rects) + len(page.lines) + len(page.curves)
        assert vector == 0, "raster fixture must have no vector content"
        assert len(page.images) == 1, "raster fixture must carry one image"
    with _open(build_twopage()) as pdf:
        assert len(pdf.pages) == 2
        assert len(pdf.pages[0].rects) == 1 and len(pdf.pages[1].rects) == 1
        assert pdf.pages[0].rects[0]["x0"] != pdf.pages[1].rects[0]["x0"], (
            "the two pages must hold distinct rects"
        )
    with _open(build_scale_annotation()) as pdf:
        page = pdf.pages[0]
        assert len(page.rects) == 1, "one closed rect (re op)"
        assert len(page.curves) == 1, "the open multi-segment path is one curve object"
        words = [w["text"] for w in page.extract_words()]
        assert words == ["SCALE", "1:100"], (
            "the annotation must split into exactly the tokens the 1:N regex "
            f"consumes (SCALE does not match; 1:100 does): {words}"
        )
    try:
        with _open(build_corrupt()) as pdf:  # type: ignore[union-attr]
            if len(pdf.pages) == 0:
                raise ValueError("no pages in truncated file")
    except Exception:
        pass  # refusal is the expected outcome (any exception shape)
    else:
        raise AssertionError("truncated fixture must not parse")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _check_all()
    for name, build in FIXTURES.items():
        path = OUT / name
        data = build()
        assert build() == data, f"{name} must be byte-deterministic"
        path.write_bytes(data)
        print(f"wrote {path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
