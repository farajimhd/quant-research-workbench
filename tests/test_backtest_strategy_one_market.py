"""Certified sparse Strategy 1 market loader keeps source and memory bounds."""
import numpy as np
import pytest

from src.backend import backtest_strategy_one_market as subject
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


DAY = "2026-08-18"
BUILD = "a" * 64
ATTEMPT = "00000000-0000-0000-0000-000000000001"


def prepared(ticker, clocks):
    clocks = np.array(clocks, dtype=np.int64)
    count = len(clocks)
    return PreparedStrategyOneTicker(
        ticker, 1_000, np.arange(count), clocks, clocks,
        np.zeros((count, 4), dtype=np.int64),
        np.zeros(count, dtype=np.int64), np.zeros(count, dtype=np.uint64))


def authority(items):
    tickers = tuple(sorted(item.ticker for item in items))
    units = tuple(MarketDayUnit(BUILD, DAY, ticker, stage, ATTEMPT,
                                "b" * 64, 1_000, "c" * 64)
                  for ticker in tickers
                  for stage in ("bars", "technical", "broker_100ms"))
    market = CertifiedMarketDayPlan(
        ExecutionInterval.parse("100ms"), BUILD, "d" * 64,
        (DAY,), tickers, units, (100, 1_000), "e" * 64)
    prices = PriceLevelPlan(BUILD, tuple(PriceLevelUnit(
        DAY, ticker, ATTEMPT, ATTEMPT, 0, 0, 0., "f" * 64)
        for ticker in tickers), "g" * 64)
    candidates = CertifiedCandidatePlan(
        BUILD, RULE_DIGEST, "h" * 64, (), items, "i" * 64)
    return market, prices, candidates


def test_sparse_loader_merges_concurrent_shards_by_global_boundary(monkeypatch):
    items = (prepared("AAA", (100, 300)), prepared("BBB", (200,)),
             prepared("EMPTY", ()))
    market, prices, candidates = authority(items)
    visited = []
    closed = []

    def rows(scoped, *, candidate_boundaries, price_plan, client):
        visited.append(candidate_boundaries)
        for ticker, clocks in candidate_boundaries.items():
            for boundary in clocks:
                yield {"session_date": DAY, "ticker": ticker,
                       "boundary_ms": boundary, "resolution_ms": 100,
                       "price_valid": 1, "indicator_resolution_ms": 100}

    class Client:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(subject, "iter_candidate_market_rows", rows)
    result = subject.load_sparse_candidate_market(
        market, candidates, price_plan=prices, client_factory=Client,
        max_workers=2)
    assert [(row["boundary_ms"], row["ticker"]) for row in result] == [
        (100, "AAA"), (200, "BBB"), (300, "AAA")]
    assert sum(len(clocks) for shard in visited for clocks in shard.values()) == 3
    assert all("EMPTY" not in shard for shard in visited)
    assert len(closed) == len(visited)


def test_sparse_loader_rejects_budget_and_missing_market_row(monkeypatch):
    items = (prepared("AAA", (100, 200)),)
    market, prices, candidates = authority(items)
    with pytest.raises(RuntimeError, match="memory budget"):
        subject.load_sparse_candidate_market(
            market, candidates, price_plan=prices,
            client_factory=lambda: None, max_rows=1)

    class Client:
        def close(self):
            pass

    monkeypatch.setattr(subject, "iter_candidate_market_rows",
                        lambda *args, **kwargs: iter((
                            {"session_date": DAY, "ticker": "AAA",
                             "boundary_ms": 100},)))
    with pytest.raises(RuntimeError, match="differ from certified"):
        subject.load_sparse_candidate_market(
            market, candidates, price_plan=prices, client_factory=Client)


def test_candidate_market_shards_never_exceed_key_or_ticker_limit():
    items = tuple(prepared(f"T{index:02d}", tuple(range(100, 10_100, 100)))
                  for index in range(12))
    shards = subject.candidate_market_shards(items)
    assert sum(len(clocks) for shard in shards for clocks in shard.values()) == 1200
    assert all(len(shard) <= 8 and sum(map(len, shard.values())) <= 512
               for shard in shards)


def test_sparse_market_join_preserves_certified_closed_bar_evidence():
    item = PreparedStrategyOneTicker(
        "AAA", 1_000, np.array([42]), np.array([31_000]),
        np.array([30_000]), np.array([[31_000, 30_000, 30_000, 30_000]]),
        np.array([30_000]), np.array([99_000]))
    market_row = {"ticker": "AAA", "boundary_ms": 31_000,
                  "resolution_ms": 100, "price_valid": 1,
                  "indicator_resolution_ms": 100}
    joined = subject.attach_sparse_candidate_evidence((market_row,), (item,))
    assert joined[0].market_row is market_row
    assert joined[0].evidence.source_row_index == 42
    assert joined[0].evidence.stop_low_int == 99_000
    assert joined[0].evidence.macd_boundary_ms == (
        31_000, 30_000, 30_000, 30_000)

    with pytest.raises(ValueError, match="exceed certified evidence"):
        subject.attach_sparse_candidate_evidence((market_row, market_row), (item,))
    with pytest.raises(ValueError, match="exceeds candidate market rows"):
        subject.attach_sparse_candidate_evidence((), (item,))
    for wrong in ({**market_row, "boundary_ms": 31_100},
                  {**market_row, "ticker": "BBB"}):
        with pytest.raises(ValueError, match="differs from certified"):
            subject.attach_sparse_candidate_evidence((wrong,), (item,))


def test_sparse_market_join_rejects_future_or_stale_stop_and_macd():
    row = {"ticker": "AAA", "boundary_ms": 31_000,
           "resolution_ms": 100, "price_valid": 1,
           "indicator_resolution_ms": 100}
    for stop, macd in ((60_000, (31_000, 30_000, 30_000, 30_000)),
                       (0, (31_000, 30_000, 30_000, 30_000)),
                       (30_000, (32_000, 30_000, 30_000, 30_000)),
                       (30_000, (29_000, 30_000, 30_000, 30_000))):
        item = PreparedStrategyOneTicker(
            "AAA", 1_000, np.array([0]), np.array([31_000]),
            np.array([30_000]), np.array([macd]),
            np.array([stop]), np.array([99_000]))
        with pytest.raises(ValueError, match="differs from certified"):
            subject.attach_sparse_candidate_evidence((row,), (item,))
