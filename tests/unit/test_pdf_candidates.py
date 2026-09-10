"""T047 — PDF candidate detectors: advisory areas/lengths/counts, never measured.

The doctrine under test:
  * candidates are heuristic ADVISORY outputs — they never become MEASURED
    rows, register no rules, and present no authoritative quantity,
  * area/length candidates carry the declared 0.5 confidence, counts 0.7,
    as PINNED constants (arbitrary-but-honest, documented in the module),
  * values are in DRAWING UNITS (pt / pt²) and labeled candidate-only,
  * kernel-refused rings (bowtie/open) produce no candidate — the same
    honesty gate measurements use,
  * count_by_example returns None for a missing seed (caller surfaces the
    refusal) and counts by exact bbox width AND height within 1e-6 pt.

Unit-marked: pure functions over fixture-parsed geometry, no DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.enums import GeomType, SourceFormat
from core.geometry import NormalizedGeometry, SourceHandleRef
from ingestion.pdf import parse_pdf
from takeoff.pdf_candidates import (
    AREA_LENGTH_CANDIDATE_CONFIDENCE,
    BBOX_MATCH_TOLERANCE_PT,
    COUNT_CANDIDATE_CONFIDENCE,
    PdfAreaCandidate,
    PdfCountCandidate,
    PdfLengthCandidate,
    count_by_example,
    detect_closed_area_candidates,
    detect_length_candidates,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"
ROOT = Path(__file__).resolve().parents[2]  # repo root, cwd-independent


def _geometries_of(name: str) -> list[NormalizedGeometry]:
    return list(parse_pdf((FIXTURES / name).read_bytes()).geometries)


def _rect(
    index: int, x0: float, top: float, x1: float, bottom: float,
    page: int = 0,
) -> NormalizedGeometry:
    """A parser-shaped rect ring (5 coords, closed) with a stable ref."""
    return NormalizedGeometry(
        geom_type=GeomType.POLYGON,
        coordinates=[
            (x0, top), (x1, top), (x1, bottom), (x0, bottom), (x0, top),
        ],
        source_format=SourceFormat.PDF_VECTOR,
        source_handles=(SourceHandleRef(
            format=SourceFormat.PDF_VECTOR,
            sheet_ref=f"page:{page}",
            entity_ref=f"p{page}:rect:{index}",
        ),),
    )


@pytest.mark.unit
class TestAreaCandidates:
    def test_closed_rects_yield_exact_areas(self) -> None:
        # Fixture rects: 240x120, 180x90, 120x60 (pt).
        candidates = detect_closed_area_candidates(_geometries_of("vector_rects.pdf"))
        assert sorted(round(c.area_pt2, 6) for c in candidates) == [
            7200.0, 16200.0, 28800.0,
        ]

    def test_candidate_carries_bbox_handles_confidence_why(self) -> None:
        (candidate,) = detect_closed_area_candidates(
            _geometries_of("curves.pdf")
        )
        assert isinstance(candidate, PdfAreaCandidate)
        assert candidate.bbox == (72.0, 32.0, 312.0, 152.0)
        assert [h.entity_ref for h in candidate.source_handles] == ["p0:rect:0"]
        assert candidate.confidence == AREA_LENGTH_CANDIDATE_CONFIDENCE == 0.5
        assert "candidate-only" in candidate.why

    def test_open_polylines_are_not_area_candidates(self) -> None:
        geoms = _geometries_of("vector_rects.pdf")
        assert all(c.source_handles[0].entity_ref.startswith("p0:rect:")
                   for c in detect_closed_area_candidates(geoms))

    def test_bowtie_ring_refused_as_candidate(self) -> None:
        """A self-intersecting ring yields NO candidate — never |shoelace|.

        The kernel's honesty gate applies to candidates too: a bowtie's
        absolute shoelace would understate the true enclosed area.
        """
        bowtie = NormalizedGeometry(
            geom_type=GeomType.POLYGON,
            coordinates=[
                (0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0),
                (0.0, 0.0),
            ],
            source_format=SourceFormat.PDF_VECTOR,
            source_handles=(SourceHandleRef(
                format=SourceFormat.PDF_VECTOR, sheet_ref="page:0",
                entity_ref="p0:curve:0",
            ),),
        )
        assert detect_closed_area_candidates([bowtie]) == []

    def test_degenerate_rings_refused(self) -> None:
        zero_area = _rect(0, 10.0, 10.0, 10.0, 110.0)  # zero width
        assert detect_closed_area_candidates([zero_area]) == []


@pytest.mark.unit
class TestLengthCandidates:
    def test_straight_polylines_yield_exact_lengths(self) -> None:
        # Fixture path: 180pt horizontal + 120pt vertical = 300pt.
        (candidate,) = detect_length_candidates(_geometries_of("vector_rects.pdf"))
        assert isinstance(candidate, PdfLengthCandidate)
        assert candidate.length_pt == pytest.approx(300.0)
        assert [h.entity_ref for h in candidate.source_handles] == ["p0:curve:0"]

    def test_confidence_pinned_and_candidate_only(self) -> None:
        (candidate,) = detect_length_candidates(_geometries_of("vector_rects.pdf"))
        assert candidate.confidence == AREA_LENGTH_CANDIDATE_CONFIDENCE == 0.5
        assert "candidate-only" in candidate.why

    def test_polygons_are_not_length_candidates(self) -> None:
        """Rings deliberately excluded: perimeter ≠ run length (semantic
        guess a candidate layer must not smuggle in)."""
        assert detect_length_candidates(_geometries_of("curves.pdf")) == []


@pytest.mark.unit
class TestCountByExample:
    def test_seed_found_counts_exact_matches_including_seed(self) -> None:
        # 240x120 rect A + two 240x120 duplicates elsewhere on the page.
        seed = _rect(0, 72.0, 32.0, 312.0, 152.0)
        twins = [
            _rect(1, 400.0, 32.0, 640.0, 152.0),
            _rect(2, 72.0, 300.0, 312.0, 420.0),
        ]
        # A different-sized rect must not be counted.
        other = _rect(3, 72.0, 500.0, 132.0, 530.0)  # 60x30
        result = count_by_example([*twins, other, seed], "p0:rect:0")
        assert isinstance(result, PdfCountCandidate)
        assert result.count == 3, "seed + both same-bbox twins"
        assert result.pattern_ref == "p0:rect:0"
        assert [h.entity_ref for h in result.source_handles] == [
            "p0:rect:0", "p0:rect:1", "p0:rect:2",
        ], "deterministic ordering by handle"
        assert result.confidence == COUNT_CANDIDATE_CONFIDENCE == 0.7
        assert "candidate-only" in result.why

    def test_rotated_shape_is_not_counted_by_bbox(self) -> None:
        """BBox matching is exact width AND height: a 90°-rotated twin
        (120x240 vs the 240x120 seed) does NOT match — count-by-example is
        bbox-exact, orientation-sensitive, and never guesses a rotation."""
        seed = _rect(0, 72.0, 32.0, 312.0, 152.0)  # 240 x 120
        rotated = _rect(1, 72.0, 200.0, 192.0, 440.0)  # 120 x 240
        result = count_by_example([seed, rotated], "p0:rect:0")
        assert isinstance(result, PdfCountCandidate)
        assert result.count == 1, "rotated twin has swapped bbox dimensions"

    def test_within_tolerance_counts_exact_size_match(self) -> None:
        seed = _rect(0, 0.0, 0.0, 240.0, 120.0)
        near = _rect(1, 0.0, 0.0, 240.0 + BBOX_MATCH_TOLERANCE_PT / 2,
                     120.0 + BBOX_MATCH_TOLERANCE_PT / 2)
        result = count_by_example([seed, near], "p0:rect:0")
        assert isinstance(result, PdfCountCandidate)
        assert result.count == 2

    def test_beyond_tolerance_is_not_counted(self) -> None:
        seed = _rect(0, 0.0, 0.0, 240.0, 120.0)
        far = _rect(1, 0.0, 0.0, 240.0 + 0.1, 120.0)
        result = count_by_example([seed, far], "p0:rect:0")
        assert isinstance(result, PdfCountCandidate)
        assert result.count == 1, "only the seed matches"

    def test_seed_missing_returns_none(self) -> None:
        geoms = _geometries_of("vector_rects.pdf")
        assert count_by_example(geoms, "p0:rect:99") is None
        assert count_by_example([], "p0:rect:0") is None

    def test_seed_may_be_referenced_by_any_of_its_handles(self) -> None:
        seed = NormalizedGeometry(
            geom_type=GeomType.POLYLINE,
            coordinates=[(0.0, 0.0), (240.0, 0.0)],
            source_format=SourceFormat.PDF_VECTOR,
            source_handles=(
                SourceHandleRef(format=SourceFormat.PDF_VECTOR,
                                sheet_ref="page:0", entity_ref="p0:curve:0"),
                SourceHandleRef(format=SourceFormat.PDF_VECTOR,
                                sheet_ref="page:0", entity_ref="p0:curve:1"),
            ),
        )
        result = count_by_example([seed], "p0:curve:1")
        assert isinstance(result, PdfCountCandidate)
        assert result.count == 1


@pytest.mark.unit
class TestDeterminism:
    def test_same_inputs_identical_outputs(self) -> None:
        geoms = _geometries_of("vector_rects.pdf")
        assert detect_closed_area_candidates(geoms) == detect_closed_area_candidates(geoms)
        assert detect_length_candidates(geoms) == detect_length_candidates(geoms)
        assert count_by_example(geoms, "p0:rect:0") == count_by_example(geoms, "p0:rect:0")

    def test_fixture_order_is_deterministic(self) -> None:
        refs = [c.source_handles[0].entity_ref
                for c in detect_closed_area_candidates(_geometries_of("twopage.pdf"))]
        assert refs == ["p0:rect:0", "p1:rect:0"]


@pytest.mark.unit
class TestDoctrinePins:
    """The module must stay an advisory library — same guard style as
    test_architecture_guards.test_engine_measures_through_registered_rule_callable."""

    def test_module_never_calls_run_rule_or_registers_rules(self) -> None:
        source = (ROOT / "takeoff" / "pdf_candidates.py").read_text(encoding="utf-8")
        assert "run_rule(" not in source, (
            "candidates are advisory: measuring goes through the versioned registry"
        )
        assert "@register" not in source and "rules.register" not in source, (
            "candidates must not register rules"
        )
        # The doctrine is QUOTED in the docstring ("emits no MeasurementState");
        # the pin is on usage: no state construction or attribute access.
        assert "MeasurementState." not in source, (
            "candidates never carry or construct a measurement state"
        )

    def test_rule_registry_unchanged_by_import(self) -> None:
        from takeoff import rules

        before = [r.rule_id for r in rules.all_rules()]
        import takeoff.pdf_candidates  # noqa: F401 — the import is the probe

        assert [r.rule_id for r in rules.all_rules()] == before

    def test_confidence_constants_are_declared_honest_values(self) -> None:
        """Pinned: arbitrary-but-declared, documented in the module docstring."""
        assert AREA_LENGTH_CANDIDATE_CONFIDENCE == 0.5
        assert COUNT_CANDIDATE_CONFIDENCE == 0.7
        assert BBOX_MATCH_TOLERANCE_PT == 1e-6
