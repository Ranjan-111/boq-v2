"""T102 PDF writer tests — the csv_export contract, reportlab-rendered.

Pinned:
  * byte-determinism: two calls with the same rows → identical bytes (the
    manifest sha256 is meaningful only if this holds; reportlab embeds a
    wall-clock timestamp + file fingerprint unless SimpleDocTemplate is
    built with invariant=1),
  * the shared validate_export gate: no approval, stale digest, bad totals,
    mixed currencies all refuse exactly like the CSV writer,
  * grand total row = exact integer-minor sum, formatted 2dp via Decimal,
    rendered into the page content stream (ASCII85+Flate decoded — a real
    text extraction, not a size proxy),
  * the output is a real non-empty PDF (%PDF header),
  * quantity/money strings never show float artifacts.
"""
from __future__ import annotations

import base64
import re
import time
import zlib
from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from core.domain.enums import BoqStatus
from exports.csv_export import ExportApproval, rows_digest
from exports.pdf_export import pdf_bytes

UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"


@dataclass(frozen=True, slots=True)
class Row:
    """The CsvExportRow shape (a local fixture — exports is Protocol-typed)."""

    code: str = "2.1.1"
    description: str = "Brick wall 230mm thick"
    unit: str = "m"
    quantity: Decimal = Decimal("12.5")
    rate_minor: int = 85_000
    markup_bp: int = 750
    total_minor: int = 1_142_188  # 12.5 x 850.00 + 7.5% markup (banker's)
    currency: str = "INR"
    measurement_ids: tuple[str, ...] = (UUID_A,)


def approved(rows: list[Row]) -> ExportApproval:
    return ExportApproval("boq-1", "approval-1", rows_digest(rows),
                          BoqStatus.APPROVED)


def two_rows() -> list[Row]:
    return [
        Row(),
        Row(quantity=Decimal("4.75"), total_minor=434_031,
            measurement_ids=(UUID_B,)),
    ]


class TestDeterminism:
    def test_two_calls_are_byte_identical(self) -> None:
        rows = two_rows()
        approval = approved(rows)
        first = pdf_bytes(rows, approval=approval)
        time.sleep(1.1)  # the default path would now stamp a new timestamp
        assert pdf_bytes(rows, approval=approval) == first

    def test_reexport_with_exported_status_is_identical(self) -> None:
        rows = [Row()]
        approval = approved(rows)
        assert pdf_bytes(rows, approval=approval) == pdf_bytes(
            rows, approval=replace(approval, status=BoqStatus.EXPORTED))


class TestValidationGate:
    """The writers share validate_export — the CSV refusal set, pinned here."""

    def test_no_approval_refused(self) -> None:
        with pytest.raises(ValueError, match="approval"):
            pdf_bytes(two_rows())

    @pytest.mark.parametrize("status", [BoqStatus.DRAFT, BoqStatus.IN_REVIEW,
                                        BoqStatus.REVIEWED,
                                        BoqStatus.STALE_APPROVED])
    def test_unapproved_state_refused(self, status: BoqStatus) -> None:
        rows = two_rows()
        with pytest.raises(ValueError, match="status"):
            pdf_bytes(rows, approval=replace(approved(rows), status=status))

    def test_stale_digest_refused(self) -> None:
        rows = two_rows()
        changed = [replace(rows[0], description="changed")]
        with pytest.raises(ValueError, match="stale"):
            pdf_bytes(changed, approval=approved(rows))

    def test_bad_total_refused(self) -> None:
        rows = [replace(Row(), total_minor=1)]  # not recomputable
        with pytest.raises(ValueError, match="recomputable"):
            pdf_bytes(rows, approval=approved(rows))

    def test_mixed_currency_refused(self) -> None:
        rows = [Row(), Row(currency="USD",
                           measurement_ids=(UUID_B,))]
        with pytest.raises(ValueError, match="mixed currencies"):
            pdf_bytes(rows, approval=approved(rows))

    def test_empty_rows_refused(self) -> None:
        with pytest.raises(ValueError, match="no priced rows"):
            pdf_bytes([], approval=ExportApproval(
                "boq-1", "approval-1", rows_digest([]), BoqStatus.APPROVED))


def _content_text(data: bytes) -> str:
    """Decode the page content stream (reportlab: ASCII85 + Flate) so the
    rendered strings are assertable — a real extraction, not a size proxy."""
    m = re.search(rb"stream\r?\n", data)
    assert m is not None, "content stream present"
    end = data.find(b"endstream", m.end())
    chunk = data[m.end():end].strip().replace(b"\n", b"").replace(b"\r", b"")
    a85 = base64.a85decode(chunk, adobe=True)
    return zlib.decompress(a85).decode("latin-1")


class TestPdfContent:
    def test_is_real_nonempty_pdf(self) -> None:
        rows = two_rows()
        data = pdf_bytes(rows, approval=approved(rows))
        assert data.startswith(b"%PDF-")
        assert len(data) > 0

    def test_content_stream_carries_the_table(self) -> None:
        rows = two_rows()
        data = pdf_bytes(rows, approval=approved(rows),
                          title="Bill of Quantities")
        text = _content_text(data)
        # Header + items + grand total — all rendered as real text.
        for needle in ("sr_no", "2.1.1", "Brick wall 230mm thick",
                       "12.500000", "4.750000", "850.00", "11421.88",
                       "4340.31", "GRAND TOTAL", "Bill of Quantities",
                       "INR", UUID_A, UUID_B):
            assert needle in text, needle

    def test_grand_total_rendered_from_integer_sum(self) -> None:
        rows = two_rows()
        data = pdf_bytes(rows, approval=approved(rows))
        text = _content_text(data)
        expected_minor = rows[0].total_minor + rows[1].total_minor
        expected = f"{expected_minor // 100}.{expected_minor % 100:02d}"
        assert expected in text  # 15762.19: exact integer-minor sum, 2dp
        # Same formatted value the shared formatter produces.
        from exports.csv_export import fmt_money_minor

        assert fmt_money_minor(expected_minor) == expected

    def test_no_float_artifacts_in_money(self) -> None:
        # 0.07 m x 850.00 = 59.50 base; markup 4.46 -> 63.96 exact —
        # a float path would show 63.96000000000001-style strings.
        rows = [Row(quantity=Decimal("0.07"), total_minor=6_396)]
        data = pdf_bytes(rows, approval=approved(rows))
        text = _content_text(data)
        assert "63.96" in text
        assert "0.070000" in text
        assert "63.9600000000000" not in text
