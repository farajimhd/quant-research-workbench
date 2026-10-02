"""Prepared additional failure rule for Strategy39; no execution admission.

The pinned parent exit remains separate and takes precedence. This rule
consumes its existing producer/held-position fields; it never derives a bar,
indicator, quote or held-start clock. Native publication and cold replay are
required before any numbered executor may consume the returned witness.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from .strategy_liquidity_fade_failure import (
    AFTER_HOURS_START_MS, COMPLETED_BAR_MAX_AGE_MS, MAX_QUOTE_AGE_US,
    MAX_TRADE_COUNT, PREMARKET_END_MS, LiquidityFadeFailure, LiquidityFadeInput,
    liquidity_fade_failure,
)

POLICY_ID = 'strategy-thirty-nine-half-risk-liquidity-failure-v1'


@dataclass(frozen=True, slots=True)
class HalfRiskLiquidityFadeFailure(LiquidityFadeFailure):
    """Distinct prepared witness; the parent validator cannot admit this type."""


def half_risk_liquidity_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'price': 'completed_5s_close_and_fresh_bid_at_or_below_half_original_risk',
        'risk_anchor': 'unchanged_original_reference_ask_and_original_initial_stop',
        'momentum': 'completed_5s_macd_line_strictly_below_signal',
        'activity': 'prior10 > 0 and 2 * recent10 <= prior10',
        'observations': 'four_contiguous_completed_5s_bars_wholly_after_native_first_held',
        'decision_clock': 'completed_100ms; latest_completed_5s_age_in_[0,5000)ms',
        'quote_max_age_us': MAX_QUOTE_AGE_US,
        'pending_exit': 'no_duplicate_exit',
        'missing': 'no_synthetic_observations_or_zero_imputation',
        'priority': 'every_inherited_strategy38_exit_first; additional_failure_only',
        'scope': 'held_long_exits_only; entries_sizing_costs_protection_unchanged',
        'session': 'premarket_or_afterhours; native_held_start_in_same_session',
        'time_alone': 'never_exits; no_maximum_holding_age',
    }


def half_risk_liquidity_fade_failure(value: LiquidityFadeInput) -> HalfRiskLiquidityFadeFailure | None:
    """Require failed price/momentum and a twofold activity decline together.

    Original risk, native integer counts and quote age retain the parent's
    type validation. Its returned witness is never relabeled or submitted
    here. This additional predicate can confirm on any completed 100ms clock
    before the next 5s candle, using only the four already completed intervals.
    """
    # Keep the pinned parent's shared input-validation authority. A None
    # result is not sufficient to establish this different predicate: all
    # observation, position and price conditions are checked explicitly below.
    liquidity_fade_failure(value)
    x = value.five_second
    if len(value.candles) != 4 or x.position_quantity == 0 or x.pending_exit:
        return None
    same_window = (x.first_held_boundary_ms < PREMARKET_END_MS
                   and x.boundary_ms <= PREMARKET_END_MS) or (
                       x.first_held_boundary_ms >= AFTER_HOURS_START_MS
                       and x.boundary_ms >= AFTER_HOURS_START_MS)
    if not same_window:
        return None
    completed_at = x.completed_five_second_boundary_ms
    if (type(completed_at) is not int or completed_at % 5000
            or not 0 <= x.boundary_ms - completed_at < COMPLETED_BAR_MAX_AGE_MS
            or completed_at - 20_000 < x.first_held_boundary_ms
            or tuple(c.boundary_ms for c in value.candles)
            != tuple(completed_at - offset for offset in (15_000, 10_000, 5000, 0))):
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
    # Decimal conversion preserves equality at the literal original-risk
    # boundary; integer sums preserve all UInt64 count values without overflow.
    threshold = (Decimal(str(x.reference_ask)) + Decimal(str(x.initial_stop))) / 2
    if (prior == 0 or 2 * recent > prior
            or Decimal(x.completed_five_second_close_int) > threshold * 10_000
            or Decimal(str(x.bid)) > threshold):
        return None
    return HalfRiskLiquidityFadeFailure(
        x.boundary_ms, x.first_held_boundary_ms, x.reference_ask, x.initial_stop,
        completed_at, x.completed_five_second_close_int, float(x.macd_line),
        float(x.macd_signal), float(x.bid), float(x.ask), x.quote_age_us, value.candles,
    )


def numbered_liquidity_fade_failure(value: LiquidityFadeInput, *, strategy_number=35):
    """Replay the pinned number's rule, preserving inherited exit precedence.

    This is a pure prepared-policy selector, not executor registration. Older
    numbers cannot reach the additional predicate; Strategy39 considers it
    only when the unchanged parent liquidity predicate did not produce an exit.
    """
    if type(strategy_number) is not int or strategy_number not in (35,36,37,38,39):
        raise ValueError('Liquidity failure requires an exact supported strategy number')
    inherited = liquidity_fade_failure(value)
    if inherited is not None or strategy_number != 39:
        return inherited
    return half_risk_liquidity_fade_failure(value)
