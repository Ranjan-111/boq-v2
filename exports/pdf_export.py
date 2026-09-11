"""PDF export (T102) — priced BOQ rows → deterministic reportlab PDF bytes.

Same contract as csv_export: same Protocol rows, same validate_export gate,
same Decimal/int-only money formatting. The exportlibs extra (reportlab,
BSD) is imported here only — never re-exported.

Determinism contract: reportlab embeds wall-clock creation time and a
random file fingerprint in the PDF info unless `invariant` mode is on.
SimpleDocTemplate forwards its `invariant` kwarg into the Canvas, which
switches pdfdoc to fixed timestamps and no fingerprint — verified
byte-identical across calls in test_pdf_export.py (the contract the export
manifest's sha256 depends on).
"""
from __future__ import annotations

import io
from collections.abc import Sequence

# reportlab ships no py.typed/stubs package yet — per-line ignores, the
# ingestion/dxf (ezdxf) idiom. Swap for a types- package when one is blessed.
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4, landscape  # type: ignore[import-untyped]
from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from exports.csv_export import (
    HEADER,
    CsvExportRow,
    ExportApproval,
    fmt_money_minor,
    fmt_quantity,
    validate_export,
)

_GRAND_TOTAL = "GRAND TOTAL"
# 10 columns + margins: landscape A4 (842 x 595 pt) fits without wrapping.
_COL_WIDTHS = (34, 44, 200, 32, 62, 48, 46, 62, 44, 176)


def pdf_bytes(
    rows: Sequence[CsvExportRow], *, approval: ExportApproval | None = None,
    title: str = "Bill of Quantities",
) -> bytes:
    """Render BOQ rows as PDF bytes (sha256-able, byte-deterministic).

    Columns mirror the CSV exactly — quantity 6dp string, rate/total 2dp
    strings via Decimal division (never float). V1 rows are flat (no
    section grouping), so a single GRAND TOTAL row closes the table.
    """
    validate_export(rows, approval)
    styles = getSampleStyleSheet()
    total_minor = 0
    data: list[list[str]] = [list(HEADER)]
    for i, row in enumerate(rows, start=1):
        data.append([
            str(i),
            row.code,
            row.description,
            row.unit,
            fmt_quantity(row.quantity),
            fmt_money_minor(row.rate_minor),
            str(row.markup_bp),
            fmt_money_minor(row.total_minor),
            row.currency,
            ";".join(row.measurement_ids),
        ])
        total_minor += row.total_minor
    data.append(["", _GRAND_TOTAL, "", "", "", "", "",
                 fmt_money_minor(total_minor), rows[0].currency, ""])
    table = Table(data, colWidths=_COL_WIDTHS, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),  # grand total
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEEEEE")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), title=title, invariant=1,
        leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18,
    )
    doc.build([Paragraph(title, styles["Title"]), table])
    return buf.getvalue()
