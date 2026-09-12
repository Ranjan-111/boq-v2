"""T125 §G XLSX benchmark — 5k-row BOQ export < 10s.

The measured operation is `exports.xlsx_export.xlsx_bytes` — the
DETERMINISTIC openpyxl writer (fixed zip timestamps + fixed
DocumentProperties, byte-identical output for identical rows) behind the same
validate_export gate as CSV. The 5k rows are GENERATED at test time
(tests/fixtures/generate_bench_fixtures.xlsx_export_rows: consistent
quantity/rate/total so the gate recomputes every row, unique canonical
measurement identities). Timing wraps the writer call ONLY.

Asserted against §G as written: "export XLSX 5k rows < 10s".
"""
from __future__ import annotations

import os
import time

import pytest

from core.domain.enums import BoqStatus
from exports.csv_export import ExportApproval, rows_digest
from exports.xlsx_export import xlsx_bytes
from tests.fixtures.generate_bench_fixtures import BOQ_ITEMS, xlsx_export_rows

# §G: "export XLSX 5k rows < 10s"
XLSX_TARGET_SECONDS = 10.0

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("BOQ_PERF", "") != "1",
        reason="BOQ_PERF=1 not set — perf benchmarks are opt-in (CI perf job)",
    ),
]


class TestXlsxExport5k:
    def test_xlsx_5k_rows_under_10s(self) -> None:
        """§G: 5k priced rows render to deterministic xlsx bytes in < 10s."""
        rows = xlsx_export_rows(BOQ_ITEMS)
        assert len(rows) == BOQ_ITEMS
        approval = ExportApproval(
            boq_id="bench-boq", approval_id="bench-approval",
            rows_digest=rows_digest(rows), status=BoqStatus.APPROVED)

        start = time.perf_counter()
        data = xlsx_bytes(rows, approval=approval)
        elapsed = time.perf_counter() - start

        assert len(data) > 0
        # The writer's determinism contract at scale: same rows → same bytes
        # (the export manifest sha256 is meaningful only if this holds).
        assert xlsx_bytes(rows, approval=approval) == data
        assert elapsed < XLSX_TARGET_SECONDS, (
            f"XLSX 5k-row export took {elapsed:.2f}s "
            f"(§G target < {XLSX_TARGET_SECONDS:.0f}s)")
        print(f"\nxlsx 5k rows: {elapsed:.2f}s, {len(data) / 1e6:.2f} MB")
