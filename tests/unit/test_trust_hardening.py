"""Round 3 trust-hardening gate — adversarial regressions.

Each test pins one "cannot silently produce a believable but wrong quantity"
invariant from docs/domain-model.md §"Round 3 trust-hardening semantics":
disjoint/ambiguous wall refusal, evidence enforcement, content-bound replay,
scale/sheet validation, and parser-warning propagation to the measurement
boundary via measure_parsed.
"""
from dataclasses import replace
from decimal import Decimal

import pytest
from test_engine import CONFIRMED, F_A, F_B
from test_wall_detection import edge

from core.domain.enums import BoqStatus, ExceptionSeverity, MeasurementState
from core.geometry import ParseResult, SheetSummary
from core.provenance.records import ExceptionRecord
from takeoff.engine import measure_parsed, measure_sheet
from takeoff.rules import run_rule
from takeoff.wall_detection import detect_walls


@pytest.mark.parametrize('start,end', [(10000,11000),(500,1500),(0,1500),(1000,2000)])
def test_no_longitudinal_support_no_wall(start, end):
    result = detect_walls([F_A, edge((start,200),(end,200),'B')], max_thickness=250)
    assert not result.walls
    assert len(result.unmatched_edges) == 2


def test_ambiguous_component_never_greedily_measured():
    third = edge((0,400),(1000,400),'C')
    result = detect_walls([F_A,F_B,third], max_thickness=250)
    assert not result.walls
    assert result.overlaps
    assert result == detect_walls([third,F_B,F_A], max_thickness=250)


def test_explicit_thickness_selection_required():
    assert not detect_walls([F_A,F_B]).walls


@pytest.mark.parametrize('missing', [0,1,2])
def test_each_face_requires_evidence(missing):
    faces = [F_A,F_B]
    for i in range(2):
        if missing == 2 or missing == i:
            faces[i] = replace(faces[i], source_handles=())
    out = measure_sheet(sheet_id='modelspace',geometries=faces,calibration=CONFIRMED,
                        drawing_units='mm',max_wall_thickness=250)
    assert not out.measurements
    assert any(e.code == 'missing_evidence' and e.severity is ExceptionSeverity.BLOCKING
               for e in out.exceptions)


def test_disjoint_rule_replay_refused():
    with pytest.raises(ValueError):
        run_rule('wall.centerline.length.v1',[F_A,edge((10000,200),(11000,200),'B')])


@pytest.mark.parametrize('bad_ref', ['0', 'None', '', '  '])
def test_malformed_evidence_ref_refused(bad_ref):
    faces = [F_A, replace(F_B, source_handles=(
        replace(F_B.source_handles[0], entity_ref=bad_ref),))]
    out = measure_sheet(sheet_id='modelspace',geometries=faces,calibration=CONFIRMED,
                        drawing_units='mm',max_wall_thickness=250)
    assert not out.measurements
    assert any(e.code == 'missing_evidence' and e.severity is ExceptionSeverity.BLOCKING
               for e in out.exceptions)


@pytest.mark.parametrize('factor', [None,Decimal(0),Decimal(-1),Decimal('NaN'),Decimal('Infinity')])
def test_invalid_confirmed_scale_refused(factor):
    out = measure_sheet(sheet_id='modelspace',geometries=[F_A,F_B],
                        calibration=replace(CONFIRMED, units_per_drawing_unit=factor),
                        drawing_units='mm',max_wall_thickness=250)
    assert not out.measurements
    assert out.exceptions[0].severity is ExceptionSeverity.BLOCKING


def test_digest_tracks_content_and_scale():
    def measure(faces,cal=CONFIRMED):
        return measure_sheet(sheet_id='modelspace',geometries=faces,calibration=cal,
                             drawing_units='mm',max_wall_thickness=250).measurements[0]
    base = measure([F_A,F_B])
    moved = measure([replace(F_A,coordinates=[(0,0),(2000,0)]),
                     replace(F_B,coordinates=[(0,200),(2000,200)])])
    scaled = measure([F_A,F_B],replace(CONFIRMED,units_per_drawing_unit=Decimal(2)))
    assert len({base.inputs_digest,moved.inputs_digest,scaled.inputs_digest}) == 3
    assert base == measure([F_B,F_A])
    assert base.state is MeasurementState.MEASURED


def test_digest_binds_selection_parameters():
    """Relevant selection parameters (max thickness) are replay inputs.

    Same geometry measured under a different explicit thickness selection is a
    different measurement context — its replay identity must differ.
    """
    def measure(thickness):
        return measure_sheet(sheet_id='modelspace',geometries=[F_A,F_B],
                             calibration=CONFIRMED,drawing_units='mm',
                             max_wall_thickness=thickness).measurements[0]
    thin = measure(250)
    wide = measure(500)
    assert thin.value == wide.value, 'same wall, only the policy differs'
    assert thin.inputs_digest != wide.inputs_digest


# ---------------------------------------------------------------------------
# Item 6 — scale/sheet validation hardening
# ---------------------------------------------------------------------------


def test_calibration_for_another_sheet_refused():
    other = replace(CONFIRMED, sheet_id='paperspace:Layout1')
    out = measure_sheet(sheet_id='modelspace',geometries=[F_A,F_B],
                        calibration=other,drawing_units='mm',max_wall_thickness=250)
    assert not out.measurements
    assert out.exceptions[0].severity is ExceptionSeverity.BLOCKING
    assert 'another sheet' in out.exceptions[0].message


def test_unknown_drawing_units_refused_after_confirmation():
    out = measure_sheet(sheet_id='modelspace',geometries=[F_A,F_B],
                        calibration=CONFIRMED,drawing_units='unknown',
                        max_wall_thickness=250)
    assert not out.measurements
    assert out.exceptions[0].severity is ExceptionSeverity.BLOCKING
    assert 'unsupported drawing unit' in out.exceptions[0].message


def test_geometry_from_another_sheet_refused():
    foreign = replace(F_B, source_handles=(
        replace(F_B.source_handles[0], sheet_ref='paperspace:Layout1'),))
    out = measure_sheet(sheet_id='modelspace',geometries=[F_A,foreign],
                        calibration=CONFIRMED,drawing_units='mm',max_wall_thickness=250)
    assert not out.measurements
    assert out.exceptions[0].severity is ExceptionSeverity.BLOCKING
    assert out.exceptions[0].code == 'ambiguous_sheet'


# ---------------------------------------------------------------------------
# Item 3 — replay digest binds source identity and units, not just geometry
# ---------------------------------------------------------------------------


def test_digest_binds_source_identity_and_units():
    def measure(source_id=None, units='mm'):
        return measure_sheet(sheet_id='modelspace',geometries=[F_A,F_B],
                             calibration=CONFIRMED,drawing_units=units,
                             max_wall_thickness=250,source_id=source_id,
                             source_version=source_id).measurements[0]
    anonymous = measure()
    reuploaded = measure(source_id='sha256:abc123')
    changed_units = measure(units='cm')
    assert len({anonymous.inputs_digest, reuploaded.inputs_digest,
                changed_units.inputs_digest}) == 3
    # Same semantic input replays identically, including the anonymous
    # content-addressed snapshot fallback.
    assert measure() == anonymous
    assert measure(source_id='sha256:abc123') == reuploaded
    # Durable identity derives from the digest: same replay → same id, and a
    # different measurement context (source upload) is a different id.
    assert anonymous.measurement_id == measure().measurement_id
    assert anonymous.measurement_id != reuploaded.measurement_id


# ---------------------------------------------------------------------------
# Item 5 — parser warnings reach the measurement boundary
# ---------------------------------------------------------------------------


def _parsed(geometries=(), warnings=(), units='mm', sha='sha256:deadbeef',
            sheet_ref='modelspace', is_modelspace=True):
    return ParseResult(
        drawing_units=units,
        geometries=geometries,
        sheets=(SheetSummary(
            sheet_ref=sheet_ref, layout_name='Model', entity_count=len(geometries),
            measurable_count=len(geometries), is_modelspace=is_modelspace,
            unit_code=units, measurable=is_modelspace),),
        warnings=warnings,
        source_sha256=sha,
    )


def test_measure_parsed_blocks_on_parser_warnings():
    out = measure_parsed(_parsed(warnings=('unsupported CIRCLE handle=2A: circles',)),
                         sheet_id='modelspace', calibration=CONFIRMED,
                         max_wall_thickness=250)
    assert not out.measurements, 'a sheet with unsupported geometry must never look complete'
    assert all(e.code == 'parse_incomplete' and e.severity is ExceptionSeverity.BLOCKING
               for e in out.exceptions)
    assert any('CIRCLE' in e.message for e in out.exceptions)


def test_measure_parsed_blocks_absent_sheet_and_missing_source_version():
    cases = (
        (_parsed(sheet_ref='paperspace:Layout1'),
         'requested sheet is absent or not modelspace'),
        (_parsed(is_modelspace=False),
         'requested sheet is absent or not modelspace'),
        (replace(_parsed(), source_sha256=''),
         'parsed input has no raw source version'),
    )
    for parsed, why in cases:
        out = measure_parsed(parsed, sheet_id='modelspace', calibration=CONFIRMED,
                             max_wall_thickness=250)
        assert not out.measurements, why
        assert any(why in e.message for e in out.exceptions), why


def test_measure_parsed_clean_run_measures_with_source_bound_replay():
    parsed = _parsed(geometries=(F_A, F_B))
    out = measure_parsed(parsed, sheet_id='modelspace', calibration=CONFIRMED,
                         max_wall_thickness=250)
    assert out.exceptions == ()
    assert out.measurements
    for m in out.measurements:
        # Round 5: a wall with no openings carries an honest MEASURED_ZERO
        # opening-count row; every other row is MEASURED. Both states are
        # legitimate measured outputs (domain-model measurement states).
        assert m.state in (MeasurementState.MEASURED,
                           MeasurementState.MEASURED_ZERO)
        if m.quantity_type.value == 'count':
            assert m.state is MeasurementState.MEASURED_ZERO
            assert m.value == Decimal('0')
        # replay identity is bound to the raw source version, not a label
        assert 'sha256:deadbeef' in m.evidence[0].ref
        assert m.measurement_id  # durable identity exists


# ---------------------------------------------------------------------------
# Item 7 — export exception gate fails CLOSED on malformed severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('severity', ['blocking', 'BLOCKING', 'review', None, 42, ''])
def test_export_gate_refuses_malformed_exception_severity(severity):
    """Malformed severity from an approval context must block export (fail closed).

    The approval context is trust-boundary data; a severity that is not a
    real INFO-level ExceptionSeverity member can never permit an export.
    (Regression for the fail-open allowlist found by the Round 3 audit.)
    """
    from boq.assembly import CatalogueRate, assemble_item
    from exports.csv_export import ExportApproval, csv_bytes, rows_digest

    rate = CatalogueRate(catalogue_item_id='cat-1', code='1', description='d',
                         unit='m', rate_minor=1000)
    row = assemble_item([_m(Decimal('1'))], rate)
    approval = ExportApproval(
        boq_id='b', approval_id='a', rows_digest=rows_digest([row]),
        status=BoqStatus.APPROVED,
        unresolved_exceptions=(ExceptionRecord(
            code='parse_incomplete', severity=severity,  # type: ignore[arg-type]
            message='probe'),),
    )
    with pytest.raises(ValueError, match='unresolved'):
        csv_bytes([row], approval=approval)


def _m(value: Decimal):
    """A minimal evidenced MEASURED length record for export tests."""
    from core.domain.enums import ElementType, MeasurementUnit, QuantityType
    from core.provenance.records import EvidenceLink
    from takeoff.engine import MeasurementRecord

    return MeasurementRecord(
        quantity_type=QuantityType.LENGTH,
        value=value, unit=MeasurementUnit.M,
        rule_id='wall.centerline.length.v1', engine_version='0.3.1',
        inputs_digest='a' * 64, state=MeasurementState.MEASURED,
        element_type=ElementType.WALL,
        evidence=(EvidenceLink(kind='geometry', ref='{"handle": "AA"}'),),
        inputs=('AA', 'AB'), label='W',
    )
