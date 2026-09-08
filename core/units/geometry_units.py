"""Unit conversions — drawing units → project units, exactly.

docs/architecture.md: 'never silently guess scale, units, dimensions'.
Every conversion here is exact Decimal arithmetic; nothing rounds silently.
Scale application is a pure function of (value, calibration) and refuses
unconfirmed calibrations (raising, not guessing — the engine layer turns
that into a BLOCKING exception).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from core.domain.enums import (
    MeasurementState,
    MeasurementUnit,
    ScaleCalibrationStatus,
)


class ScaleNotConfirmed(RuntimeError):
    """Raised when a measurement is attempted on an unconfirmed scale.

    The takeoff engine catches this and emits exception SCALE_UNCONFIRMED
    (BLOCKING) instead of ever guessing.
    """


@dataclass(frozen=True, slots=True)
class ScaleCalibration:
    """docs/domain-model.md — ScaleCalibration.

    units_per_drawing_unit: e.g. millimeters per drawing unit (1:100 drawing
    with mm base -> one drawing unit is 1 mm at paper scale; we store the
    physical ratio the user confirmed).
    """

    sheet_id: str
    status: ScaleCalibrationStatus
    units_per_drawing_unit: Decimal | None
    method: str | None = None
    confirmed_by: str | None = None

    def require_confirmed(self) -> Decimal:
        if self.status is not ScaleCalibrationStatus.CONFIRMED:
            raise ScaleNotConfirmed(
                f"sheet {self.sheet_id}: scale is {self.status.value}, not confirmed"
            )
        assert self.units_per_drawing_unit is not None  # confirmed implies set
        return self.units_per_drawing_unit


_DRAWING_TO_MM: dict[str, Decimal] = {
    # DXF $INSUNITS values we normalize at ingestion; meter/millimeter bases.
    "mm": Decimal(1),
    "cm": Decimal(10),
    "m": Decimal(1000),
    "in": Decimal("25.4"),
    "ft": Decimal("304.8"),
}

_MM_TO_TARGET_DIVISOR: dict[MeasurementUnit, Decimal] = {
    MeasurementUnit.MM: Decimal(1),
    MeasurementUnit.M: Decimal(1000),
    MeasurementUnit.M2: None,  # area — handled by square
    MeasurementUnit.M3: None,
    MeasurementUnit.COUNT: Decimal(1),
}


def convert_length(
    value: Decimal, drawing_unit: str, calibration: ScaleCalibration,
    target: MeasurementUnit,
) -> Decimal:
    """Convert a drawing-unit length to a project unit via confirmed scale."""
    factor = calibration.require_confirmed()
    per_mm = _DRAWING_TO_MM[drawing_unit]
    physical_mm = value * per_mm * factor
    divisor = _MM_TO_TARGET_DIVISOR[target]
    if divisor is None:
        raise ValueError(f"{target} is not a length unit")
    return physical_mm / divisor


def convert_area(
    value: Decimal, drawing_unit: str, calibration: ScaleCalibration,
    target: MeasurementUnit,
) -> Decimal:
    """Convert a drawing-unit² area; scale factor applies squared."""
    if target is not MeasurementUnit.M2:
        raise ValueError(f"{target} is not an area unit")
    factor = calibration.require_confirmed()
    per_mm = _DRAWING_TO_MM[drawing_unit]
    mm2 = value * (per_mm * factor) ** 2
    return mm2 / Decimal(1_000_000)  # mm² -> m²


def measurement_state_for(value: Decimal, has_evidence: bool) -> MeasurementState:
    """Derive the measurement state after a successful deterministic compute."""
    if not has_evidence:
        return MeasurementState.BLOCKED  # invariant 1
    if value == 0:
        return MeasurementState.MEASURED_ZERO
    return MeasurementState.MEASURED


def round_quantity(value: Decimal, places: int = 6) -> Decimal:
    """Standard quantity rounding (banker's) — NUMERIC(18,6) alignment."""
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)
