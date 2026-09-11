"""Round 8 — PDF vector candidates through the engine: NEEDS_REVIEW, never MEASURED.

The doctrine under test (takeoff/pdf_candidates.py, quoted there and pinned
here at the ENGINE boundary):
  * emit_candidates=False (default) keeps DXF behavior byte-identical — no
    candidate rows sneak into a standard run,
  * emit_candidates=True surfaces the detectors' outputs as NEEDS_REVIEW
    measurements through the REGISTERED rules (polygon.area.v1 /
    polyline.length.v1) with full replay contracts — never MEASURED at
    construction (a human accepts through the audited review flow; the BOQ
    bills only MEASURED/MEASURED_ZERO),
  * DEDUP is trust-critical: a geometry consumed by wall detection must NOT
    also surface as a candidate (double-billing risk after accept),
  * count candidates are NOT emitted (count_by_example needs a human-picked
    seed the engine cannot invent),
  * determinism: same inputs -> byte-identical RunOutput.

Unit-marked: pure engine over the committed hand-authored fixtures, no DB.
The live-PostgreSQL journey (parse -> PROPOSED -> confirm -> run -> review
accept -> BOQ) lives in backend/tests/test_pdf_review_depth.py.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.domain.enums import (
    ElementType,
    GeomType,
    MeasurementState,
    MeasurementUnit,
    QuantityType,
    ScaleCalibrationStatus,
    SourceFormat,
)
from core.geometry import NormalizedGeometry, SourceHandleRef
from core.units.geometry_units import ScaleCalibration
from ingestion.pdf import parse_pdf
from takeoff.engine import measure_parsed, measure_sheet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"

# The confirmed 1:100 calibration the scale_annotation fixture proposes:
# 100 * 25.4 / 72 mm per PDF point, quantized to 10 places by the proposer.
FACTOR_1_100 = Decimal("35.2777777778")


def _confirmed(sheet_ref: str = "page:0",
               factor: Decimal = FACTOR_1_100) -> ScaleCalibration:
    return ScaleCalibration(
        sheet_id=sheet_ref,
        status=ScaleCalibrationStatus.CONFIRMED,
        units_per_drawing_unit=factor,
        method="bar_scale_detected",
    )


def _run_pdf(name: str = "scale_annotation.pdf", *, emit_candidates: bool = True):
    parsed = parse_pdf((FIXTURES / name).read_bytes())
    return measure_parsed(
        parsed, sheet_id="page:0", calibration=_confirmed(),
        max_wall_thickness=250, emit_candidates=emit_candidates, drawing_units="mm",
    )


@pytest.mark.unit
class TestCandidateEmission:
    def test_default_off_keeps_pdf_runs_candidate_free(self) -> None:
        """emit_candidates defaults to False: the Round 5 behavior (a PDF
        sheet without wall-layer geometry measures nothing) is unchanged."""
        out = _run_pdf(emit_candidates=False)
        assert out.measurements == ()
        assert out.exceptions == ()

    def test_candidates_surface_through_registered_rules(self) -> None:
        out = _run_pdf()
        assert out.exceptions == ()
        rules = {m.rule_id for m in out.measurements}
        # the scale_annotation fixture: one closed rect + one open polyline
        assert rules == {"polygon.area.v1", "polyline.length.v1"}
        area = next(m for m in out.measurements
                    if m.rule_id == "polygon.area.v1")
        length = next(m for m in out.measurements
                      if m.rule_id == "polyline.length.v1")
        # drawing-unit values: rect 240x120 pt = 28800 pt²; path 300 pt.
        # Project values: pt² * (mm/pt)² / 1e6 = m²; pt * (mm/pt) / 1000 = m.
        assert area.value == Decimal("35.842222")
        assert length.value == Decimal("10.583333")
        assert area.unit is MeasurementUnit.M2
        assert length.unit is MeasurementUnit.M

    def test_candidates_are_needs_review_never_measured(self) -> None:
        out = _run_pdf()
        assert out.measurements, "the fixture must produce candidates"
        for m in out.measurements:
            assert m.state is MeasurementState.NEEDS_REVIEW, (
                "a candidate NEVER becomes MEASURED at construction — the "
                "human review accept pivots the state, not the engine"
            )
            assert m.state is not MeasurementState.MEASURED
            assert m.state is not MeasurementState.MEASURED_ZERO

    def test_candidate_evidence_and_replay_contract(self) -> None:
        out = _run_pdf()
        for m in out.measurements:
            # invariant 1: >=1 evidence link, and it points at the source op
            assert m.evidence
            assert all(e.kind == "geometry" for e in m.evidence)
            # replay contract on candidate rows too
            assert len(m.inputs_digest) == 64
            assert m.engine_version == "0.5.0"
            # the drawn source handle is the input (p0:rect:0 / p0:curve:0)
            assert all(ref.startswith("p0:") for ref in m.inputs)

    def test_candidate_label_is_unmissable_and_deterministic(self) -> None:
        out = _run_pdf()
        labels = [m.label for m in out.measurements]
        assert all("candidate" in (label or "") for label in labels), (
            "candidate status must be unmissable in the label"
        )
        # no f-string float noise: Decimal-quantized 6-place values
        assert any("28800.000000 pt2" in label for label in labels)
        assert any("300.000000 pt" in label for label in labels)

    def test_candidates_bind_to_their_own_elements(self) -> None:
        """Each candidate carries an element row (Round 4 persistence shape)
        whose geometry is the drawn shape — so the review UI can highlight
        the evidence on the sheet."""
        out = _run_pdf()
        for m in out.measurements:
            assert m.element_index is not None
            element = out.elements[m.element_index]
            assert element.element_type is ElementType.OTHER
            assert "candidate" in (element.label or "")
            assert element.geometry.geom_type is GeomType.POLYGON or (
                element.geometry.geom_type is GeomType.POLYLINE
            )

    def test_kernel_refused_rings_never_surface(self) -> None:
        """A bowtie ring yields NO candidate: the detectors apply the kernel
        gate, and the engine's polygon-refusal loop surfaces the refusal as
        an exception row instead — never a plausible wrong area."""
        bowtie = NormalizedGeometry(
            geom_type=GeomType.POLYGON,
            coordinates=[(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0), (0.0, 0.0)],
            source_format=SourceFormat.PDF_VECTOR,
            source_handles=(SourceHandleRef(
                format=SourceFormat.PDF_VECTOR, sheet_ref="page:0",
                entity_ref="p0:rect:0"),),
        )
        out = measure_sheet(
            sheet_id="page:0", geometries=[bowtie], calibration=_confirmed(),
            drawing_units="mm", max_wall_thickness=250, emit_candidates=True,
        )
        assert out.measurements == (), "a bowtie must never become a candidate"
        assert any(e.code.value == "self_intersecting" for e in out.exceptions)

    def test_count_candidates_never_emitted(self) -> None:
        """count_by_example needs a human-picked seed: the engine emits no
        count candidates even on rect-heavy sheets (three same-bbox rects in
        vector_rects.pdf would be countable — they are not counted)."""
        out = _run_pdf("vector_rects.pdf")
        counts = [m for m in out.measurements
                  if m.quantity_type is QuantityType.COUNT]
        assert counts == [], "no engine path may emit count candidates"
        # the three closed rects DO surface as area candidates, though
        assert len([m for m in out.measurements
                   if m.rule_id == "polygon.area.v1"]) == 3


@pytest.mark.unit
class TestCandidateDedup:
    """Trust-critical: a geometry consumed by wall detection must not ALSO
    surface as a candidate — accepting both would double-bill the shape."""

    def _faces(self) -> list[NormalizedGeometry]:
        def face(a, b, handle):
            return NormalizedGeometry(
                geom_type=GeomType.POLYLINE, coordinates=[a, b],
                source_format=SourceFormat.PDF_VECTOR,
                source_handles=(SourceHandleRef(
                    format=SourceFormat.PDF_VECTOR, sheet_ref="page:0",
                    entity_ref=handle, layer="Wall"),),
                layer="Wall",
            )
        return [face((0.0, 0.0), (1000.0, 0.0), "p0:line:0"),
                face((0.0, 200.0), (1000.0, 200.0), "p0:line:1")]

    def test_wall_edge_geometry_produces_no_length_candidate(self) -> None:
        stray = NormalizedGeometry(
            geom_type=GeomType.POLYLINE, coordinates=[(50.0, 50.0), (150.0, 50.0)],
            source_format=SourceFormat.PDF_VECTOR,
            source_handles=(SourceHandleRef(
                format=SourceFormat.PDF_VECTOR, sheet_ref="page:0",
                entity_ref="p0:curve:0"),),
        )
        out = measure_sheet(
            sheet_id="page:0", geometries=[*self._faces(), stray],
            calibration=_confirmed(factor=Decimal("1.0")),
            drawing_units="mm", max_wall_thickness=250, emit_candidates=True,
        )
        # the wall measured (4 rows) and ONLY the stray surfaced as candidate
        lengths = [m for m in out.measurements
                   if m.rule_id == "polyline.length.v1"]
        assert len(lengths) == 1
        assert lengths[0].inputs == ("p0:curve:0",)
        wall_rows = [m for m in out.measurements
                     if m.rule_id == "wall.centerline.length.v1"]
        assert len(wall_rows) == 1, "the wall pair still measures"

    def test_unconsumed_ring_surfaces_but_consumed_edges_do_not(self) -> None:
        """Dedup keys on ACTUAL consumption, never a layer guess: the two
        wall faces (consumed by pairing) never surface as length candidates,
        while a closed rect on the same sheet — which extract_segs cannot
        consume (it pairs only straight 2-point polylines) — honestly
        surfaces as an area candidate for review."""
        faces = self._faces()
        wall_layer_rect = NormalizedGeometry(
            geom_type=GeomType.POLYGON,
            coordinates=[(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0),
                         (0.0, 0.0)],
            source_format=SourceFormat.PDF_VECTOR,
            source_handles=(SourceHandleRef(
                format=SourceFormat.PDF_VECTOR, sheet_ref="page:0",
                entity_ref="p0:rect:0", layer="Wall"),),
            layer="Wall",
        )
        out = measure_sheet(
            sheet_id="page:0", geometries=[*faces, wall_layer_rect],
            calibration=_confirmed(factor=Decimal("1.0")),
            drawing_units="mm", max_wall_thickness=250, emit_candidates=True,
        )
        # consumed: the wall faces never surface as candidates
        length_candidates = [m for m in out.measurements
                             if m.rule_id == "polyline.length.v1"]
        assert length_candidates == [], "both wall faces were consumed"
        # unconsumed: the rect surfaces honestly (review decides semantics)
        area_candidates = [m for m in out.measurements
                           if m.rule_id == "polygon.area.v1"]
        assert [m.inputs for m in area_candidates] == [("p0:rect:0",)]
        # and the wall still measured through the deterministic path
        assert len([m for m in out.measurements
                   if m.rule_id == "wall.centerline.length.v1"]) == 1


@pytest.mark.unit
class TestCandidateDeterminism:
    def test_same_inputs_identical_outputs(self) -> None:
        first = _run_pdf()
        second = _run_pdf()
        assert first.measurements == second.measurements
        assert first.exceptions == second.exceptions
        assert first.elements == second.elements
        assert first.engine_version == second.engine_version
        assert [m.measurement_id for m in first.measurements] == [
            m.measurement_id for m in second.measurements]

    def test_candidate_order_follows_detector_output_order(self) -> None:
        """Detector order is input order: areas first (vector_rects: 3 rects
        in content-stream order), then lengths (the one straight path)."""
        out = _run_pdf("vector_rects.pdf")
        inputs = [m.inputs for m in out.measurements]
        assert inputs == [
            ("p0:rect:0",), ("p0:rect:1",), ("p0:rect:2",), ("p0:curve:0",),
        ], "candidate order must follow detector output order (input order)"

    def test_emit_flag_is_a_replay_input(self) -> None:
        """The same geometry with/without candidates is a different replay
        context — the flag rides the digest (selection-parameter doctrine)."""
        on = _run_pdf()
        off = _run_pdf(emit_candidates=False)
        assert off.measurements == ()
        assert on.measurements


@pytest.mark.unit
class TestScaleGateStillGovernsCandidates:
    def test_unconfirmed_scale_blocks_candidates_too(self) -> None:
        """No confirmed scale -> zero rows INCLUDING candidates: advisory
        geometry never escapes the human gate either."""
        parsed = parse_pdf((FIXTURES / "scale_annotation.pdf").read_bytes())
        out = measure_parsed(
            parsed, sheet_id="page:0",
            calibration=ScaleCalibration(
                sheet_id="page:0", status=ScaleCalibrationStatus.PROPOSED,
                units_per_drawing_unit=FACTOR_1_100, method="bar_scale_detected"),
            max_wall_thickness=250, emit_candidates=True, drawing_units="mm",
        )
        assert out.measurements == ()
        assert out.exceptions[0].code.value == "scale_unconfirmed"
        assert out.exceptions[0].severity.value == "blocking"
