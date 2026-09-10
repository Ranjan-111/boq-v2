"""PDF parser (T032/T034) — pdfplumber → normalized geometry + stable path refs.

The provenance anchor (mirrors the DXF parser's dxf.handle stance): every
normalized geometry carries the index of its path in the page's content
stream, so any quantity traces back to the exact source drawing op.
pdfplumber iterates decoded objects in content-stream order, which makes
that index stable across re-parses of the same bytes.

Behavioral stance (pattern-inspired by OCErp's honest-unit handling, code
written fresh against docs/domain-model.md):
  * a PDF has NO INSUNITS equivalent — drawing_units is always "unknown"
    and scale must come from a PROPOSED text-token regex (below) or human
    confirmation; it is NEVER assumed (T034),
  * a PDF page IS the drawing sheet (is_modelspace=True) — there is no
    modelspace/paperspace split to refuse on,
  * only honestly straight vector paths are normalized: `re` rects (exact
    4-corner ring from their bounding box — rects are axis-aligned by the
    time pdfplumber classifies them) and all-straight `m`/`l` paths; any
    bezier (`c`/`v`/`y`) segment refuses the WHOLE path with a warning —
    curves are never flattened into chords,
  * degenerate shapes are skipped WITH a warning, never silently dropped
    (open polyline with <2 distinct vertices; closed ring with <3 distinct),
  * raster-only pages (images, no vector) still surface as sheets with
    measurable_count=0 plus a scanned?-warning — honest, not a crash,
  * encrypted PDFs that an empty password cannot open raise; owner-locked
    files that do open are parsed (they are readable, so refusing would
    be the dishonest option),
  * coordinates stay in PDF points and in pdfplumber's NATIVE top-left
    frame (x, top) — captured honestly, never converted. Mixing PDF and
    DXF coordinates in one geometry is a non-goal: PDF is a separate
    format path, and downstream consumers (candidates, scale proposals)
    work in PDF points.

Text tokens are grouped words from pdfplumber's default WordExtractor
(documented grouping: x/y tolerance 3, upright chars, ligatures expanded;
height = bottom-top) captured with insertion=(x0, top). Truncated/odd text
is kept as-is — tokens are evidence for later scale-proposal regexes,
never geometry.

PyMuPDF is banned project-wide; this module uses pdfplumber (MIT) for
parsing and pypdf (BSD) only for the encrypted-file sniff.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
from decimal import Decimal
from typing import Any

import pdfplumber
from pypdf import PasswordType, PdfReader

from core.domain.enums import GeomType, SourceFormat
from core.geometry import (
    NormalizedGeometry,
    ParseResult,
    SheetSummary,
    SourceHandleRef,
    TextToken,
)

# entity_ref scheme: "p{page}:{kind}:{index}" with kind ∈ rect|line|curve|text.
# pdfplumber keeps rect/line/curve objects in SEPARATE lists, each in
# content-stream order — the kind tag keeps refs collision-free across the
# independent per-type indices. This is the PDF equivalent of the DXF handle.
_CURVE_OPS = frozenset({"c", "v", "y"})

# 1 drawing unit = 1 PDF point = 1/72 inch on paper (a constant of the
# format, not a scale assumption). At an annotated ratio 1:N, one point of
# paper depicts N/72 inch ≈ N*25.4/72 mm of the drawn real-world object.
# Used ONLY to shape a PROPOSED calibration candidate — never auto-applied.
_POINTS_PER_INCH = Decimal(72)
_MM_PER_INCH = Decimal("25.4")
_SCALE_RE = re.compile(r"1\s*:\s*(\d+)")
_SCALE_DECIMALS = 10


class PdfParseError(ValueError):
    """Structural PDF problems that make parsing impossible."""


def _ref(page_no: int, kind: str, index: int) -> SourceHandleRef:
    return SourceHandleRef(
        format=SourceFormat.PDF_VECTOR,
        sheet_ref=f"page:{page_no}",
        entity_ref=f"p{page_no}:{kind}:{index}",
        layer=None,
    )


def _num(value: Any) -> float:
    out = float(value)  # pdfplumber resolves coordinates to float/int
    if not math.isfinite(out):
        raise PdfParseError(f"non-finite coordinate: {value!r}")
    return out


def _finite_pairs(points: Any) -> list[tuple[float, float]] | None:
    """(x, top) pairs from a pdfplumber pts list; None if any is unusable."""
    out: list[tuple[float, float]] = []
    for pt in points:
        try:
            out.append((_num(pt[0]), _num(pt[1])))
        except PdfParseError:
            return None
    return out


def _ops_of(obj: dict[str, Any]) -> list[Any]:
    """The decoded path ops ('m'/'l'/'c'/'v'/'y'/'h') of a vector object."""
    return list(obj.get("path", []))


def _is_straight(ops: list[Any]) -> bool:
    """True when no segment op is a bezier (c/v/y) — never flatten curves."""
    return all(op[0] not in _CURVE_OPS for op in ops)


def _closed_by_h(ops: list[Any]) -> bool:
    return any(op[0] == "h" for op in ops)


def _geometry(
    pairs: list[tuple[float, float]], ref: SourceHandleRef, closed: bool,
) -> NormalizedGeometry:
    return NormalizedGeometry(
        geom_type=GeomType.POLYGON if closed else GeomType.POLYLINE,
        coordinates=pairs,
        source_format=SourceFormat.PDF_VECTOR,
        source_handles=(ref,),
        layer=None,
    )


def _normalize_path(
    obj: dict[str, Any], ref: SourceHandleRef,
) -> tuple[NormalizedGeometry | None, str | None]:
    """One pdfplumber line/curve object → geometry or (None, warning).

    pts carry only on-path points (bezier control points excluded), already
    in pdfplumber-native (x, top) coordinates. Closure: an `h` op, or a ring
    whose first point repeats as its last (an explicitly drawn closure).
    """
    pairs = _finite_pairs(obj.get("pts", []))
    if pairs is None:
        return None, f"unsupported {ref.entity_ref}: non-finite or empty coordinates"
    ops = _ops_of(obj)
    if not _is_straight(ops):
        return None, f"unsupported path {ref.entity_ref}: curved segment"
    closed = _closed_by_h(ops) or (len(pairs) >= 4 and pairs[0] == pairs[-1])
    distinct = len(set(pairs))
    if closed:
        # A ring needs >= 3 distinct vertices to bound an area — the same
        # degeneracy doctrine the DXF parser applies to closed polylines.
        if distinct < 3:
            return None, (
                f"skipped {ref.entity_ref}: "
                "closed ring with fewer than three distinct vertices"
            )
        if pairs[0] != pairs[-1]:
            pairs = [*pairs, pairs[0]]
    elif distinct < 2:
        return None, f"skipped {ref.entity_ref}: fewer than two distinct vertices"
    return _geometry(pairs, ref, closed), None


def _normalize_rect(
    obj: dict[str, Any], ref: SourceHandleRef,
) -> tuple[NormalizedGeometry | None, str | None]:
    """A pdfplumber rect → exact closed 4-corner ring (POLYGON).

    The ring is rebuilt from (x0, top, x1, bottom): rects are axis-aligned
    by classification time, so the bounding box IS the corner set — exact
    and deterministic, no re-derivation from path ops needed.
    """
    try:
        x0, top = _num(obj["x0"]), _num(obj["top"])
        x1, bottom = _num(obj["x1"]), _num(obj["bottom"])
    except (KeyError, PdfParseError):
        return None, f"skipped {ref.entity_ref}: non-finite or empty rect coordinates"
    corners = [(x0, top), (x1, top), (x1, bottom), (x0, bottom)]
    if len(set(corners)) < 3:
        return None, (
            f"skipped {ref.entity_ref}: degenerate rect (fewer than three distinct corners)"
        )
    return _geometry([*corners, corners[0]], ref, closed=True), None


def _is_encrypted(data: bytes) -> bool:
    """True when the file is encrypted AND an empty password cannot open it.

    Owner-password-only files (empty user password) decrypt with "" and are
    readable — refusing them would be dishonest. pypdf does the sniff
    because pdfplumber raises a generic PdfminerException with no encrypted
    marker to branch on.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        if not reader.is_encrypted:
            return False
        return reader.decrypt("") is PasswordType.NOT_DECRYPTED
    except Exception:
        # Unparsable input belongs to the parse-failure path, not here.
        return False


def parse_pdf(data: bytes) -> ParseResult:
    """Parse PDF bytes → ParseResult (geometries + sheets + text + warnings)."""
    if not data:
        raise PdfParseError("empty input is not a PDF")
    if _is_encrypted(data):
        raise PdfParseError("encrypted")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = list(pdf.pages)
    except Exception as exc:
        raise PdfParseError(f"pdfplumber could not parse: {exc}") from exc
    if not pages:
        raise PdfParseError("PDF has no pages")

    warnings: list[str] = []
    geometries: list[NormalizedGeometry] = []
    tokens: list[TextToken] = []
    sheets: list[SheetSummary] = []

    for page_no, page in enumerate(pages):
        measurable = 0
        for kind, objects in (
            ("rect", page.rects), ("line", page.lines), ("curve", page.curves),
        ):
            normalize = _normalize_rect if kind == "rect" else _normalize_path
            for index, obj in enumerate(objects):
                ref = _ref(page_no, kind, index)
                geom, warning = normalize(obj, ref)
                if geom is None:
                    warnings.append(warning or f"skipped {ref.entity_ref}")
                else:
                    geometries.append(geom)
                    measurable += 1
        words = list(page.extract_words())
        for index, word in enumerate(words):
            try:
                insertion = (_num(word["x0"]), _num(word["top"]))
                height = _num(word["height"]) if word.get("height") is not None else None
            except PdfParseError as exc:
                warnings.append(f"skipped text p{page_no}:text:{index}: {exc}")
                continue
            tokens.append(TextToken(
                text=str(word["text"]),
                insertion=insertion,
                height=height,
                handle=_ref(page_no, "text", index),
            ))
        has_vector = bool(page.rects or page.lines or page.curves)
        entity_count = (
            len(page.rects) + len(page.lines) + len(page.curves)
            + len(page.images) + len(words)
        )
        sheets.append(SheetSummary(
            sheet_ref=f"page:{page_no}",
            layout_name=f"Page {page_no + 1}",
            entity_count=entity_count,
            measurable_count=measurable,
            is_modelspace=True,  # a PDF page IS the drawing sheet
            title=None,
            unit_code=None,  # never guessed: PDF has no INSUNITS equivalent
            measurable=measurable > 0,
        ))
        if not has_vector:
            warnings.append(f"page {page_no} has no vector content (scanned?)")

    return ParseResult(
        source_sha256=hashlib.sha256(data).hexdigest(),
        drawing_units="unknown",  # T034: never assumed, scale is human-gated
        geometries=tuple(geometries),
        sheets=tuple(sheets),
        text_tokens=tuple(tokens),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Scale proposal (T034 PDF slice): from text tokens only, PROPOSED — never
# applied. The human gate confirms; this returns a candidate at most.
# ---------------------------------------------------------------------------


def propose_scale_from_text(texts: list[str]) -> tuple[str | None, str | None]:
    """PROPOSED calibration candidate from scale annotations: (factor, method).

    Scans the given strings (in order, first match wins) for the pattern
    ``1\\s*:\\s*(\\d+)`` — "1:100", "SCALE 1:50", "1:250". For a paper
    drawing annotated 1:N, one drawing unit (1 PDF point = 1/72 inch paper)
    depicts N/72 inch ≈ N*25.4/72 mm of the real-world object, so the
    proposal is units_per_drawing_unit_mm = N*25.4/72, computed exactly with
    Decimal, quantized to 10 decimal places, and stringified. No match →
    (None, None). PROPOSED-ONLY — never auto-applies (the human gate does).
    """
    for text in texts:
        match = _SCALE_RE.search(text)
        if match is None:
            continue
        denominator = Decimal(match.group(1))
        mm = denominator * _MM_PER_INCH / _POINTS_PER_INCH
        factor = mm.quantize(Decimal(1).scaleb(-_SCALE_DECIMALS))
        return str(factor), "bar_scale_detected"
    return None, None


def sheets_of(data: bytes) -> tuple[SheetSummary, ...]:
    """Convenience: parse and return just the sheet summaries."""
    return parse_pdf(data).sheets


__all__ = [
    "NormalizedGeometry",
    "ParseResult",
    "PdfParseError",
    "SheetSummary",
    "SourceHandleRef",
    "parse_pdf",
    "propose_scale_from_text",
    "sheets_of",
]
