"""Activation prices are exact, pinned, completed 100ms bar reads."""
import json

import numpy as np
import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
    SESSION_OPEN_OFFSET_MS,
)
from src.backend.backtest_strategy_one_activation import load_strategy_one_activations
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


DAY = "2026-08-18"
ATTEMPT = "00000000-0000-0000-0000-000000000001"


def plans():
    units = tuple(MarketDayUnit("build", DAY, "ABCD", stage, ATTEMPT,
                                "source", 100, "hash")
                  for stage in ("bars", "technical", "broker_100ms"))
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build",
                                    "definition", (DAY,), ("ABCD",), units,
                                    (100, 1000, 5000, 10000, 30000), "token")
    prepared = PreparedStrategyOneTicker(
        "ABCD", 100, np.array([1, 2]), np.array([2_000, 2_100]),
        np.array([1_000, 1_000]), np.array([[2_000] * 4] * 2),
        np.array([0, 0]), np.array([100_000, 100_000]))
    candidate = CertifiedCandidatePlan("build", "rule", "query", (),
                                       (prepared,), "candidate-token")
    return market, candidate


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, query):
        self.sql.append(query)
        return "\n".join(json.dumps(row) for row in self.rows)


def row(*, price=101_000, valid=1):
    return {"ticker": "ABCD", "bucket_index":
            (SESSION_OPEN_OFFSET_MS + 1_000) // 100 - 1,
            "resolution_ms": 100, "price_valid": valid, "close_int": price}


def test_one_unique_activation_read_for_two_candidates():
    market, candidates = plans()
    client = Client((row(),))
    result = load_strategy_one_activations(market, candidates, client=client)
    assert [(item.boundary_ms, item.ticker, item.price_int)
            for item in result.rows] == [(1_000, "ABCD", 101_000)]
    assert len(result.token) == 64
    assert len(client.sql) == 1
    assert all(sql.lstrip().startswith("SELECT") and "arte.bars_v1" in sql
               for sql in client.sql)


def test_missing_invalid_or_duplicate_source_blocks_preflight():
    market, candidates = plans()
    for rows in ((), (row(valid=0),), (row(), row())):
        with pytest.raises(RuntimeError, match="activation"):
            load_strategy_one_activations(market, candidates,
                                          client=Client(rows))
