"""Persisted V7 geometry still advances BOS from causal certified seconds."""
from __future__ import annotations

from datetime import date
from dataclasses import replace
from uuid import UUID

import pytest

from src.backend import fixed_v7_interval_cache as subject
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
    market_day_boundary,
)
from src.backend.backtest_strategy_one_v7_interval_store import (
    CertifiedV7IntervalPlan, CertifiedV7IntervalUnit,
)
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval


DAY = date(2026, 8, 18)
ATTEMPT = str(UUID(int=1))


class Reader:
    def iter_json_each_row(self, _sql):
        raise AssertionError("source is monkeypatched")


def fixtures():
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition",
        (DAY.isoformat(),), ("TEST",),
        (MarketDayUnit("build", DAY.isoformat(), "TEST", "bars", ATTEMPT,
                       "source", 1, "output"),), (100, 1000), "market-token")
    unit = CertifiedV7IntervalUnit(
        "TEST", ATTEMPT, ATTEMPT, "a" * 64, "b" * 64, "c" * 64,
        "d" * 64, POLICY, 1, 1, "e" * 64, "f" * 64)
    level = V7LevelInterval("L", 0, 0, 57_600_001, 10.0, 10.2,
                            "resistance", "", 1_800_000_000_000, True)
    product = CertifiedV7IntervalPlan(
        "build", DAY.isoformat(), (unit,), (("TEST", (1000,)),),
        (("TEST", (level,)),), "product-token")
    return market, product


def test_persisted_cache_observes_only_completed_price_valid_seconds(monkeypatch):
    market, product = fixtures()
    source = {"ticker": "TEST", "resolution_ms": 1000,
              "bucket_index": 14_400, "price_valid": 1,
              "extremes_valid": 1, "open_int": 100_000,
              "close_int": 101_000}
    monkeypatch.setattr(subject, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: iter((source,)))
    seen = []
    cache = subject.FixedV7IntervalCache(
        market_plan=market, interval_plan=product, session=DAY,
        client=Reader(),
        observe_completed_second=lambda ticker, row, at: seen.append((ticker, at)))
    assert cache.strategy_one_levels(
        "TEST", as_of=market_day_boundary(DAY, 1100))[0]["unified_level_id"] == "L"
    assert seen == [("TEST", 1000)]
    assert cache.last_completed_price_second("TEST")["boundary_ms"] == 1000
    assert cache.strategy_one_ready_without_read(
        "TEST", as_of=market_day_boundary(DAY, 1900))


def test_sealed_clock_disagreement_fails_closed(monkeypatch):
    market, product = fixtures()
    source = {"ticker": "TEST", "resolution_ms": 1000,
              "bucket_index": 14_400, "price_valid": 0,
              "extremes_valid": 0}
    monkeypatch.setattr(subject, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: iter((source,)))
    cache = subject.FixedV7IntervalCache(
        market_plan=market, interval_plan=product, session=DAY,
        client=Reader())
    with pytest.raises(RuntimeError, match="validity clock"):
        cache.strategy_one_levels("TEST", as_of=market_day_boundary(DAY, 1000))


def test_precomputed_entry_facts_load_only_exact_latest_completed_second(monkeypatch):
    market, product = fixtures()
    product = replace(product, valid_seconds=(("TEST", (1000, 5000)),))
    calls = []
    source = {"ticker": "TEST", "resolution_ms": 1000,
              "bucket_index": 14_404, "price_valid": 1,
              "extremes_valid": 1, "open_int": 100_000,
              "close_int": 101_000}
    def latest(*_args, **kwargs):
        calls.append((kwargs["after_boundary_ms"], kwargs["through_boundary_ms"]))
        return iter((source,))
    monkeypatch.setattr(subject, "iter_persisted_v7_seconds", latest)
    cache = subject.FixedV7IntervalCache(
        market_plan=market, interval_plan=product, session=DAY,
        client=Reader(), precomputed_entry_facts=True)
    cache.strategy_one_levels("TEST", as_of=market_day_boundary(DAY, 5100))
    assert calls == [(4000, 5000)]
    assert cache.last_completed_price_second("TEST")["boundary_ms"] == 5000
    # A completed quote-only second must not carry the earlier price forward.
    cache.strategy_one_levels("TEST", as_of=market_day_boundary(DAY, 6100))
    assert calls == [(4000, 5000)]
    assert cache.last_completed_price_second("TEST") is None
