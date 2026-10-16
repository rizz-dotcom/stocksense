"""Exact money handling.

Money is stored as integer *minor units* (e.g. paisa for PKR, cents for USD).
Decimal is used for parsing and display. Binary floats are never authoritative.
Rounding rule: ROUND_HALF_UP to 2 decimal places, applied once at input time.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from stocksense.exceptions import DomainValidationError

MINOR_UNITS = 100
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000000000")  # 1 trillion, generous sanity cap


def parse_money(value, *, field: str = "amount", allow_zero: bool = True) -> Decimal:
    """Convert user input to a validated, rounded Decimal amount."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DomainValidationError(f"{field} is required.")
    if isinstance(value, float):
        value = repr(value)  # avoid binary float artefacts such as 0.1 + 0.2
    if isinstance(value, str):
        value = value.strip().replace(" ", "")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise DomainValidationError(f"{field} must be a number.") from None
    if not amount.is_finite():
        raise DomainValidationError(f"{field} must be a finite number.")
    if amount < 0:
        raise DomainValidationError(f"{field} cannot be negative.")
    if amount == 0 and not allow_zero:
        raise DomainValidationError(f"{field} must be greater than zero.")
    if amount > MAX_AMOUNT:
        raise DomainValidationError(f"{field} is unrealistically large.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def to_minor(amount: Decimal) -> int:
    return int((amount.quantize(CENT, rounding=ROUND_HALF_UP) * MINOR_UNITS).to_integral_value())


def from_minor(minor: int) -> Decimal:
    return (Decimal(int(minor)) / MINOR_UNITS).quantize(CENT)


def money_to_minor(value, *, field: str = "amount", allow_zero: bool = True) -> int:
    return to_minor(parse_money(value, field=field, allow_zero=allow_zero))


def format_money(minor: int | None, currency: str = "PKR") -> str:
    if minor is None:
        return "-"
    return f"{currency} {from_minor(minor):,.2f}"
