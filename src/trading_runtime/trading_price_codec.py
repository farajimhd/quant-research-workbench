"""Exact shared scalar prices for staged future normalized trading contracts.

Uses the existing broker-match UInt64 binary64 representation. No table, price
rounding, order policy or operational admission is installed by this codec.
Money and quantity contracts remain separate decimal concerns.
"""
from math import isfinite

from .strategy_one_broker_match_snapshot import float64_bits, float64_from_bits


def _price(value, *, allow_zero, nullable):
    if type(allow_zero) is not bool or type(nullable) is not bool:
        raise ValueError("Trading price domain flags must be explicit booleans")
    if value is None:
        if nullable:
            return None
        raise ValueError("Trading price requires a non-null value")
    if type(value) not in (int, float):
        raise ValueError("Trading price requires a native numeric scalar")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("Trading price cannot fit binary64") from exc
    if (not isfinite(number) or number < 0 or not allow_zero and number == 0
            or type(value) is int and number != value):
        raise ValueError("Trading price is non-finite, outside its domain or inexact")
    return number


def encode_trading_price(value, *, allow_zero=False, nullable=False):
    """Return one UInt64 scalar; reject implicit truncation or decimal rounding."""
    number = _price(value, allow_zero=allow_zero, nullable=nullable)
    return None if number is None else float64_bits(number, "trading price")


def decode_trading_price(bits, *, allow_zero=False, nullable=False):
    """Validate the same price domain when restoring its exact native value."""
    if bits is None:
        return _price(None, allow_zero=allow_zero, nullable=nullable)
    number = float64_from_bits(bits, "trading price")
    return _price(number, allow_zero=allow_zero, nullable=nullable)
