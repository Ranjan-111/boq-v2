"""T121 — property-based tests (hypothesis): banker's rounding + money.

docs/testing-strategy.md §1: "Rounding: banker's rounding on money/quantity
edges via hypothesis property tests (0.005 boundaries, negative deductions,
markup compounding order)."

The project's ACTUAL rounding helpers are tested by IMPORT (never a
reimplementation of their logic):

  * core.units.geometry_units.round_quantity — the 6-place banker's quantize
    every engine measurement passes through (NUMERIC(18,6) alignment).
  * core.units.money.from_major / multiply_rate / apply_markup — THE pricing
    kernel: integer minor units + ROUND_HALF_EVEN.

Properties (all mathematically TRUE of ROUND_HALF_EVEN):

  1. Sign symmetry: round(x) == -round(-x) — half-even is an odd function
     (ties at .5 go to the even neighbor, and parity is sign-blind). This is
     the "negative deductions" doctrine: a deduction never rounds differently
     than the addition it mirrors.
  2. Idempotence: round(round(x)) == round(x) — re-rounding a rounded value
     is a no-op at every scale the helpers target.
  3. Half-unit bound: |round(x) - x| <= exactly one half of the target unit
     — banker's is a nearest-neighbor rounding, never more.
  4. Two-way split: rounding the half of a 2-place amount can stray from the
     exact half by at most one half of the LAST retained digit, so the two
     parts re-sum to the whole within exactly ONE minor unit — the honest
     statement of split invariance under banker's (documented edge cases:
     0.01 -> 0.00+0.00 parts (tie to even 0), 0.03 -> 0.02+0.02 parts).
  5. Tie behavior (example-pinned): ROUND_HALF_EVEN at the exact .5 boundary
     goes to the EVEN neighbor — the property tests document, not assume, it.

Determinism: the hypothesis profile lives in tests/conftest.py (derandomized
+ fixed max_examples), so CI rounds behave identically on every run.
"""
from __future__ import annotations

from decimal import Decimal

from hypothesis import example, given
from hypothesis import strategies as st

from core.units.geometry_units import round_quantity
from core.units.money import Currency, Money, apply_markup, from_major, multiply_rate

# Quantities: the engine's domain is Decimals with at most 4 fractional
# digits (scale factors and drawing-unit values); 2-place money inputs are
# the from_major domain. Both are covered by the <=4dp strategy below.
AMOUNTS_4DP = st.integers(min_value=-10_000_000, max_value=10_000_000).map(
    lambda units: Decimal(units) / Decimal(10_000)  # exact 4-place decimal
)
NONNEG_AMOUNTS_4DP = st.integers(min_value=0, max_value=10_000_000).map(
    lambda units: Decimal(units) / Decimal(10_000)
)
MINOR_TOTALS = st.integers(min_value=0, max_value=100_000_000)  # minor units
RATES_MINOR = st.integers(min_value=0, max_value=1_000_000)
MARKUP_BP = st.integers(min_value=0, max_value=30_000)  # 0% .. 300%


class TestRoundQuantity:
    """The engine's 6-place banker's quantize (every measurement passes it)."""

    @given(amount=AMOUNTS_4DP)
    def test_sign_symmetry(self, amount: Decimal) -> None:
        """round(x) == -round(-x): half-even is an odd function, so a
        negative deduction always mirrors its positive addition exactly."""
        assert round_quantity(amount) == -round_quantity(-amount)

    @given(amount=AMOUNTS_4DP)
    def test_idempotent(self, amount: Decimal) -> None:
        assert round_quantity(round_quantity(amount)) == round_quantity(amount)

    @given(amount=AMOUNTS_4DP)
    def test_never_strays_more_than_half_a_unit(self, amount: Decimal) -> None:
        quantum = Decimal("0.000001")
        assert abs(round_quantity(amount) - amount) <= quantum / 2

    def test_ties_go_to_the_even_neighbor(self) -> None:
        """The ROUND_HALF_EVEN contract, example-pinned (banker's, not half-up):
        0.0000005 -> 0.000000 (0 is even); 0.0000015 -> 0.000002 (2 is even)."""
        assert round_quantity(Decimal("0.0000005")) == Decimal("0.000000")
        assert round_quantity(Decimal("0.0000015")) == Decimal("0.000002")
        assert round_quantity(Decimal("-0.0000025")) == Decimal("-0.000002")
        assert round_quantity(Decimal("0.0000035")) == Decimal("0.000004")


class TestFromMajor:
    """2-place money parsing with banker's rounding (core.units.money)."""

    @given(amount=AMOUNTS_4DP)
    def test_sign_symmetry(self, amount: Decimal) -> None:
        left = from_major(amount, "INR").amount_minor
        right = from_major(-amount, "INR").amount_minor
        assert left == -right

    @given(amount=AMOUNTS_4DP)
    def test_idempotent(self, amount: Decimal) -> None:
        once = from_major(amount, "INR")
        twice = from_major(once.major(), "INR")
        assert twice == once

    @given(amount=AMOUNTS_4DP)
    def test_never_strays_more_than_half_a_minor_unit(self, amount: Decimal) -> None:
        minor_exact = amount * 100  # exact Decimal
        assert abs(Decimal(from_major(amount, "INR").amount_minor) - minor_exact) <= Decimal("0.5")

    def test_ties_go_to_the_even_neighbor(self) -> None:
        """0.005 -> 0.00 (even 0); 0.015 -> 0.02 (even 2) — banker's at the
        classic 0.005 boundary (never half-up)."""
        assert from_major(Decimal("0.005"), "INR").amount_minor == 0
        assert from_major(Decimal("0.015"), "INR").amount_minor == 2
        assert from_major(Decimal("0.025"), "INR").amount_minor == 2
        assert from_major(Decimal("-0.015"), "INR").amount_minor == -2


class TestTwoWaySplit:
    """Splitting a 2-place total in two equal parts re-sums to the whole
    within EXACTLY ONE minor unit — the honest banker's statement.

    Edge behavior (documented, by design of ROUND_HALF_EVEN):
      total 0.01 -> parts 0.00 + 0.00 (tie at 0.005 goes to even 0)
      total 0.03 -> parts 0.02 + 0.02 (tie at 0.015 goes to even 2)
    Total symmetry does NOT hold for odd minor totals — the parts can
    over- or under-shoot by exactly one minor unit, never more."""

    @given(total_minor=MINOR_TOTALS)
    def test_parts_sum_to_whole_within_one_minor_unit(self, total_minor: int) -> None:
        total = from_major(Decimal(total_minor) / 100, "INR")
        half_major = Decimal(total_minor) / 200  # exact: at most 3 decimal places
        part = from_major(half_major, "INR")
        assert abs(2 * part.amount_minor - total.amount_minor) <= 1

    @example(total_minor=1)  # 0.01: tie 0.005 -> even 0 -> parts sum 0 (off by 1)
    @example(total_minor=2)  # 0.02: exact half 0.01 -> parts sum 2
    @example(total_minor=3)  # 0.03: tie 0.015 -> even 2 -> parts sum 4 (off by 1)
    @given(total_minor=st.integers(min_value=1, max_value=99))
    def test_documented_edge_behavior(self, total_minor: int) -> None:
        total = from_major(Decimal(total_minor) / 100, "INR")
        part = from_major(Decimal(total_minor) / 200, "INR")
        overshoot = 2 * part.amount_minor - total.amount_minor
        assert abs(overshoot) <= 1
        if total_minor % 2 == 0:  # even totals split EXACTLY (no tie)
            assert overshoot == 0


class TestMultiplyRate:
    """THE pricing kernel: line total = quantity x rate, banker-rounded."""

    @given(
        quantity=AMOUNTS_4DP,
        rate_minor=RATES_MINOR,
    )
    def test_sign_symmetry(self, quantity: Decimal, rate_minor: int) -> None:
        left = multiply_rate(quantity, rate_minor, currency="INR").amount_minor
        right = multiply_rate(-quantity, rate_minor, currency="INR").amount_minor
        assert left == -right

    @given(
        quantity=AMOUNTS_4DP,
        rate_minor=RATES_MINOR,
    )
    def test_never_strays_more_than_half_a_minor_unit(
        self, quantity: Decimal, rate_minor: int
    ) -> None:
        exact = quantity * rate_minor  # exact Decimal product
        total = multiply_rate(quantity, rate_minor, currency="INR")
        assert abs(Decimal(total.amount_minor) - exact) <= Decimal("0.5")

    @given(
        units=st.integers(min_value=0, max_value=100_000),
        rate_minor=RATES_MINOR,
    )
    def test_integer_quantities_are_exact(
        self, units: int, rate_minor: int
    ) -> None:
        """An integer quantity x integer rate is an integer product: the
        quantize is a no-op, no rounding ever occurs."""
        total = multiply_rate(Decimal(units), rate_minor, currency="INR")
        assert total.amount_minor == units * rate_minor


class TestApplyMarkup:
    """Markup in basis points, banker-rounded (core.units.money).

    Contract (as boq/assembly.py composes it): apply_markup returns the
    MARKUP AMOUNT, not the marked-up total — callers write
    ``base + apply_markup(base, bp)``. Properties below model exactly that."""

    def test_zero_markup_is_zero_delta(self) -> None:
        base = Money(123_456, Currency("INR"))
        assert apply_markup(base, 0) == Money(0, Currency("INR"))
        assert base + apply_markup(base, 0) == base

    @given(minor=st.integers(min_value=-10_000_000, max_value=10_000_000), bp=MARKUP_BP)
    def test_sign_symmetry(self, minor: int, bp: int) -> None:
        """A negative base marks up to the exact negative of the positive one
        (negative deductions mirror additions)."""
        base = Money(minor, Currency("INR"))
        assert apply_markup(base, bp).amount_minor == -apply_markup(
            Money(-minor, Currency("INR")), bp
        ).amount_minor

    @given(minor=st.integers(min_value=0, max_value=10_000_000), bp=MARKUP_BP)
    def test_never_strays_more_than_half_a_minor_unit(self, minor: int, bp: int) -> None:
        exact = Decimal(minor * bp) / Decimal(10_000)
        marked = apply_markup(Money(minor, Currency("INR")), bp)
        assert abs(Decimal(marked.amount_minor) - exact) <= Decimal("0.5")

    @given(
        minor=st.integers(min_value=0, max_value=10_000_000),
        first=st.integers(min_value=0, max_value=20_000),
        second=st.integers(min_value=0, max_value=20_000),
    )
    def test_compounding_order_never_underquotes_the_direct_markup(
        self, minor: int, first: int, second: int
    ) -> None:
        """Markup compounding order (testing-strategy §1): staging a% then b%
        (compounding explicitly, as boq/assembly does) is never LESS than
        applying (a+b)% directly minus one minor unit of double-rounding
        slack — the estimator never underquotes by staging markups."""
        base = Money(minor, Currency("INR"))
        raised = base + apply_markup(base, first)
        staged = raised + apply_markup(raised, second)
        direct = base + apply_markup(base, first + second)
        assert staged.amount_minor >= direct.amount_minor - 1
