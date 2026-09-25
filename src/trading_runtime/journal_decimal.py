"""Exact scalar wire format for normalized ClickHouse journal measurements.

The trading runtime emits Python floats today. Their shortest decimal spelling
is the logical source value; the journal stores that value as Decimal(38, 18)
and rejects a source that cannot survive that contract losslessly.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import math
from typing import Any


_QUANTUM = Decimal("0.000000000000000001")
_LIMIT = Decimal("100000000000000000000")


def decimal_38_18(value: Any, *, source_float: bool = False,
                  positive: bool = False, nonnegative: bool = False) -> str:
    if positive and nonnegative:
        raise ValueError("Journal decimal sign constraint is ambiguous")
    if source_float:
        if type(value) is not float:
            raise ValueError("Journal source measurement is not a float")
    elif type(value) not in (int, float, str, Decimal):
        raise ValueError("Journal persisted measurement type differs")
    try:
        number = Decimal(str(value))
        with localcontext() as context:
            context.prec = 80
            exact = number.quantize(_QUANTUM)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("Journal measurement cannot fit Decimal(38, 18)") from exc
    if (not number.is_finite() or number != exact or abs(exact) >= _LIMIT
            or positive and exact <= 0 or nonnegative and exact < 0):
        raise ValueError("Journal measurement cannot fit Decimal(38, 18) losslessly")
    return format(exact, ".18f")


def source_float_from_decimal(value: Any, *, positive: bool = False,
                              nonnegative: bool = False) -> float:
    wire = decimal_38_18(value, positive=positive, nonnegative=nonnegative)
    result = float(wire)
    if (not math.isfinite(result)
            or decimal_38_18(result, source_float=True,
                             positive=positive, nonnegative=nonnegative) != wire):
        raise ValueError("Journal decimal cannot restore the source float exactly")
    return result
