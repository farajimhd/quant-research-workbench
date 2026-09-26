"""Backtest can only SELECT exact coverage-sealed structural pivots."""
import json

import pytest

from src.backend import backtest_strategy_one_pivot_store as store
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
    market_day_boundary,
)
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, interval_content_hash,
)
from src.trading_runtime.strategy_one_pivot_schema import PRODUCT_DIGEST


DAY = "2026-08-18"
SOURCE = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"
ORIGIN = round(market_day_boundary(DAY, 0).timestamp() * 1_000_000)
INTERVAL = PivotInterval("high", 101_000, ORIGIN + 1_000_000,
                         ORIGIN + 2_000_000, 2_000, None)


def market():
    units = tuple(MarketDayUnit("build", DAY, "ABCD", stage, SOURCE,
                                "source", 100, "hash")
                  for stage in ("bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build",
                                  "definition", (DAY,), ("ABCD",), units,
                                  (100, 1000, 5000, 10000, 30000), "token")


class Client:
    def __init__(self, intervals=(INTERVAL,)):
        self.intervals = intervals
        self.coverage = [{
            "ticker": "ABCD", "derivation_attempt_text": DERIVED,
            "bars_attempt_text": SOURCE, "product_digest": PRODUCT_DIGEST,
            "interval_count": len(intervals),
            "content_hash": interval_content_hash(intervals),
        }]
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        if "FROM arte.strategy_one_pivot_coverage_v1" in query:
            return "\n".join(json.dumps(row) for row in self.coverage)
        if "FROM arte.strategy_one_pivot_interval_v1" in query:
            return "\n".join(json.dumps({
                "side": item.side, "price_int": item.price_int,
                "pivot_at_us": item.pivot_at_us,
                "confirmed_at_us": item.confirmed_at_us,
                "valid_from_boundary_ms": item.valid_from_boundary_ms,
                "valid_to_boundary_ms": item.valid_to_boundary_ms,
            }) for item in self.intervals)
        raise AssertionError(query)


def test_certification_pins_exact_select_only_rows(monkeypatch):
    monkeypatch.setattr(store, "verify_tables", lambda _: None)
    client = Client()
    plan = store.certify_pivot_plan(market(), session_date=DAY,
                                    candidate_tickers=("ABCD",), client=client)
    assert plan.coverage[0].interval_count == 1
    assert plan.intervals == (("ABCD", (INTERVAL,)),)
    assert len(plan.token) == 64
    assert all(query.startswith("SELECT") for query in client.queries)
    assert any("toString(side)" in query for query in client.queries
               if "pivot_interval" in query)


def test_missing_duplicate_or_changed_coverage_blocks_preflight(monkeypatch):
    monkeypatch.setattr(store, "verify_tables", lambda _: None)
    for facts in ([], Client().coverage * 2,
                  [{**Client().coverage[0], "bars_attempt_text": DERIVED}]):
        client = Client()
        client.coverage = facts
        with pytest.raises(RuntimeError, match="coverage"):
            store.certify_pivot_plan(market(), session_date=DAY,
                                      candidate_tickers=("ABCD",), client=client)


def test_future_visibility_or_changed_child_blocks_preflight(monkeypatch):
    monkeypatch.setattr(store, "verify_tables", lambda _: None)
    future = PivotInterval("high", 101_000, ORIGIN + 1_000_000,
                           ORIGIN + 3_000_000, 2_000, None)
    client = Client((future,))
    with pytest.raises(RuntimeError, match="noncausal"):
        store.certify_pivot_plan(market(), session_date=DAY,
                                  candidate_tickers=("ABCD",), client=client)
    client = Client()
    client.intervals = ()
    with pytest.raises(RuntimeError, match="differ"):
        store.certify_pivot_plan(market(), session_date=DAY,
                                  candidate_tickers=("ABCD",), client=client)
