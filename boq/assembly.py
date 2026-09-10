"""BOQ assembly (T085 vertical slice) — measurement → priced BoqItem.

Pure module: measurements + rate → BoqItem rows. No DB, no AI, no clock.
The full BOQ (sections, manual lines, PC sums, diff-on-change) is Round D;
this is the vertical-slice path: run measurements → draft BOQ rows → CSV.

Contract sources: docs/domain-model.md §Boq/BoqSection/BoqItem,
core.units.money (integer minor units, banker's rounding), and the rule that
every BoqItem total is recomputable from (quantity, rate, markup) — invariant 5.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Protocol
from uuid import UUID

from core.domain.enums import MeasurementState, MeasurementUnit
from core.provenance.records import EvidenceLink
from core.units.money import apply_markup, multiply_rate


class AssemblyMeasurement(Protocol):
    """Read-only measurement contract; BOQ does not depend on the takeoff engine."""

    @property
    def measurement_id(self) -> str: ...
    @property
    def value(self) -> Decimal: ...
    @property
    def unit(self) -> MeasurementUnit: ...
    @property
    def state(self) -> MeasurementState: ...
    @property
    def evidence(self) -> tuple[EvidenceLink, ...]: ...
    @property
    def label(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class CatalogueRate:
    """The rate applied to a mapped measurement (price provenance seed)."""

    catalogue_item_id: str
    code: str  # e.g. CPWD-style code
    description: str
    unit: str  # must reconcile with the measurement unit (validated below)
    rate_minor: int  # integer minor units per measurement unit
    markup_bp: int = 0  # basis points; 750 == 7.5%


@dataclass(frozen=True, slots=True)
class BoqItemRow:
    """One priced BOQ line — invariant 5: total recomputable from inputs."""

    catalogue_item_id: str
    code: str
    description: str
    quantity: Decimal
    unit: str
    rate_minor: int
    markup_bp: int
    total_minor: int
    currency: str
    measurement_ids: tuple[str, ...]  # provenance: which measurements fed this row
    element_label: str | None = None

    def recompute_total_minor(self) -> int:
        """Invariant 5 check: recompute from (quantity, rate, markup) —
        must equal total_minor or the row is corrupt."""
        base = multiply_rate(self.quantity, self.rate_minor, currency=self.currency)
        if self.markup_bp:
            return (base + apply_markup(base, self.markup_bp)).amount_minor
        return base.amount_minor


class BoqAssemblyError(ValueError):
    """Unit mismatch or empty-input — assembly refuses rather than guess."""


def assemble_item(
    measurements: Sequence[AssemblyMeasurement],
    rate: CatalogueRate,
    *,
    currency: str = "INR",
) -> BoqItemRow:
    """One BOQ row from >=1 measurements sharing a catalogue item.

    quantity = sum of the mapped measurements' values (must share the unit).
    Unit reconciliation (docs/domain-model.md): the catalogue item's unit must
    equal the measurements' unit — a mismatch is a refused assembly, never a
    silent conversion.
    """
    if not measurements:
        raise BoqAssemblyError("no measurements to map")
    ids: list[str] = []
    for m in measurements:
        if m.state not in (MeasurementState.MEASURED, MeasurementState.MEASURED_ZERO):
            raise BoqAssemblyError("measurement is not authoritative")
        if not m.evidence or any(not e.kind.strip() or not e.ref.strip() for e in m.evidence):
            raise BoqAssemblyError("measurement requires actual evidence")
        if not m.value.is_finite() or m.value < 0:
            raise BoqAssemblyError("measurement value must be finite and non-negative")
        if (m.value == 0) != (m.state == MeasurementState.MEASURED_ZERO):
            raise BoqAssemblyError("measurement state does not match its value")
        identity = getattr(m, "measurement_id", "")
        try:
            parsed_id = UUID(identity)
            if str(parsed_id) != identity or parsed_id.int == 0:
                raise ValueError("measurement identity must be a nonzero canonical UUID")
        except (ValueError, TypeError, AttributeError) as exc:
            raise BoqAssemblyError("measurement requires a durable UUID identity") from exc
        if identity in ids:
            raise BoqAssemblyError("duplicate measurement identity")
        ids.append(identity)
    if currency not in {"INR", "USD", "EUR", "GBP"}:
        raise BoqAssemblyError("unsupported two-decimal currency")
    if type(rate.rate_minor) is not int or rate.rate_minor < 0:
        raise BoqAssemblyError("rate must be non-negative integer minor units")
    if type(rate.markup_bp) is not int or rate.markup_bp < 0:
        raise BoqAssemblyError("markup must be non-negative integer basis points")
    if not rate.catalogue_item_id.strip():
        raise BoqAssemblyError("missing catalogue identity")
    units = {m.unit.value for m in measurements}
    if len(units) != 1:
        raise BoqAssemblyError(f"cannot mix units in one BOQ row: {sorted(units)}")
    if units.pop() != rate.unit:
        raise BoqAssemblyError(
            f"catalogue unit {rate.unit!r} does not match measurement unit — refuse"
        )
    quantity = sum((m.value for m in measurements), Decimal(0))
    base = multiply_rate(quantity, rate.rate_minor, currency=currency)
    # apply_markup returns the markup AMOUNT (core.units.money contract);
    # a marked-up total is base + markup, compounding explicit at the caller.
    total = base + apply_markup(base, rate.markup_bp) if rate.markup_bp else base
    return BoqItemRow(
        catalogue_item_id=rate.catalogue_item_id,
        code=rate.code,
        description=rate.description,
        quantity=quantity,
        unit=rate.unit,
        rate_minor=rate.rate_minor,
        markup_bp=rate.markup_bp,
        total_minor=total.amount_minor,
        currency=currency,
        measurement_ids=tuple(ids),
        element_label=measurements[0].label,
    )


def validate_rows(rows: Sequence[BoqItemRow]) -> list[str]:
    """Check priced rows before approval; zero measured quantities are valid."""
    problems: list[str] = []
    seen: set[str] = set()
    if len({row.currency for row in rows}) > 1:
        problems.append("mixed currencies")
    for row in rows:
        if row.currency not in {"INR", "USD", "EUR", "GBP"}:
            problems.append(f"{row.code}: unsupported two-decimal currency")
        if not row.quantity.is_finite() or row.quantity < 0:
            problems.append(f"{row.code}: invalid quantity")
        for value in (row.rate_minor, row.markup_bp, row.total_minor):
            if type(value) is not int or value < 0:
                problems.append(f"{row.code}: invalid integer pricing")
        if not row.measurement_ids:
            problems.append(f"{row.code}: missing measurement identity")
        for identity in row.measurement_ids:
            try:
                parsed_id = UUID(identity)
                if str(parsed_id) != identity or parsed_id.int == 0:
                    raise ValueError("measurement identity must be a nonzero canonical UUID")
            except (ValueError, TypeError, AttributeError):
                problems.append(f"{row.code}: invalid measurement identity")
            if identity in seen:
                problems.append(f"{row.code}: duplicate measurement identity")
            seen.add(identity)
        try:
            if row.recompute_total_minor() != row.total_minor:
                problems.append(f"{row.code}: total not recomputable (invariant 5)")
        except (ValueError, TypeError, DecimalException):
            problems.append(f"{row.code}: invalid pricing inputs")
    return problems
