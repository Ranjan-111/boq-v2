"""Money and quantity value objects — integer minor units, banker's rounding.

docs/domain-model.md: 'All monetary/quantity values stored as integers with
explicit scale or NUMERIC(18,6) — never floats.'

Money is always (amount_minor: int, currency: str).
Quantities are Decimal at the domain layer; DB stores NUMERIC(18,6).
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import NewType

getcontext().prec = 28  # headroom for compounding markups

Currency = NewType("Currency", str)  # ISO 4217


class Money:
    """An integer-minor-unit monetary amount. Immutable."""

    __slots__ = ("_amount_minor", "_currency")

    def __init__(self, amount_minor: int, currency: Currency) -> None:
        if not isinstance(amount_minor, int):
            raise TypeError(f"amount_minor must be int, got {type(amount_minor).__name__}")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"currency must be ISO 4217, got {currency!r}")
        if currency != currency.upper():
            raise ValueError(f"currency must be uppercase, got {currency!r}")
        self._amount_minor = amount_minor
        self._currency = currency

    @property
    def amount_minor(self) -> int:
        return self._amount_minor

    @property
    def currency(self) -> Currency:
        return self._currency

    def major(self) -> Decimal:
        return Decimal(self._amount_minor) / Decimal(100)

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self._amount_minor + other._amount_minor, self._currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self._amount_minor - other._amount_minor, self._currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int):
            raise TypeError("money * non-integer is ambiguous; use multiply_rate")
        return Money(self._amount_minor * factor, self._currency)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Money)
            and self._amount_minor == other._amount_minor
            and self._currency == other._currency
        )

    def __hash__(self) -> int:
        return hash((self._amount_minor, self._currency))

    def __repr__(self) -> str:
        return f"Money({self._amount_minor}, {self._currency!r})"

    def _require_same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other._currency != self._currency:
            raise ValueError(f"currency mismatch: {self._currency} vs {other._currency}")


def from_major(amount: Decimal | int | str, currency: str) -> Money:
    """Parse a major-unit amount into Money with banker's rounding (2dp)."""
    d = Decimal(str(amount))
    minor = int(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) * 100)
    return Money(minor, Currency(currency))


def multiply_rate(quantity: Decimal, rate_minor: int, *, currency: str) -> Money:
    """line total = quantity x rate, banker-rounded to minor units.

    This is THE pricing kernel — deterministic and float-free.
    """
    q = Decimal(str(quantity))
    r = Decimal(rate_minor)
    minor = int((q * r).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    return Money(minor, Currency(currency))


def apply_markup(base: Money, pct_bp: int) -> Money:
    """Apply a percentage markup given in basis points (bp), banker-rounded.

    pct_bp=750 == 7.5%. Integer bp keeps markup definitions exact.
    """
    if pct_bp < 0:
        raise ValueError("markup bp cannot be negative")
    scaled = base.amount_minor * pct_bp
    minor = int(
        (Decimal(scaled) / Decimal(10_000)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    )
    return Money(minor, base.currency)
