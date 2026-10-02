"""Semantic types and operand roles for the v3 search grammar.

Storage units do not grant interchangeability: a UTC timestamp and an elapsed
duration are different types even though both happen to be stored in seconds.
"""

from enum import StrEnum


class Kind(StrEnum):
    TIMESTAMP = "absolute_timestamp"
    DURATION = "elapsed_duration"
    PRICE = "absolute_price"
    PRICE_DELTA = "price_difference"
    RETURN = "signed_return"
    FRACTION = "nonnegative_fraction"
    MONEY = "money"
    COUNT = "count"
    SHARES = "shares"
    IDENTITY = "identity"
    BOOLEAN = "boolean"


def arithmetic(operation, left, right):
    """Return a semantic result type; reject unsupported transformations."""
    if operation == "subtract" and left == right == Kind.TIMESTAMP:
        return Kind.DURATION
    if operation == "subtract" and left == right == Kind.PRICE:
        return Kind.PRICE_DELTA
    if (
        operation in ("add", "subtract")
        and left == Kind.TIMESTAMP
        and right == Kind.DURATION
    ):
        return Kind.TIMESTAMP
    if operation == "add" and left == Kind.PRICE and right == Kind.PRICE_DELTA:
        return Kind.PRICE
    if (
        operation in ("add", "subtract")
        and left == right
        and left
        in (
            Kind.DURATION,
            Kind.PRICE_DELTA,
            Kind.MONEY,
            Kind.COUNT,
            Kind.SHARES,
        )
    ):
        return left
    raise ValueError(f"Forbidden semantic arithmetic: {operation}({left}, {right})")


def compare_threshold(left, right):
    """Threshold RHS is a bounded value, never an account register reference."""
    if left != right or left in (Kind.TIMESTAMP, Kind.IDENTITY, Kind.BOOLEAN):
        raise ValueError(f"Invalid threshold roles: {left} versus {right}")
    return Kind.BOOLEAN
