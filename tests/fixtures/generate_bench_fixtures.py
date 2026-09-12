"""T125 bench-fixture generators — DETERMINISTIC, GENERATED AT TEST TIME.

The §G performance fixtures (docs/architecture.md §G) are too large to
commit: a 50k-entity DXF (~7 MB), a 100-page PDF and a 5k-row BOQ payload.
These builders generate them in-memory at test time instead, seeded and
byte-deterministic (same input → same bytes, so a rerun reproduces the same
measurement run), reusing the round-3/5 generator idioms:

  * DXF  — tests/fixtures/generate_dxf_fixtures.py's ezdxf approach
           (wall pairs via parallel LINEs, $INSUNITS=4 mm, LWPOLYLINE rings,
           TEXT labels as evidence),
  * PDF  — tests/fixtures/generate_pdf_fixtures.py's raw-object builder
           (hand-authored content streams, computed xref offsets; vector rects
           + straight paths + text per page; no bezier — the parser refuses
           curves, and a bench must not spend its budget in the warning path),
  * BOQ  — 5k mapped items + measurement rows shaped like a real run's output
           (durable uuid5 identities, one wall element each, upstream
           corrected_value on every row so the recompute has real work).

Determinism: coordinates derive from the loop index alone (no clock, no
random, no network). ezdxf TEXT insertions use .set_placement, the same
idiom as the committed fixtures.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# 50k-entity DXF (§G: parse+normalize < 60s)
# ---------------------------------------------------------------------------

# Entity mix (all measurable LINE/LWPOLYLINE plus TEXT evidence — the
# adversarial-fixture doctrine's "50k-entity DXF perf fixture doubles as
# correctness at scale"): 23,000 wall pairs (46,000 LINEs) + 2,000 closed
# room rings + 2,000 open polylines = 50,000 measurable entities, plus
# 1,000 TEXT labels.
DXF_WALL_PAIRS = 23_000
DXF_CLOSED_RINGS = 2_000
DXF_OPEN_POLYLINES = 2_000
DXF_TEXT_LABELS = 1_000
DXF_SPACING = 9_000.0  # mm between building origins — no accidental adjacency


def _bench_wall_pair(
    msp: Any, x0: float, y0: float, x1: float, y1: float, t: float,
    layer: str = "WALL",
) -> None:
    """One wall segment as two parallel LINEs (generate_dxf_fixtures idiom)."""
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5
    nx, ny = -dy / length, dx / length
    msp.add_line((x0 + nx * t / 2, y0 + ny * t / 2),
                 (x1 + nx * t / 2, y1 + ny * t / 2), dxfattribs={"layer": layer})
    msp.add_line((x0 - nx * t / 2, y0 - ny * t / 2),
                 (x1 - nx * t / 2, y1 - ny * t / 2), dxfattribs={"layer": layer})


def build_50k_dxf() -> bytes:
    """~50k measurable entities + 1k TEXT labels, $INSUNITS=4 (mm), ASCII DXF.

    ezdxf stamps the wall clock into $TDCREATE/$TDUPDATE/$FINGERPRINTGUID/
    $VERSIONGUID and its fingerprint comment — the documented
    ``write_fixed_meta_data_for_testing`` option pins all of them so two
    builds are BYTE-IDENTICAL (same bytes → same sha256 → a rerun measures
    the same fixture, and a golden-run replay of the bench input stays
    meaningful). The option lives on ezdxf's global options; setting it here
    is test-generator code, so the blast radius is this process only.
    """
    import ezdxf

    ezdxf.options.write_fixed_meta_data_for_testing = True
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm — never guessed
    for layer in ("WALL", "TEXT"):
        if layer not in doc.layers:
            doc.layers.add(layer)
    msp = doc.modelspace()
    t = 200.0  # wall thickness
    for i in range(DXF_WALL_PAIRS):
        x = (i % 100) * DXF_SPACING
        y = (i // 100) * DXF_SPACING
        _bench_wall_pair(msp, x, y, x + 6000.0, y, t)
    for i in range(DXF_CLOSED_RINGS):
        x = (i % 100) * DXF_SPACING + 400.0
        y = (i // 100) * DXF_SPACING + 1000.0
        msp.add_lwpolyline([(x, y), (x + 3000.0, y), (x + 3000.0, y + 2000.0),
                            (x, y + 2000.0)], format="xy", close=True)
    for i in range(DXF_OPEN_POLYLINES):
        x = (i % 100) * DXF_SPACING + 1000.0
        y = (i // 100) * DXF_SPACING + 5000.0
        msp.add_lwpolyline([(x, y), (x + 2000.0, y), (x + 2000.0, y + 1500.0)],
                           format="xy", close=False)
    for i in range(DXF_TEXT_LABELS):
        x = (i % 100) * DXF_SPACING + 2000.0
        y = (i // 100) * DXF_SPACING + 3000.0
        msp.add_text(f"ROOM {i}", dxfattribs={
            "layer": "TEXT", "height": 200}).set_placement((x, y))
    from io import StringIO

    buf = StringIO()
    doc.write(buf)  # ascii DXF
    return buf.getvalue().encode("ascii")


# ---------------------------------------------------------------------------
# 100-page PDF (§G: text+vector extract < 300s)
# ---------------------------------------------------------------------------

PDF_PAGES = 100
PDF_RECTS_PER_PAGE = 40
PDF_PATHS_PER_PAGE = 20


def build_100page_pdf() -> bytes:
    """100 pages, each 40 rects + 20 straight paths + 2 text runs; raw-object PDF.

    Reuses generate_pdf_fixtures' _assemble/_page/_stream/_font helpers for the
    byte layout (letter 612x792, 1-indexed objects, computed xref). Objects
    are 1-indexed but the list 0-indexed: 1 catalog, 2 pages, 3..202
    page/content pairs, 203 the shared font — the font is the LAST object, so
    the list length equals its object number.
    """
    from tests.fixtures.generate_pdf_fixtures import _assemble, _font, _page, _stream

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # patched with the kid list below
    ]
    kids: list[str] = []
    font_obj = 3 + 2 * PDF_PAGES  # 203 — the last object number
    next_obj = 3
    for page in range(PDF_PAGES):
        content = ["1 0 0 RG"]
        for r in range(PDF_RECTS_PER_PAGE):
            x = 20 + (r % 8) * 72
            top = 700 - (r // 8) * 60
            content.append(f"{x} {top} 50 30 re S")
        for r in range(PDF_PATHS_PER_PAGE):
            x = 20 + (r % 5) * 100
            top = 350 - (r // 5) * 50
            content.append(f"{x} {top} m {x + 80} {top} l {x + 80} {top + 40} l S")
        content.append(f"BT /F1 10 Tf 30 760 Td (ROOM {page}) Tj ET")
        content.append("BT /F1 10 Tf 30 40 Td (SCALE 1:100) Tj ET")
        objects.append(_page(f"{next_obj + 1} 0 R",
                             f"<< /Font << /F1 {font_obj} 0 R >> >>"))
        objects.append(_stream("\n".join(content) + "\n"))
        kids.append(f"{next_obj} 0 R")
        next_obj += 2
    objects[1] = (f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {PDF_PAGES} >>"
                  ).encode("ascii")
    objects.append(_font())
    assert len(objects) == font_obj, "object numbering must match the font ref"
    return _assemble(objects)


# ---------------------------------------------------------------------------
# 5k-row BOQ payload (§G: recompute < 2s against live PG)
# ---------------------------------------------------------------------------

BOQ_ITEMS = 5_000


def bench_identity(i: int) -> str:
    """Durable measurement identity: uuid5 of the inputs digest (engine
    convention) — unique per run, stable across reruns of the same seed."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"boq-perf-m-{i}"))


def boq_measurement_rows(n: int = BOQ_ITEMS) -> list[dict[str, Any]]:
    """n measurement-row kwargs shaped like a run's wall-length output.

    Every row carries an upstream corrected_value (value + 1) so the
    recompute benchmark does real work on every mapped item.
    """
    return [
        {
            "measurement_id": bench_identity(i),
            "quantity_type": "length",
            "value": Decimal(i) / Decimal(4),
            "corrected_value": Decimal(i) / Decimal(4) + Decimal("1"),
            "unit": "m",
            "rule_id": "wall.centerline.length.v1",
            "engine_version": "0.5.0",
            "state": "measured",
            "label": f"wall {i}",
        }
        for i in range(n)
    ]


@dataclass(frozen=True, slots=True)
class BenchExportRow:
    """The CsvExportRow structural shape (exports is Protocol-typed).

    quantity/rate/total are consistent — the export gate recomputes the total
    from quantity-times-rate with banker's rounding and refuses rows that lie.
    """

    code: str
    description: str
    unit: str
    quantity: Decimal
    rate_minor: int
    markup_bp: int
    total_minor: int
    currency: str
    measurement_ids: tuple[str, ...]


def xlsx_export_rows(n: int = BOQ_ITEMS) -> list[BenchExportRow]:
    """n priced, gate-clean rows for the deterministic XLSX writer."""
    from core.units.money import apply_markup, multiply_rate

    rows: list[BenchExportRow] = []
    for i in range(n):
        quantity = (Decimal(i) / Decimal(4)).quantize(Decimal("0.000001"))
        rate_minor = 1000 + (i % 500)
        base = multiply_rate(quantity, rate_minor, currency="INR")
        total_minor = (base + apply_markup(base, 0)).amount_minor
        rows.append(BenchExportRow(
            code=f"2.1.{i % 9 + 1}",
            description=f"Brick wall 230mm thick — run variant {i}",
            unit="m",
            quantity=quantity,
            rate_minor=rate_minor,
            markup_bp=0,
            total_minor=total_minor,
            currency="INR",
            measurement_ids=(bench_identity(i),),
        ))
    return rows


__all__ = [
    "BOQ_ITEMS",
    "DXF_CLOSED_RINGS",
    "DXF_OPEN_POLYLINES",
    "DXF_TEXT_LABELS",
    "DXF_WALL_PAIRS",
    "PDF_PAGES",
    "PDF_PATHS_PER_PAGE",
    "PDF_RECTS_PER_PAGE",
    "BenchExportRow",
    "bench_identity",
    "boq_measurement_rows",
    "build_50k_dxf",
    "build_100page_pdf",
    "xlsx_export_rows",
]
