"""T085/T100 slice tests — BOQ assembly + CSV export determinism.

Pinned:
  * unit reconciliation refuses mismatched units (never converts silently),
  * quantity = exact sum, money = integer minor units with banker's rounding,
  * invariant 5: every total recomputable from (quantity, rate, markup),
  * CSV output is byte-identical across runs (manifest sha256 meaningful),
  * money/quantity formatting never goes through float.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
from decimal import Decimal

import pytest

from boq.assembly import BoqAssemblyError, BoqItemRow, CatalogueRate, assemble_item, validate_rows
from core.domain.enums import (
    BoqStatus,
    ExceptionSeverity,
    MeasurementState,
    MeasurementUnit,
    QuantityType,
)
from core.provenance.records import EvidenceLink, ExceptionRecord
from exports.csv_export import ExportApproval, csv_bytes, rows_digest, rows_to_csv
from takeoff.engine import MeasurementRecord


def measurement(
    value: Decimal, label: str, unit: MeasurementUnit = MeasurementUnit.M
) -> MeasurementRecord:
    return MeasurementRecord(
        quantity_type=QuantityType.LENGTH,
        value=value,
        unit=unit,
        rule_id="wall.centerline.length.v1",
        engine_version="0.3.0",
        inputs_digest=hashlib.sha256(label.encode()).hexdigest(),
        state=MeasurementState.MEASURED,
        element_type="wall",  # type: ignore[arg-type]  # ElementType in real flow
        evidence=(EvidenceLink(kind="geometry", ref="AA"),),
        inputs=("AA", "AB"),
        label=label,
    )


WALL_RATE = CatalogueRate(
    catalogue_item_id="cat-1",
    code="2.1.1",
    description="Brick wall 230mm thick, in cement mortar",
    unit="m",
    rate_minor=85000,  # ₹850.00 / m
    markup_bp=750,  # 7.5%
)


class TestAssembleItem:
    def test_single_measurement_row(self) -> None:
        row = assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE)
        assert row.quantity == Decimal("12.5")
        assert row.unit == "m"
        # 12.5 x 850.00 = 10,625.00 → 1,062,500 minor base; markup amount =
        # 7.5% x 1,062,500 = 79,687.5 → banker's 79,688; total = 1,142,188
        assert row.total_minor == 1_142_188
        assert row.currency == "INR"
        assert row.measurement_ids == (measurement(Decimal("12.5"), "Wall 1").measurement_id,)

    def test_quantity_sums_across_measurements(self) -> None:
        m1 = measurement(Decimal("10"), "Wall 1")
        m2 = measurement(Decimal("5.25"), "Wall 2")
        row = assemble_item([m1, m2], WALL_RATE)
        assert row.quantity == Decimal("15.25")
        assert row.measurement_ids == (m1.measurement_id, m2.measurement_id)

    def test_unit_mismatch_refused(self) -> None:
        m2 = measurement(Decimal("3"), "Wall 2", unit=MeasurementUnit.M2)
        with pytest.raises(BoqAssemblyError, match="mix units"):
            assemble_item([measurement(Decimal("1"), "Wall 1"), m2], WALL_RATE)

    def test_catalogue_unit_mismatch_refused(self) -> None:
        bad_rate = CatalogueRate(
            catalogue_item_id="cat-2", code="x", description="d", unit="m2", rate_minor=1
        )
        with pytest.raises(BoqAssemblyError, match="does not match"):
            assemble_item([measurement(Decimal("1"), "Wall 1")], bad_rate)

    def test_empty_measurements_refused(self) -> None:
        with pytest.raises(BoqAssemblyError, match="no measurements"):
            assemble_item([], WALL_RATE)

    def test_invariant_5_total_recomputes(self) -> None:
        row = assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE)
        assert row.recompute_total_minor() == row.total_minor


class TestValidateRows:
    def test_valid_rows_pass(self) -> None:
        row = assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE)
        assert validate_rows([row]) == []

    def test_corrupt_total_flagged(self) -> None:
        row = assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE)
        corrupt = dataclasses.replace(row, total_minor=row.total_minor + 1)
        problems = validate_rows([corrupt])
        assert problems and "invariant 5" in problems[0]


class TestCsvExport:
    def test_csv_deterministic_bytes(self) -> None:
        rows = [
            assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE),
            assemble_item([measurement(Decimal("4.75"), "Wall 2")], WALL_RATE),
        ]
        assert rows_to_csv(rows, approval=approved(rows)) == rows_to_csv(
            rows, approval=approved(rows))

    def test_csv_structure_and_totals(self) -> None:
        rows = [assemble_item([measurement(Decimal("12.5"), "Wall 1")], WALL_RATE)]
        text = rows_to_csv(rows, approval=approved(rows))
        r = list(csv.reader(io.StringIO(text)))
        assert r[0] == [
            "sr_no", "code", "description", "unit", "quantity",
            "rate", "markup_bp", "total", "currency", "measurement_refs",
        ]
        assert r[1][0] == "1" and r[1][1] == "2.1.1"
        assert r[1][4] == "12.500000"  # 6dp quantity, NUMERIC(18,6) alignment
        assert r[1][5] == "850.00"
        assert r[1][7] == "11421.88"  # major units, 2dp
        assert r[2][1] == "TOTAL" and r[2][7] == "11421.88"

    def test_no_float_artifacts_in_money(self) -> None:
        # 0.07 m x 850.00 = 59.50 base; markup 4.46; total 63.96 exactly —
        # a float path would show 63.96000000000001-style artifacts.
        rows = [assemble_item([measurement(Decimal("0.07"), "W")], WALL_RATE)]
        text = rows_to_csv(rows, approval=approved(rows))
        assert "850.00" in text
        assert "63.96" in text
        assert "e-" not in text and "0000000000000" not in text


class TestTrustGateRegressions:
    @pytest.mark.parametrize("state", [MeasurementState.NEEDS_REVIEW,
                                      MeasurementState.NOT_MEASURABLE,
                                      MeasurementState.BLOCKED])
    def test_untrusted_measurement_refused(self, state: MeasurementState) -> None:
        m = dataclasses.replace(measurement(Decimal("1"), "W"), state=state)
        with pytest.raises(BoqAssemblyError):
            assemble_item([m], WALL_RATE)

    @pytest.mark.parametrize("evidence", [(), (EvidenceLink(kind="geometry", ref=""),)])
    def test_absent_evidence_refused(self, evidence: tuple[EvidenceLink, ...]) -> None:
        m = dataclasses.replace(measurement(Decimal("1"), "W"), evidence=evidence)
        with pytest.raises(BoqAssemblyError):
            assemble_item([m], WALL_RATE)

    def test_duplicate_measurement_refused(self) -> None:
        m = measurement(Decimal("1"), "W")
        with pytest.raises(BoqAssemblyError):
            assemble_item([m, m], WALL_RATE)

    def test_export_without_approval_refused(self) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        with pytest.raises(ValueError):
            rows_to_csv(rows)



def approved(rows: list[BoqItemRow]) -> ExportApproval:
    return ExportApproval("boq-1", "approval-1", rows_digest(rows), BoqStatus.APPROVED)


class TestExportApproval:
    @pytest.mark.parametrize("status", [BoqStatus.DRAFT, BoqStatus.IN_REVIEW,
                                        BoqStatus.REVIEWED, BoqStatus.STALE_APPROVED])
    def test_unapproved_state_refused(self, status: BoqStatus) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        with pytest.raises(ValueError, match="status"):
            rows_to_csv(rows, approval=dataclasses.replace(approved(rows), status=status))

    @pytest.mark.parametrize("severity", [ExceptionSeverity.BLOCKING, ExceptionSeverity.REVIEW])
    def test_unresolved_exception_refused(self, severity: ExceptionSeverity) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        approval = dataclasses.replace(approved(rows), unresolved_exceptions=(
            ExceptionRecord(code="test", severity=severity, message="unresolved"),))
        with pytest.raises(ValueError, match="unresolved"):
            csv_bytes(rows, approval=approval)

    def test_changed_row_invalidates_approval(self) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        approval = approved(rows)
        changed = [dataclasses.replace(rows[0], description="changed")]
        with pytest.raises(ValueError, match="stale"):
            rows_to_csv(changed, approval=approval)

    @pytest.mark.parametrize("change", [
        {"total_minor": 1}, {"quantity": Decimal("NaN")},
        {"rate_minor": -1}, {"markup_bp": -1}, {"currency": "JPY"},
        {"measurement_ids": ()}, {"measurement_ids": ("label",)},
    ])
    def test_invalid_row_refused_even_with_matching_approval(self, change: dict) -> None:
        row = assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)
        rows = [dataclasses.replace(row, **change)]
        with pytest.raises(ValueError):
            rows_to_csv(rows, approval=approved(rows))

    def test_duplicate_refs_and_mixed_currency_refused(self) -> None:
        row = assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)
        for rows in ([row, row], [row, dataclasses.replace(row, currency="USD")]):
            with pytest.raises(ValueError):
                rows_to_csv(rows, approval=approved(rows))

    def test_reexport_is_byte_identical(self) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        approval = approved(rows)
        assert csv_bytes(rows, approval=approval) == csv_bytes(
            rows, approval=dataclasses.replace(approval, status=BoqStatus.EXPORTED))

    def test_measured_zero_allowed(self) -> None:
        m = dataclasses.replace(measurement(Decimal("0"), "W"),
                                state=MeasurementState.MEASURED_ZERO)
        rows = [assemble_item([m], WALL_RATE)]
        assert validate_rows(rows) == []
        assert "0.000000" in rows_to_csv(rows, approval=approved(rows))


class TestApprovalSnapshotBinding:
    """rows_digest must bind every serialized field AND row/identity order —
    a reordered or field-edited export can never reuse a stale approval."""

    @pytest.mark.parametrize("change", [
        {"code": "9.9.9"},
        {"description": "changed"},
        {"unit": "mm"},
        {"quantity": Decimal("2")},
        {"rate_minor": 2},
        {"markup_bp": 1},
        {"total_minor": 2},
        {"currency": "USD"},
        {"measurement_ids": ("11111111-1111-1111-1111-111111111111",)},
    ])
    def test_any_serialized_field_change_invalidates_approval(self, change: dict) -> None:
        row = assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)
        mutated = dataclasses.replace(row, **change)
        assert rows_digest([mutated]) != rows_digest([row])
        with pytest.raises(ValueError, match="stale"):
            rows_to_csv([mutated], approval=approved([row]))

    def test_row_order_invalidates_approval(self) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "A")], WALL_RATE),
                assemble_item([measurement(Decimal("2"), "B")], WALL_RATE)]
        approval = approved(rows)
        assert rows_digest(rows) != rows_digest(list(reversed(rows)))
        with pytest.raises(ValueError, match="stale"):
            rows_to_csv(list(reversed(rows)), approval=approval)

    def test_identity_order_within_row_invalidates_approval(self) -> None:
        m1 = measurement(Decimal("1"), "A")
        m2 = measurement(Decimal("2"), "B")
        forward = assemble_item([m1, m2], WALL_RATE)
        backward = assemble_item([m2, m1], WALL_RATE)
        # Same economic content, different identity order — both must bind.
        assert forward.quantity == backward.quantity
        assert forward.total_minor == backward.total_minor
        assert rows_digest([forward]) != rows_digest([backward])
        with pytest.raises(ValueError, match="stale"):
            rows_to_csv([backward], approval=approved([forward]))

    def test_info_severity_exception_does_not_block_export(self) -> None:
        rows = [assemble_item([measurement(Decimal("1"), "W")], WALL_RATE)]
        approval = dataclasses.replace(approved(rows), unresolved_exceptions=(
            ExceptionRecord(code="test", severity=ExceptionSeverity.INFO, message="note"),))
        assert "2.1.1" in rows_to_csv(rows, approval=approval)


@pytest.mark.parametrize("identity", ["", "Wall 1", "00000000"])
def test_missing_or_truncated_measurement_identity_refused(identity: str) -> None:
    @dataclasses.dataclass(frozen=True)
    class Candidate:
        measurement_id: str = identity
        value: Decimal = Decimal("1")
        unit: MeasurementUnit = MeasurementUnit.M
        state: MeasurementState = MeasurementState.MEASURED
        evidence: tuple[EvidenceLink, ...] = (EvidenceLink(kind="geometry", ref="AA"),)
        label: str | None = "Wall 1"

    with pytest.raises(BoqAssemblyError, match="identity"):
        assemble_item([Candidate()], WALL_RATE)


@pytest.mark.parametrize("value,state", [
    (Decimal("NaN"), MeasurementState.MEASURED),
    (Decimal("Infinity"), MeasurementState.MEASURED),
    (Decimal("-1"), MeasurementState.MEASURED),
    (Decimal("0"), MeasurementState.MEASURED),
    (Decimal("1"), MeasurementState.MEASURED_ZERO),
])
def test_invalid_measurement_value_or_state_refused(
    value: Decimal, state: MeasurementState
) -> None:
    m = dataclasses.replace(measurement(value, "W"), state=state)
    with pytest.raises(BoqAssemblyError):
        assemble_item([m], WALL_RATE)
