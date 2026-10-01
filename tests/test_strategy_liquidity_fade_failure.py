"""Candidate semantics and timing; not native admission or financial replay."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_liquidity_fade_failure import (
    LiquidityFadeCandle, LiquidityFadeInput, liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_exit import (
    liquidity_fade_exit_intent, validate_liquidity_fade_witness,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions


def observed_case():
    # PLUG Aug10 AH producer counts and fresh quote at 16:26:47.4 NY.
    # First-held here is a fixture, not a connected native-state attestation.
    x = FollowThroughFailureInput(
        44_807_400, 44_784_100, 2.33, 2.30, 44_805_000, 23_197, True,
        0.004625572833944158, 0.004761743820885122, 2.31, 2.32, 42_652, 100.0, False,
    )
    return LiquidityFadeInput(x, tuple(LiquidityFadeCandle(at, count) for at, count in (
        (44_790_000, 57), (44_795_000, 18), (44_800_000, 8), (44_805_000, 5))))


def test_completed_signal_can_wait_for_a_fresh_quote_without_moving_bar_clock():
    value = observed_case()
    assert liquidity_fade_failure(replace(value, five_second=replace(
        value.five_second, boundary_ms=44_805_000, quote_age_us=2_104_701))) is None
    w = liquidity_fade_failure(value)
    assert w.boundary_ms == 44_807_400
    assert w.completed_five_second_boundary_ms == 44_805_000
    assert tuple(c.trade_count for c in w.candles) == (57, 18, 8, 5)
    validate_liquidity_fade_witness(w)


@pytest.mark.parametrize("changes", [
    {"quote_age_us": 1_000_001}, {"quote_age_us": -1}, {"quote_age_us": None},
    {"bid": 2.34, "ask": 2.35}, {"bid": 2.33, "ask": 2.32},
    {"completed_five_second_close_int": 23_301}, {"price_valid": False},
    {"macd_line": 0.006}, {"macd_line": None}, {"macd_signal": float("nan")},
    {"pending_exit": True}, {"position_quantity": 0},
    {"boundary_ms": 44_810_000}, {"completed_five_second_boundary_ms": 44_810_000},
    {"first_held_boundary_ms": 44_785_100},
])
def test_quote_position_momentum_and_candle_authority_fail_closed(changes):
    value = observed_case()
    assert liquidity_fade_failure(replace(value, five_second=replace(value.five_second, **changes))) is None


def test_four_candles_must_be_complete_contiguous_whole_held_and_not_imputed():
    value = observed_case()
    assert liquidity_fade_failure(replace(value, candles=value.candles[1:])) is None
    assert liquidity_fade_failure(replace(value, candles=tuple(reversed(value.candles)))) is None
    gap = (replace(value.candles[0], boundary_ms=44_785_000), *value.candles[1:])
    assert liquidity_fade_failure(replace(value, candles=gap)) is None
    for bad in (True, -1, 1.5, 1 << 64):
        with pytest.raises(ValueError, match="producer authority"):
            liquidity_fade_failure(replace(value, candles=(replace(value.candles[0], trade_count=bad), *value.candles[1:])))


def test_exact_integer_ratio_zero_prior_and_real_zero_recent_are_distinct():
    value = observed_case()
    counts = lambda xs: tuple(replace(c, trade_count=n) for c, n in zip(value.candles, xs))
    assert liquidity_fade_failure(replace(value, candles=counts((40, 0, 10, 0)))) is not None
    assert liquidity_fade_failure(replace(value, candles=counts((40, 0, 11, 0)))) is None
    assert liquidity_fade_failure(replace(value, candles=counts((0, 0, 0, 0)))) is None
    assert liquidity_fade_failure(replace(value, candles=counts((1, 0, 0, 0)))) is not None
    assert liquidity_fade_failure(replace(value, candles=counts(((1 << 64)-1, (1 << 64)-1, 0, 0)))) is not None


def test_no_time_only_exit_and_late_held_failure_remains_eligible():
    value = observed_case()
    late = replace(value, five_second=replace(value.five_second, first_held_boundary_ms=44_700_000))
    assert liquidity_fade_failure(late) is not None
    assert liquidity_fade_failure(replace(late, candles=tuple(replace(c, trade_count=100) for c in late.candles))) is None


def test_exit_factory_retains_deterministic_financial_and_execution_identity():
    w = liquidity_fade_failure(observed_case())
    financial = StrategyOneFinancialView("assignment", "account", "PLUG", AssignmentStatus.MANAGING,
                                         StrategyPermissions(), 100.0, False, False, False, 1)
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id="325dcc8d-1171-52c5-b944-a92ac772d865")
    intent = liquidity_fade_exit_intent(w, financial, **args)
    assert intent == liquidity_fade_exit_intent(w, financial, **args)
    UUID(intent.intent_id)
    assert intent.event_time.isoformat() == "2026-08-10T20:26:47.400000+00:00"
    assert intent.quantity == 100 and intent.reference_price == 2.31
    assert intent.metadata == {} and intent.execution_policy.quote_source == "qmd"
    assert intent != liquidity_fade_exit_intent(w, replace(financial, ticker="OTHER"), **args)
    # Pending entries retain inherited Portfolio/OMS cancellation authority.
    assert intent == liquidity_fade_exit_intent(w, replace(financial, pending_entry=True), **args)
    for changes in ({"pending_exit": True}, {"position_quantity": 0}):
        with pytest.raises(ValueError, match="financial authority"):
            liquidity_fade_exit_intent(w, replace(financial, **changes), **args)


@pytest.mark.parametrize("decision,held", [(30_007_400, 29_984_100), (44_807_400, 19_784_100)])
def test_regular_hours_and_cross_session_hold_sources_are_excluded(decision, held):
    value = observed_case()
    shift = decision - value.five_second.boundary_ms
    x = replace(value.five_second, boundary_ms=decision, first_held_boundary_ms=held,
                completed_five_second_boundary_ms=value.five_second.completed_five_second_boundary_ms+shift)
    candles = tuple(replace(c, boundary_ms=c.boundary_ms+shift) for c in value.candles)
    assert liquidity_fade_failure(LiquidityFadeInput(x, candles)) is None
