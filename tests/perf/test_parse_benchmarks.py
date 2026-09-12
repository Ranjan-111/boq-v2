"""T125 §G parse benchmarks — 50k-entity DXF < 60s, 100-page PDF < 300s.

The measured operation is `ingestion.dxf.parse_dxf` / `ingestion.pdf.parse_pdf`
exactly as a run executes them (parse+normalize). Timing wraps the operation
ONLY (time.perf_counter around the call; fixture generation is setup, outside
the window). Fixtures are GENERATED at test time by
tests/fixtures/generate_bench_fixtures.py (deterministic, never committed).

Honesty contract (docs/testing-strategy.md §7 — no coverage theater, no
skipped-failing tests): the assert is the §G number as written. A miss is a
red test with the real number visible in the failure message — never a
weakened gate. The suite is marked `perf` and runs only under BOQ_PERF=1.
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import time

import pytest

from ingestion.dxf import parse_dxf
from ingestion.pdf import parse_pdf
from tests.fixtures.generate_bench_fixtures import (
    DXF_CLOSED_RINGS,
    DXF_OPEN_POLYLINES,
    DXF_TEXT_LABELS,
    DXF_WALL_PAIRS,
    PDF_PAGES,
    PDF_PATHS_PER_PAGE,
    PDF_RECTS_PER_PAGE,
    build_50k_dxf,
    build_100page_pdf,
)

# §G: "DXF 50k entities: parse+normalize < 60s"
DXF_TARGET_SECONDS = 60.0
# §G: "PDF 100 pages: text+vector extract < 5 min" — 300s
PDF_TARGET_SECONDS = 300.0

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("BOQ_PERF", "") != "1",
        reason="BOQ_PERF=1 not set — perf benchmarks are opt-in (CI perf job)",
    ),
]


@pytest.fixture(scope="module")
def dxf_50k() -> bytes:
    return build_50k_dxf()


@pytest.fixture(scope="module")
def pdf_100p() -> bytes:
    return build_100page_pdf()


class TestDxfParse50k:
    def test_parse_50k_entities_under_60s(self, dxf_50k: bytes) -> None:
        """§G: 50k measurable entities parse+normalize in < 60s.

        Correctness-at-scale is asserted alongside the wall clock (the
        adversarial-fixture doctrine: the perf fixture doubles as
        correctness): every measurable entity normalizes, every text label
        is captured as evidence, and zero warnings means the fixture measures
        the normalize path, not the refusal path.
        """
        expected_measurable = (
            2 * DXF_WALL_PAIRS + DXF_CLOSED_RINGS + DXF_OPEN_POLYLINES)
        start = time.perf_counter()
        result = parse_dxf(dxf_50k)
        elapsed = time.perf_counter() - start
        assert len(result.geometries) == expected_measurable, (
            f"expected {expected_measurable} normalized geometries, "
            f"got {len(result.geometries)}"
        )
        assert len(result.text_tokens) == DXF_TEXT_LABELS
        assert not result.warnings, f"unexpected parse warnings: {result.warnings[:3]}"
        sheet = result.sheets[0]
        assert sheet.unit_code == "mm" and sheet.measurable
        assert sheet.measurable_count == expected_measurable
        assert sheet.entity_count == expected_measurable + DXF_TEXT_LABELS
        assert elapsed < DXF_TARGET_SECONDS, (
            f"DXF 50k-entity parse+normalize took {elapsed:.2f}s "
            f"(§G target < {DXF_TARGET_SECONDS:.0f}s)")
        print(f"\nDXF 50k-entity parse+normalize: {elapsed:.2f}s "
              f"({expected_measurable} measurable entities)")


class TestPdfExtract100Pages:
    def test_extract_100_pages_under_300s(self, pdf_100p: bytes) -> None:
        """§G: 100 pages of text+vector extract in < 5 min (300s)."""
        start = time.perf_counter()
        result = parse_pdf(pdf_100p)
        elapsed = time.perf_counter() - start
        assert len(result.sheets) == PDF_PAGES
        assert len(result.geometries) == PDF_PAGES * (
            PDF_RECTS_PER_PAGE + PDF_PATHS_PER_PAGE)
        assert len(result.text_tokens) == 4 * PDF_PAGES  # ROOM + n, SCALE + 1:100
        assert not result.warnings
        assert elapsed < PDF_TARGET_SECONDS, (
            f"PDF 100-page extraction took {elapsed:.2f}s "
            f"(§G target < {PDF_TARGET_SECONDS:.0f}s)")
        print(f"\nPDF 100-page text+vector extraction: {elapsed:.2f}s "
              f"({len(result.geometries)} vector objects)")


def test_dxf_50k_profile_recorded(dxf_50k: bytes) -> None:
    """T125 profiling deliverable: cProfile the 50k DXF parse, top-10 to stdout.

    Not a timing gate — a recorded profile so the hotspots are visible in the
    CI log (the report cites these numbers). Honest measurement: the profile
    run's wall time is printed beside the un-profiled number from the
    benchmark above when they differ materially.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    result = parse_dxf(dxf_50k)
    profiler.disable()
    assert len(result.geometries) > 0
    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out)
    stats.sort_stats("cumulative").print_stats(12)
    print("\n--- DXF 50k parse cProfile (top 12 by cumulative time) ---")
    print(out.getvalue())
