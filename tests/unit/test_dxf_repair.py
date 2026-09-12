"""Spec-guided DXF repair (ingestion/dxf/repair.py) — the real-world corpus.

Manual-testing pass (post-R9): real drawings exported by scanners, other CAD
libraries, or AI tools carry structural shortcuts the strict ezdxf loader
refuses while their measurable content is intact. These tests pin the
bounded, honest repair ladder:

  * subclass-marker injection (LWPOLYLINE/HATCH without `100` markers),
  * truncation closure (ENDSEC/EOF tail),
  * annotation-only drop (unparseable HATCH — never measurable geometry),
  * the error-message type extraction that drives the drop,
  * aggregation (one warning per damage class, not per entity).

The corpus files live in `test files/` (untracked, manual-testing inputs)
and are located by name; the tests skip when a file is absent so CI does
not depend on the folder. The committed fixtures in tests/fixtures/dxf
already pin the strict reader's behaviour for spec-clean files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.dxf.repair import (
    close_truncation,
    drop_entity_type,
    entity_type_from_error,
    file_has_entity,
    inject_missing_subclass_markers,
    is_annotation_only,
)

pytestmark = pytest.mark.unit

# The untracked manual-testing folder (repo root / "test files").
CORPUS = Path(__file__).resolve().parents[2] / "test files"


def corpus(name: str) -> bytes:
    p = CORPUS / name
    return p.read_bytes() if p.exists() else b""


def require(name: str) -> bytes:
    data = corpus(name)
    if not data:
        pytest.skip(f"manual-test corpus file not present: {name}")
    return data


# ---------------------------------------------------------------------------
# Subclass-marker injection
# ---------------------------------------------------------------------------


class TestSubclassMarkerInjection:
    def test_markerless_lwpolyline_repaired_and_readable(self) -> None:
        """residual_test.dxf: 26 LWPOLYLINEs with no 100 markers crash the
        strict loader; the rewrite makes the same entities loadable. (The
        file's HATCHes are separately unparseable — the annotation-drop
        rung covers them; here we assert the polyline content loads once
        the annotation noise is out of the way.)"""
        import io

        import ezdxf

        data = require("residual_test.dxf")
        repaired, warnings = inject_missing_subclass_markers(data)
        assert warnings, "expected repair warnings for the marker-less file"
        assert any("LWPOLYLINE" in w for w in warnings)
        # drop the (separately unparseable) annotation so the polyline
        # repair itself is what's under test
        sans_hatch, _ = drop_entity_type(repaired, "HATCH")
        doc = ezdxf.read(io.StringIO(sans_hatch.decode("utf-8")))
        msp = doc.modelspace()
        polys = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
        assert len(polys) >= 7, "LWPOLYLINEs must load after marker injection"

    def test_clean_file_passes_through_byte_identical(self) -> None:
        """A spec-clean DXF must not be rewritten at all — zero cost."""
        import io

        import ezdxf

        doc = ezdxf.new("R2013")
        doc.modelspace().add_lwpolyline(
            [(0, 0), (100, 0), (100, 50), (0, 50)], close=True
        )
        buf = io.StringIO()
        doc.write(buf)
        original = buf.getvalue().encode("utf-8")
        repaired, warnings = inject_missing_subclass_markers(original)
        assert repaired == original
        assert warnings == []

    def test_warning_is_one_per_type_not_per_entity(self) -> None:
        """26 broken polylines -> ONE aggregated warning line."""
        data = require("residual_test.dxf")
        _, warnings = inject_missing_subclass_markers(data)
        poly_warnings = [w for w in warnings if "LWPOLYLINE" in w]
        assert len(poly_warnings) == 1
        assert "26 LWPOLYLINE" in poly_warnings[0]

    def test_compliant_entities_left_alone(self) -> None:
        """Entities that DO carry 100 markers pass through untouched."""
        data = require("Gplus2_combined_layers_fixed.dxf")
        _repaired, warnings = inject_missing_subclass_markers(data)
        # the file has marker-less LWPOLYLINEs (repaired) AND marker-carrying
        # INSERTs (must not be rewritten — no INSERT warning appears).
        assert not any("INSERT" in w for w in warnings)
        assert any("LWPOLYLINE" in w for w in warnings)


# ---------------------------------------------------------------------------
# Truncation closure
# ---------------------------------------------------------------------------


class TestTruncationClosure:
    def test_truncated_file_gets_endsec_eof(self) -> None:
        """Gemini file: cut mid-tag before EOF; closure appends the missing
        structural tail so the complete measured content loads."""
        import io

        import ezdxf

        data = require("Gemini Residual Test AI Drawing.dxf")
        markered, _ = inject_missing_subclass_markers(data)
        closed, warnings = close_truncation(markered)
        assert warnings, "the truncated tail must be reported"
        assert warnings[0].startswith("closed truncated tail")
        doc = ezdxf.read(io.StringIO(closed.decode("utf-8")))
        assert doc.modelspace()

    def test_complete_file_not_modified(self) -> None:
        data = require("Sample Floor Plan.dxf")
        closed, warnings = close_truncation(data)
        assert closed == data
        assert warnings == []


# ---------------------------------------------------------------------------
# Annotation-only drop
# ---------------------------------------------------------------------------


class TestAnnotationDrop:
    def test_unparseable_hatch_dropped_not_fatal(self) -> None:
        """clean-plan.dxf: HATCHes missing the required loop-count tag cannot
        load; dropping them keeps the walls (LWPOLYLINEs) loadable."""
        import io

        import ezdxf

        data = require("clean-plan.dxf")
        markered, _ = inject_missing_subclass_markers(data)
        # sanity: the HATCHes still do not load with markers alone (their
        # loop-count tag is genuinely missing — a marker repair cannot fix it)
        from ezdxf.lldxf.const import DXFStructureError

        with pytest.raises(DXFStructureError):
            ezdxf.read(io.StringIO(markered.decode("utf-8")))
        dropped, count = drop_entity_type(markered, "HATCH")
        assert count == 5
        doc = ezdxf.read(io.StringIO(dropped.decode("utf-8")))
        msp = doc.modelspace()
        assert [e for e in msp if e.dxftype() == "LWPOLYLINE"]

    def test_annotation_only_classification(self) -> None:
        assert is_annotation_only("HATCH")
        assert is_annotation_only("DIMENSION")
        assert not is_annotation_only("LWPOLYLINE")
        assert not is_annotation_only("LINE")
        assert not is_annotation_only("POLYLINE")
        assert not is_annotation_only("TEXT")

    def test_file_has_entity(self) -> None:
        data = require("residual_test.dxf")
        assert file_has_entity(data, "HATCH")
        assert file_has_entity(data, "LWPOLYLINE")
        assert not file_has_entity(data, "DIMENSION")


# ---------------------------------------------------------------------------
# Error-type extraction (drives the drop rung)
# ---------------------------------------------------------------------------


class TestErrorTypeExtraction:
    def test_type_labeled_error(self) -> None:
        assert (
            entity_type_from_error(
                "DXFStructureError: HATCH: Missing required DXF tag "
                "'Number of boundary paths (loops)' (code=91)."
            )
            == "HATCH"
        )

    def test_handle_labeled_error(self) -> None:
        assert (
            entity_type_from_error(
                "DXFStructureError: missing 'AcDbPolyline' subclass in LWPOLYLINE(#422)"
            )
            == "LWPOLYLINE"
        )

    def test_no_type_in_error(self) -> None:
        assert entity_type_from_error("IndexError: list index out of range") is None
        assert entity_type_from_error("AssertionError") is None


# ---------------------------------------------------------------------------
# End-to-end parse through the ladder (fixtures committed to the repo)
# ---------------------------------------------------------------------------


class TestLadderEndToEnd:
    def test_repair_ladder_parses_the_manual_corpus(self) -> None:
        """Every file from the real manual-testing session that failed
        strictly now parses — or skips if the untracked corpus is absent."""
        import sys

        sys.path.insert(0, str(CORPUS.parent))
        from ingestion.dxf import parse_dxf

        expected = {
            "residual_test.dxf": 30,
            "Gplus2_combined_layers_fixed.dxf": 99,
            "clean-plan.dxf": 2,
            "Gemini Residual Test AI Drawing.dxf": 5,
        }
        for name, min_geoms in expected.items():
            data = corpus(name)
            if not data:
                pytest.skip(f"manual-test corpus file not present: {name}")
            result = parse_dxf(data)
            assert len(result.geometries) >= min_geoms, (
                f"{name}: expected >= {min_geoms} geometries, got "
                f"{len(result.geometries)}"
            )
            # every applied repair surfaces — value-preserving repairs as
            # NOTICES (non-blocking; they made entities measurable, refused
            # nothing), annotation drops as the skip COUNT. Post-R9 manual
            # pass: none of them is a blocking warning any more, which is
            # exactly why these real-world files can now be measured.
            assert (
                any(("repaired" in w) or ("closed" in w) for w in result.notices)
                or result.annotations_skipped > 0
            ), f"{name}: repair applied without surfacing"
            assert not any(
                ("repaired" in w) or ("closed" in w) or ("dropped" in w)
                for w in result.warnings
            ), f"{name}: value-preserving repair must not block measurement"

    def test_strict_fixture_still_parses_strictly(self) -> None:
        """The committed spec-clean fixture must not go through any repair
        rung — no repair notices, warnings, or annotation drops on it."""
        import sys

        sys.path.insert(0, str(CORPUS.parent))
        from ingestion.dxf import parse_dxf

        fixture = (
            CORPUS.parent / "tests" / "fixtures" / "dxf" / "wall_plan.dxf"
        ).read_bytes()
        result = parse_dxf(fixture)
        assert not any(
            ("repaired" in w) or ("closed" in w) or ("dropped" in w)
            for w in (*result.warnings, *result.notices))
