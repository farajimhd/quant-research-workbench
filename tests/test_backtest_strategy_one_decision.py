"""Strategy 1 entry uses only the certified closed candidate and V7 levels."""
import asyncio
from datetime import date
from threading import get_ident

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_decision import (
    candidate_entry_protection, certified_v7_candidate_protection,
)
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_preparation import StrategyOneEntryCursor
from src.backend.fixed_v7_stream import FixedV7Cache
from src.trading_runtime.strategy_one_hod_product import HodContext


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
               bid_int=100_000, ask_int=100_100, close_int=100_000)
    evidence = StrategyOneEntryCursor(
        boundary, "AAA", 1, 30_000, (30_000,) * 4,
        low_boundary_ms, 97_000)
    return StrategyOneDecisionCandidate(row, evidence)


HOD = HodContext(30_100, 100_000, 100_000, False, "")


def test_entry_uses_closed_thirty_second_low_and_original_target():
    result = candidate_entry_protection(
        _candidate(), admitted_v7_levels=tuple(_level(index)
                                                  for index in range(1, 8)),
        hod_context=HOD, tick=.01)
    assert result.state.stop == 9.69
    assert result.state.target == 10.3
    assert result.stop_amendment["boundary_ms"] == 30_000
    assert result.target_amendment["ordinal"] == 3


def test_entry_rejects_stale_quote_and_nonpreceding_low():
    levels = tuple(_level(index) for index in range(1, 8))
    assert candidate_entry_protection(
        _candidate(quote_age_us=1_000_001),
        admitted_v7_levels=levels, hod_context=HOD, tick=.01) is None
    assert candidate_entry_protection(
        _candidate(low_boundary_ms=60_000),
        admitted_v7_levels=levels, hod_context=HOD, tick=.01) is None


def test_late_hod_requires_persisted_gate_and_price_below_prior_high():
    levels = tuple(_level(index) for index in range(1, 8))
    candidate = _candidate()
    candidate.market_row["close_int"] = 111_000
    admitted = HodContext(30_100, 100_000, 120_000, True, "1")
    assert candidate_entry_protection(
        candidate, admitted_v7_levels=levels,
        hod_context=admitted, tick=.01) is not None
    for blocked in (
        HodContext(30_100, 100_000, 120_000, True, ""),
        HodContext(30_100, 100_000, 120_000, True, "not-admitted"),
    ):
        assert candidate_entry_protection(
            candidate, admitted_v7_levels=levels,
            hod_context=blocked, tick=.01) is None
    candidate.market_row["close_int"] = 120_000
    assert candidate_entry_protection(
        candidate, admitted_v7_levels=levels,
        hod_context=admitted, tick=.01) is None


def test_v7_candidate_uses_resident_book_only_at_exact_completed_second():
    cache = object.__new__(FixedV7Cache)
    cache._coverage = {"AAA": {}}
    cache.session = date.fromisoformat(DAY)
    cache._streams = {"AAA": object()}
    cache._last_loaded_second_ms = {"AAA": 30_000}
    cache.strategy_one_levels = lambda ticker, *, as_of: tuple(
        _level(index) for index in range(1, 8))
    assert cache.strategy_one_ready_without_read(
        "AAA", as_of=market_day_boundary(cache.session, 30_100))
    assert not cache.strategy_one_ready_without_read(
        "AAA", as_of=market_day_boundary(cache.session, 31_000))
    opened = asyncio.run(certified_v7_candidate_protection(
        _candidate(), v7_cache=cache, hod_context=HOD, tick=.01))
    assert opened.state.stop == 9.69


def test_v7_candidate_loads_missing_second_off_engine_loop():
    cache = object.__new__(FixedV7Cache)
    cache._coverage = {"AAA": {}}
    cache.session = date.fromisoformat(DAY)
    cache._streams = {}
    cache._last_loaded_second_ms = {}
    engine_thread = get_ident()
    visited = []

    def levels(ticker, *, as_of):
        visited.append(get_ident())
        return tuple(_level(index) for index in range(1, 8))

    cache.strategy_one_levels = levels
    opened = asyncio.run(certified_v7_candidate_protection(
        _candidate(), v7_cache=cache, hod_context=HOD, tick=.01))
    assert opened.state.target == 10.3
    assert len(visited) == 1 and visited[0] != engine_thread
