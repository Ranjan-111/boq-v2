"""Fixture generator for Round-6 raster tests — byte-deterministic PIL images.

Run:  .venv/bin/python tests/fixtures/generate_raster_fixtures.py
Writes into tests/fixtures/raster/ (the generated files ARE committed so CI
never needs to regenerate them; keep each under ~20 KB — the oversized-
refusal fixture is a header-only PNG of a few dozen bytes by design).

Byte-stability doctrine (mirrors generate_pdf_fixtures.py): every builder is
a pure function of nothing — no timestamps, no randomness, no ICC profile
noise — and the generator SELF-CHECKS two ways before writing:
  1. build() twice → byte-identical,
  2. sha256 of every on-disk fixture is compared before/after writing in
     main(); a regeneration that would change committed bytes aborts
     loudly (so a Pillow upgrade that changes PNG encoding fails the
     generator instead of silently rewriting history).

Fixtures (T033 parser / T048 candidates):
  plan.png          400x300 "floor plan": 6px black ring + 6px vertical
                    divider fully connected — cv2 finds 2 wall-ish sets
                    and 2 enclosed room regions.
  border_rect.png   320x240 single black-bordered rectangle (the "plan"
                    minimal case): 4 wall candidates, 1 enclosed room.
  blank.png         320x240 all-white image — parses to a sheet (honest
                    absence of content), zero candidates of either kind.
  truncated.png     valid PNG header + a cut-off IDAT — the integrity
                    decode must refuse loudly (RasterParseError).
  oversized.png     header-only PNG (IHDR+IEND, no IDAT) declaring
                    10000x6000 = 60 MP > the 50 MP guard: refused from
                    the header pre-read WITHOUT materializing pixels.
  animated.gif      2-frame GIF — one sheet per image regardless of frame
                    count (frame 1 is the sheet content).

Pillow only (MIT-licensed, no cv2 here — generators never detect).
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "raster"


def _png_bytes(image: Image.Image) -> bytes:
    """PNG bytes deterministically (no optimization variance)."""
    import io

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def build_plan() -> bytes:
    """400x300 floor plan: 6px ring + 6px vertical divider (2 rooms)."""
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 379, 279], outline="black", width=6)
    draw.line([200, 23, 200, 276], fill="black", width=6)
    return _png_bytes(image)


def build_border_rect() -> bytes:
    """320x240 single 3px black-bordered rectangle."""
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 279, 199], outline="black", width=3)
    return _png_bytes(image)


def build_blank() -> bytes:
    """All-white image — parses, zero candidates (honest absence)."""
    return _png_bytes(Image.new("RGB", (320, 240), "white"))


def build_truncated() -> bytes:
    """Valid PNG whose pixel data is cut — decode must refuse.

    Built by taking the good border_rect PNG and slicing into the IDAT so
    the header stays valid but the zlib stream cannot finish.
    """
    good = build_border_rect()
    idat_at = good.find(b"IDAT")
    assert idat_at > 0, "fixture PNG must contain an IDAT chunk"
    # keep the 8-byte length+type of IDAT, then a quarter of the data
    return good[: idat_at + 8 + 64]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def build_oversized() -> bytes:
    """Header-only PNG declaring 10000x6000 (60 MP) — over the 50 MP guard.

    45 bytes total: Pillow's lazy open reads IHDR (so size is known) but
    the pixel guard fires before any decode; no megabytes materialize.
    Crafted by hand (not PIL) precisely so the dimensions can lie.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 10_000, 6_000, 8, 2, 0, 0, 0)  # 8-bit RGB
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IEND", b"")


def build_animated() -> bytes:
    """2-frame GIF — one sheet per image (frame 1 is the sheet content).

    Frame 1 draws a 2px rectangle whose edges are >= 40 px long so every
    edge is honest wall-segment evidence for the detector (the declared
    MIN_WALL_SEGMENT_PX threshold); frame 2 is blank.
    """
    import io

    frame1 = Image.new("RGB", (120, 100), "white")
    ImageDraw.Draw(frame1).rectangle([15, 15, 104, 84], outline="black", width=2)
    frame2 = Image.new("RGB", (120, 100), "white")
    buffer = io.BytesIO()
    frame1.save(
        buffer, "GIF", save_all=True, append_images=[frame2],
        duration=100, loop=0,
    )
    return buffer.getvalue()


FIXTURES: dict[str, Callable[[], bytes]] = {
    "plan.png": build_plan,
    "border_rect.png": build_border_rect,
    "blank.png": build_blank,
    "truncated.png": build_truncated,
    "oversized.png": build_oversized,
    "animated.gif": build_animated,
}


def _check_all() -> None:
    """Self-check: builders are byte-deterministic and every file is small."""
    for name, build in FIXTURES.items():
        first, second = build(), build()
        assert first == second, f"{name} must be byte-deterministic"
        assert len(first) < 20_000, (
            f"{name} is {len(first)} bytes — fixtures must stay under 20 KB "
            "(the oversized-refusal fixture must stay a header stub)"
        )
    # The truncated fixture must keep a valid signature but fail to decode.
    truncated = build_truncated()
    assert truncated.startswith(b"\x89PNG\r\n\x1a\n"), "truncated must keep the PNG signature"
    # The oversized fixture must declare over the guard from its header.
    import io

    from PIL import Image

    oversized = build_oversized()
    with Image.open(io.BytesIO(oversized)) as probe:
        width, height = probe.size
        assert width * height > 50_000_000, (
            f"oversized fixture declares {width}x{height} — must exceed the 50 MP guard"
        )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _check_all()
    for name, build in FIXTURES.items():
        path = OUT / name
        data = build()
        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                existing_digest = hashlib.sha256(existing).hexdigest()
                new_digest = hashlib.sha256(data).hexdigest()
                raise SystemExit(
                    f"{name} is NOT byte-stable across regeneration:\n"
                    f"  on disk sha256 {existing_digest}\n"
                    f"  rebuilt  sha256 {new_digest}\n"
                    "A Pillow change altered the encoding — investigate before "
                    "rewriting committed fixtures."
                )
            print(f"unchanged {path} ({len(data)} bytes)")
            continue
        path.write_bytes(data)
        print(f"wrote {path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
