"""Research-only AH failure confirmation; no installed release or order authority.

The proposed exit consumes existing producer candles and fresh quote evidence.
Its separate witness retains the additional 10s observation rather than
pretending that the existing 5s failure witness certifies both timeframes.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from .strategy_followthrough_failure import (
    FollowThroughFailure, FollowThroughFailureInput, followthrough_failure,
)


@dataclass(frozen=True, slots=True)
class ConfirmedAhRiskFailureInput:
    five_second: FollowThroughFailureInput
    completed_ten_second_boundary_ms: int | None
    ten_second_price_valid: bool
    ten_second_macd_line: float | None
    ten_second_macd_signal: float | None


@dataclass(frozen=True, slots=True)
class ConfirmedAhRiskFailure:
    five_second: FollowThroughFailure
    completed_ten_second_boundary_ms: int
    ten_second_macd_line: float
    ten_second_macd_signal: float


def confirmed_ah_risk_failure(
    value: ConfirmedAhRiskFailureInput,
) -> ConfirmedAhRiskFailure | None:
    """First-minute AH quarter-risk loss with bearish completed 5s/10s MACD.

    Both candles must lie wholly after native first-held authority. Fresh bid
    and 5s close must independently cross the original-risk threshold. This
    pure extra rule changes no existing failure/profit policy; a future runner
    must give inherited exits priority and persist this complete new witness.
    """
    if type(value) is not ConfirmedAhRiskFailureInput:
        raise ValueError("AH confirmation requires its exact typed input")
    x = value.five_second
    # Reuse the original input-authority checks without changing its ask/stop.
    followthrough_failure(x)
    if type(value.ten_second_price_valid) is not bool:
        raise ValueError("AH confirmation requires exact producer validity")
    if (x.position_quantity == 0 or x.pending_exit
            or x.first_held_boundary_ms < 43_200_000
            or x.boundary_ms - x.first_held_boundary_ms > 60_000
            or x.boundary_ms % 5000):
        return None
    ten_at = value.completed_ten_second_boundary_ms
    if (type(ten_at) is not int or ten_at % 10_000
            or not 0 <= x.boundary_ms - ten_at < 10_000
            or ten_at - 10_000 < x.first_held_boundary_ms
            or not value.ten_second_price_valid
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (value.ten_second_macd_line, value.ten_second_macd_signal))
            or value.ten_second_macd_line >= value.ten_second_macd_signal):
        return None
    if (x.completed_five_second_boundary_ms != x.boundary_ms
            or x.boundary_ms - 5000 < x.first_held_boundary_ms
            or not x.price_valid
            or type(x.completed_five_second_close_int) is not int
            or x.completed_five_second_close_int <= 0
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (x.macd_line, x.macd_signal, x.bid, x.ask))
            or not 0 < x.bid <= x.ask
            or type(x.quote_age_us) is not int
            or not 0 <= x.quote_age_us <= 1_000_000
            or x.macd_line >= x.macd_signal):
        return None
    threshold = (3 * Decimal(str(x.reference_ask)) + Decimal(str(x.initial_stop))) / 4
    if (Decimal(x.completed_five_second_close_int) > threshold * 10_000
            or Decimal(str(x.bid)) > threshold):
        return None
    five = FollowThroughFailure(
        x.boundary_ms, x.first_held_boundary_ms, x.reference_ask, x.initial_stop,
        x.completed_five_second_close_int, float(x.macd_line), float(x.macd_signal),
        float(x.bid), float(x.ask), x.quote_age_us,
    )
    return ConfirmedAhRiskFailure(
        five, ten_at, float(value.ten_second_macd_line), float(value.ten_second_macd_signal),
    )
