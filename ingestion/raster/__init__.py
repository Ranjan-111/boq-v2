"""Raster parser (T033) — Pillow-only format/size/mode/integrity. NO measurement.

The T033 contract is absolute: pixels are evidence for AI vision and MANUAL
takeoff only. This parser NEVER extracts geometry, NEVER proposes a scale,
and NEVER pretends a pixel has a unit:

  * geometries is ALWAYS () and text_tokens is ALWAYS () — no
    auto-measurement of any kind from pixels (T033 "NO auto-measurement").
    Detecting wall lines / room regions in pixel space is a separate,
    explicitly advisory module (`ingestion.raster.candidates`, T048); it
    proposes for review, it never measures.
  * drawing_units is ALWAYS "unknown" and unit_code is ALWAYS None: units
    are unknowable from pixels. No scale proposal exists in this package at
    all (unlike the PDF parser's text-derived PROPOSED candidate) — NULL
    calibration semantics is the human scale gate's business, and a raster
    sheet simply arrives uncalibrated.
  * one image = one sheet, sheet_ref "image:1" (the PDF parser's page:N and
    the DXF parser's layout refs have no raster equivalent; "image:1"
    names the first and only image, 1-indexed because a file has exactly
    one image — there is no image 0 concept to align with). is_modelspace
    is False: raster is never modelspace (that is a CAD layout concept).
  * measurable mirrors the honest semantics the PDF parser applies to
    scanned pages: a sheet with zero deterministic geometry is surfaced
    with measurable=False, measurable_count=0 — plus a warning, never a
    crash and never a guess.
  * every parse carries the warning "raster sheet: AI-vision/manual
    takeoff only, no deterministic geometry" — the honest notice travels
    with the data, not in a README.

Refusals, all honest and all raising `RasterParseError` (mirrors
PdfParseError/DxfParseError):

  * empty input ("empty input is not an image"),
  * bytes Pillow cannot identify as an image (corrupt/truncated header,
    unsupported format — UnidentifiedImageError/OSError from open),
  * image data that fails to decode (truncated stream mid-pixels —
    refused by Pillow with ImageFile.LOAD_TRUNCATED_IMAGES at its default
    False; this module never flips that global),
  * zero-pixel images (width or height 0 — Pillow refuses most such
    headers at identification; a post-open belt check refuses any that
    slip through),
  * the decompression guard: images whose width*height exceeds
    `MAX_IMAGE_PIXELS` (50,000,000). Pillow's lazy open reads the header
    (and therefore the size) WITHOUT decoding pixel data, so the guard
    fires BEFORE any decode — a header-only PNG claiming 60 megapixels is
    refused in microseconds without materializing a single row of pixels.
    A "drawing" bigger than 50 MP is adversarial, not architectural.
    Pillow's own decompression-bomb error (DecompressionBombError, raised
    at decode time around 89.5 MP) is caught too, as the load-phase
    backstop for headers that lie.

This module uses ONLY Pillow (the cv2 machinery lives in
ingestion/raster/candidates.py — parsing is format/size/mode/integrity,
OpenCV belongs to candidate detection).
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image, UnidentifiedImageError

from core.geometry import ParseResult, SheetSummary

# The decompression/EXIF-bomb guard: width*height at or under this is
# decodable; above it the image is refused before decode. 50 MP covers any
# honest scanned A0 at ~300 DPI with margin to spare.
MAX_IMAGE_PIXELS = 50_000_000

# The honest notice every raster parse carries (see module docstring).
_RASTER_NOTICE = "raster sheet: AI-vision/manual takeoff only, no deterministic geometry"

_SHEET_REF = "image:1"


class RasterParseError(ValueError):
    """Structural raster problems that make parsing impossible."""


def decode_image(data: bytes) -> Image.Image:
    """The shared refusal gate + full decode used by parser and candidates.

    Enforces, in order: non-empty input, Pillow identification (open),
    zero-pixel refusal, the 50 MP decompression guard (header pre-read,
    BEFORE any decode), and finally the integrity decode (load) which
    catches truncated/corrupt pixel data and Pillow's own bomb error.
    Returns a fully decoded image; raises RasterParseError on any refusal.
    """
    if not data:
        raise RasterParseError("empty input is not an image")
    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as exc:
        raise RasterParseError(
            f"unidentifiable image (corrupt header or unsupported format): {exc}"
        ) from exc
    except Exception as exc:  # Pillow raises more than OSError for hostile bytes
        raise RasterParseError(f"Pillow could not open the image: {exc}") from exc
    width, height = image.size
    # Header pre-read (Image.open is lazy — no pixels decoded yet): the
    # guards below fire before load(), so an oversized claim is refused
    # without materializing its pixels.
    if width <= 0 or height <= 0:
        raise RasterParseError(f"image has zero pixels: {width}x{height}")
    if width * height > MAX_IMAGE_PIXELS:
        raise RasterParseError(
            f"image is {width}x{height} = {width * height} pixels, over the "
            f"{MAX_IMAGE_PIXELS}-pixel decompression guard (refused before decode)"
        )
    try:
        image.load()  # integrity check: full decode of the one frame we keep
    except Exception as exc:
        raise RasterParseError(f"image data is truncated or corrupt: {exc}") from exc
    return image


def parse_raster(data: bytes) -> ParseResult:
    """Parse image bytes → ParseResult (one sheet, zero geometry, honest warnings).

    Parsing is format/size/mode/integrity ONLY. Deterministic: a pure
    function of the input bytes (sha256-anchored), no clock, no randomness.
    """
    image = decode_image(data)
    width, height = image.size
    fmt = image.format or "unknown"
    mode = image.mode
    frames = getattr(image, "n_frames", 1)

    warnings = [
        f"{_RASTER_NOTICE} ({fmt} {width}x{height} px, mode {mode})",
        "raster units are unknowable from pixels: no scale proposal exists, "
        "calibration stays the human gate",
    ]
    if frames > 1:
        warnings.append(
            f"animated raster with {frames} frames: one sheet per image, "
            "frame 1 is the sheet content"
        )

    sheet = SheetSummary(
        sheet_ref=_SHEET_REF,
        layout_name="Image 1",
        entity_count=1,  # the image itself is the sheet's single content item
        measurable_count=0,  # NO auto-measurement from pixels (T033)
        is_modelspace=False,  # raster is never modelspace (a CAD layout concept)
        title=None,  # never guessed: no OCR in V1, an AI vision pass may propose
        unit_code=None,  # never guessed: pixels carry no units
        measurable=False,  # zero deterministic geometry — the scanned-page stance
    )

    return ParseResult(
        source_sha256=hashlib.sha256(data).hexdigest(),
        drawing_units="unknown",  # T033: units unknowable from pixels, never assumed
        geometries=(),  # ALWAYS: pixels are not deterministic geometry
        sheets=(sheet,),
        text_tokens=(),  # ALWAYS: no OCR in V1; text in pixels is for AI vision
        warnings=tuple(warnings),
    )


__all__ = [
    "MAX_IMAGE_PIXELS",
    "ParseResult",
    "RasterParseError",
    "SheetSummary",
    "decode_image",
    "parse_raster",
]
