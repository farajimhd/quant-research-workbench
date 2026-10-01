"""Unpublished Strategy 35 candidate: price failure after completed activity fades.

Producer bars and quotes are inputs, never inferred or fetched here. Native
source publication, first-held/pending-state binding and installation remain
separate gates. This pure rule grants no runtime or broker admission.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from .strategy_followthrough_failure import (
    FollowThroughFailureInput, followthrough_failure,
)

POLICY_ID = "strategy-thirty-five-completed-liquidity-fade-v1"
COMPLETED_BAR_MAX_AGE_MS = 5_000
MAX_QUOTE_AGE_US = 1_000_000
MAX_TRADE_COUNT = (1 << 64) - 1
PREMARKET_END_MS = 19_800_000
AFTER_HOURS_START_MS = 43_200_000


@dataclass(frozen=True, slots=True)
class LiquidityFadeCandle:
    """Native completed 5s trade count; zero differs from missing evidence."""
    boundary_ms: int
    trade_count: int


@dataclass(frozen=True, slots=True)
class LiquidityFadeInput:
    five_second: FollowThroughFailureInput
    candles: tuple[LiquidityFadeCandle, ...]


@dataclass(frozen=True, slots=True)
class LiquidityFadeFailure:
    """Complete scalar witness including distinct observation and exit clocks."""
    boundary_ms: int
    first_held_boundary_ms: int
    reference_ask: float
    initial_stop: float
    completed_five_second_boundary_ms: int
    completed_close_int: int
    macd_line: float
    macd_signal: float
    bid: float
    ask: float
    quote_age_us: int
    candles: tuple[LiquidityFadeCandle, ...]


def liquidity_fade_policy_payload() -> dict:
    return {
        "policy_id": POLICY_ID,
        "price": "completed_5s_close_and_fresh_bid_at_or_below_original_reference_ask",
        "momentum": "completed_5s_macd_line_below_signal",
        "activity": "4 * recent_completed_10s_trade_count <= prior_completed_10s_trade_count",
        "prior_activity": "strictly_positive",
        "observations": "four_contiguous_completed_5s_bars_wholly_after_native_first_held",
        "decision_clock": "100ms; latest_completed_5s_observation_age_in_[0,5000)ms",
        "quote_max_age_us": MAX_QUOTE_AGE_US,
        "pending_exit": "no_duplicate_exit",
        "missing": "no_synthetic_observations_or_zero_imputation",
        "time_alone": "never_exits",
        "priority": "inherited_strategy34_exits_first",
        "session": "premarket_or_afterhours; original_held_start_in_same_session_window",
    }


def liquidity_fade_failure(value: LiquidityFadeInput) -> LiquidityFadeFailure | None:
    """Require failed price/momentum plus a fourfold drop in trade activity.

    The four observations cover disjoint, completed 5s intervals. A later
    fresh quote can confirm their signal until the next 5s observation is due;
    stale quotes cannot be carried into an executable witness. Integer sums
    preserve the exact ratio boundary without division or volume substitutes.
    """
    if type(value) is not LiquidityFadeInput:
        raise ValueError("Liquidity fade requires its exact typed input")
    x = value.five_second
    # Preserve the existing original-risk/position type checks, without
    # changing its predicate or treating a returned older witness as ours.
    followthrough_failure(x)
    if type(value.candles) is not tuple or len(value.candles) > 4:
        raise ValueError("Liquidity fade requires at most four exact native candles")
    for candle in value.candles:
        if (type(candle) is not LiquidityFadeCandle
                or type(candle.boundary_ms) is not int
                or not 0 < candle.boundary_ms <= 57_600_000
                or candle.boundary_ms % 5_000
                or type(candle.trade_count) is not int
                or not 0 <= candle.trade_count <= MAX_TRADE_COUNT):
            raise ValueError("Liquidity fade candle lacks exact producer authority")
    if len(value.candles) != 4 or x.position_quantity == 0 or x.pending_exit:
        return None
    same_window = (x.first_held_boundary_ms < PREMARKET_END_MS
                   and x.boundary_ms <= PREMARKET_END_MS) or (
                       x.first_held_boundary_ms >= AFTER_HOURS_START_MS
                       and x.boundary_ms >= AFTER_HOURS_START_MS)
    if not same_window:
        return None
    completed_at = x.completed_five_second_boundary_ms
    if (type(completed_at) is not int or completed_at % 5_000
            or not 0 <= x.boundary_ms - completed_at < COMPLETED_BAR_MAX_AGE_MS
            or completed_at - 20_000 < x.first_held_boundary_ms
            or tuple(c.boundary_ms for c in value.candles)
            != tuple(completed_at - offset for offset in (15_000, 10_000, 5_000, 0))):
        return None
    if (not x.price_valid
            or type(x.completed_five_second_close_int) is not int
            or not 0 < x.completed_five_second_close_int <= MAX_TRADE_COUNT
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (x.macd_line, x.macd_signal, x.bid, x.ask))
            or not 0 < x.bid <= x.ask
            or type(x.quote_age_us) is not int
            or not 0 <= x.quote_age_us <= MAX_QUOTE_AGE_US
            or x.macd_line >= x.macd_signal):
        return None
    prior = value.candles[0].trade_count + value.candles[1].trade_count
    recent = value.candles[2].trade_count + value.candles[3].trade_count
    if (prior == 0 or 4 * recent > prior
            or Decimal(x.completed_five_second_close_int) > Decimal(str(x.reference_ask)) * 10_000
            or Decimal(str(x.bid)) > Decimal(str(x.reference_ask))):
        return None
    return LiquidityFadeFailure(
        x.boundary_ms, x.first_held_boundary_ms, x.reference_ask, x.initial_stop,
        completed_at, x.completed_five_second_close_int, float(x.macd_line),
        float(x.macd_signal), float(x.bid), float(x.ask), x.quote_age_us, value.candles,
    )
