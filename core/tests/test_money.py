"""Unit tests for money/quantity arithmetic — the pricing kernel invariants."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.units.money import (
    Money,
    apply_markup,
    from_major,
    multiply_rate,
)


class TestMoney:
    def test_minor_units_exact(self):
        m = from_major("10.01", "INR")
        assert m.amount_minor == 1001

    def test_add_same_currency(self):
        assert from_major("1.10", "INR") + from_major("2.05", "INR") == from_major(
            "3.15", "INR"
        )

    def test_currency_mismatch_rejected(self):
        with pytest.raises(ValueError):
            from_major("1.00", "INR") + from_major("1.00", "USD")

    def test_non_integer_minor_rejected(self):
        with pytest.raises(TypeError):
            Money(10.5, "INR")  # type: ignore[arg-type]

    def test_bad_currency_rejected(self):
        with pytest.raises(ValueError):
            Money(100, "inr")
        with pytest.raises(ValueError):
            Money(100, "XX")


class TestPricingKernel:
    def test_quantity_times_rate_is_exact(self):
        # 12.5 m2 x 450.00 INR/m2 = 5625.00
        total = multiply_rate(Decimal("12.5"), 45000, currency="INR")
        assert total == from_major("5625.00", "INR")

    def test_bankers_rounding_on_half(self):
        # 0.125 x 0.04 (minor 4) = 0.005 -> banker's rounds to 0 (even)
        total = multiply_rate(Decimal("0.125"), 4, currency="INR")
        assert total.amount_minor == 0
        # 0.375 x 0.04 = 0.015 -> rounds to 0.02 (even)
        total = multiply_rate(Decimal("0.375"), 4, currency="INR")
        assert total.amount_minor == 2

    def test_markup_bp(self):
        base = from_major("1000.00", "INR")
        m = apply_markup(base, 750)  # 7.5%
        assert m == from_major("75.00", "INR")

    def test_markup_compounding_is_explicit_not_implicit(self):
        base = from_major("1000.00", "INR")
        first = apply_markup(base, 750)
        on_top = apply_markup(base + first, 750)  # cumulative must be explicit
        assert on_top == from_major("86.0625", "INR").__class__(
            int((Decimal(107500) * Decimal(750) / Decimal(10000)).quantize(Decimal(1))),
            "INR",
        )

    def test_negative_markup_rejected(self):
        with pytest.raises(ValueError):
            apply_markup(from_major("1", "INR"), -100)

    def test_money_int_multiply(self):
        assert from_major("2.00", "INR") * 3 == from_major("6.00", "INR")
