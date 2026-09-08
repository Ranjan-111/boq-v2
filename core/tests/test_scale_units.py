"""Unit tests for scale/units — the 'never guess scale' enforcement."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.domain.enums import MeasurementState, MeasurementUnit, ScaleCalibrationStatus
from core.units.geometry_units import (
    ScaleCalibration,
    ScaleNotConfirmed,
    convert_area,
    convert_length,
    measurement_state_for,
    round_quantity,
)


def confirmed(mm_per_unit: str = "1") -> ScaleCalibration:
    return ScaleCalibration(
        sheet_id="s1",
        status=ScaleCalibrationStatus.CONFIRMED,
        units_per_drawing_unit=Decimal(mm_per_unit),
        method="user_two_point",
        confirmed_by="user-1",
    )


def proposed() -> ScaleCalibration:
    return ScaleCalibration(
        sheet_id="s1",
        status=ScaleCalibrationStatus.PROPOSED,
        units_per_drawing_unit=Decimal("1"),
    )


class TestScaleGate:
    def test_proposed_scale_refuses_to_measure(self):
        with pytest.raises(ScaleNotConfirmed):
            convert_length(Decimal("500"), "mm", proposed(), MeasurementUnit.M)

    def test_unknown_scale_refuses(self):
        unknown = ScaleCalibration("s1", ScaleCalibrationStatus.UNKNOWN, None)
        with pytest.raises(ScaleNotConfirmed):
            convert_length(Decimal("500"), "mm", unknown, MeasurementUnit.M)

    def test_confirmed_length_conversion(self):
        # 5000 drawing units at 1mm/unit = 5000mm = 5m
        assert convert_length(
            Decimal("5000"), "mm", confirmed(), MeasurementUnit.M
        ) == Decimal("5")

    def test_unit_base_conversion(self):
        # 5 drawing meters, 1 unit = 1mm => 5 * 1000 * 1 mm = 5000mm = 5m? No:
        # 5 m-units x 1000 mm/m x 1 = 5000 mm = 5 m
        assert convert_length(
            Decimal("5"), "m", confirmed(), MeasurementUnit.M
        ) == Decimal("5")

    def test_area_applies_scale_squared(self):
        # 1 drawing unit² at 1mm/unit = 1 mm² = 1e-6 m²
        assert convert_area(
            Decimal("1"), "mm", confirmed(), MeasurementUnit.M2
        ) == Decimal("0.000001")

    def test_imperial_base(self):
        # 1 inch-unit at 1:1 => 25.4mm
        assert convert_length(
            Decimal("1"), "in", confirmed(), MeasurementUnit.MM
        ) == Decimal("25.4")


class TestMeasurementStateDerivation:
    def test_zero_becomes_measured_zero(self):
        assert (
            measurement_state_for(Decimal(0), has_evidence=True)
            is MeasurementState.MEASURED_ZERO
        )

    def test_nonzero_measured(self):
        assert (
            measurement_state_for(Decimal("3.2"), has_evidence=True)
            is MeasurementState.MEASURED
        )

    def test_no_evidence_is_blocked_invariant_1(self):
        assert (
            measurement_state_for(Decimal("3.2"), has_evidence=False)
            is MeasurementState.BLOCKED
        )


class TestRounding:
    def test_six_places_bankers(self):
        assert round_quantity(Decimal("0.0000005")) == Decimal("0.000000")
        assert round_quantity(Decimal("0.0000015")) == Decimal("0.000002")
