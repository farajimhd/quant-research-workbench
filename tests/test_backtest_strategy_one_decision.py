"""Strategy 1 entry uses only the certified closed candidate and V7 levels."""
from datetime import date

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_decision import candidate_entry_protection
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_preparation import StrategyOneEntryCursor


DAY = "2026-08-18"


def _level(number):
    center = 10 + number * .1
    return {"unified_level_id": str(number), "lower": center - .01,
            "upper": center + .01, "side": "resistance", "role": "resistance"}


def _candidate(*, quote_age_us=100_000, low_boundary_ms=30_000):
    boundary = 30_100
    now_us = int(market_day_boundary(
        date.fromisoformat(DAY), boundary).timestamp() * 1_000_000)
    row = dict(session_date=DAY, ticker="AAA", boundary_ms=boundary,
               resolution_ms=100, price_valid=1, quote_valid=1,
               quote_timestamp_us=now_us - quote_age_us,
               bid_int=100_000, ask_int=100_100)
    evidence = StrategyOneEntryCursor(
        boundary, "AAA", 1, 30_000, (30_000,) * 4,
        low_boundary_ms, 97_000)
    return StrategyOneDecisionCandidate(row, evidence)


def test_entry_uses_closed_thirty_second_low_and_original_target():
    result = candidate_entry_protection(
        _candidate(), admitted_v7_levels=tuple(_level(index)
                                                  for index in range(1, 8)),
        tick=.01)
    assert result.state.stop == 9.69
    assert result.state.target == 10.3
    assert result.stop_amendment["boundary_ms"] == 30_000
    assert result.target_amendment["ordinal"] == 3


def test_entry_rejects_stale_quote_and_nonpreceding_low():
    levels = tuple(_level(index) for index in range(1, 8))
    assert candidate_entry_protection(
        _candidate(quote_age_us=1_000_001),
        admitted_v7_levels=levels, tick=.01) is None
    assert candidate_entry_protection(
        _candidate(low_boundary_ms=60_000),
        admitted_v7_levels=levels, tick=.01) is None
