"""Backtest reads only a bit-exact, pinned V7 interval attempt."""
from __future__ import annotations

import json
from uuid import UUID

import pyarrow as pa

from src.backend import backtest_strategy_one_v7_interval_store as store
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7_interval_schema import PRODUCT_DIGEST
from src.trading_runtime.strategy_one_v7_intervals import (
    V7LevelInterval, clock_hash, interval_hash,
)


DAY = "2026-08-18"
BAR_ATTEMPT = str(UUID(int=1))
DERIVATION_ATTEMPT = str(UUID(int=2))
LEVEL = V7LevelInterval("level-1", 0, 0, 57_600_001, 10.0, 10.2,
                        "resistance", "", 1_800_000_000_000, True)


class Reader:
    def __init__(self, *, changed_hash=False):
        self.changed_hash = changed_hash

    def execute(self, sql):
        assert "strategy_one_v7_coverage_v1" in sql
        return json.dumps({
            "ticker": "TEST", "attempt_id": DERIVATION_ATTEMPT,
            "bars_attempt_id": BAR_ATTEMPT,
            "source_checkpoint_hash": "a" * 64,
            "decoded_seed_hash": "b" * 64,
            "seed_source_plan_hash": "c" * 64,
            "split_evidence_hash": "d" * 64,
            "seed_input_policy": POLICY, "product_digest": PRODUCT_DIGEST,
            "clock_count": 1, "interval_count": 1,
            "clock_hash": "f" * 64 if self.changed_hash else clock_hash((1000,)),
            "interval_hash": interval_hash((LEVEL,)),
        }) + "\n"

    def iter_arrow_record_batches(self, sql):
        if "strategy_one_v7_clock_v1" in sql:
            yield pa.record_batch(
                [["TEST"], [DERIVATION_ATTEMPT], [1000]],
                names=["ticker", "attempt_id", "boundary_ms"])
        else:
            yield pa.record_batch(
                [["TEST"], [DERIVATION_ATTEMPT], [LEVEL.level_id],
                 [LEVEL.ordinal], [LEVEL.valid_from_ms], [LEVEL.valid_to_ms],
                 [LEVEL.lower], [LEVEL.upper], [LEVEL.role], ["none"],
                 [LEVEL.confirmed_at_ms], [1]],
                names=["ticker", "attempt_id", "level_id", "ordinal",
                       "valid_from_ms", "valid_to_ms", "lower", "upper",
                       "role", "transition_from", "confirmed_at_ms",
                       "historical"])


def plans():
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build-1", "definition", (DAY,),
        ("TEST",), (MarketDayUnit("build-1", DAY, "TEST", "bars",
                                 BAR_ATTEMPT, "source", 1, "output"),),
        (100, 1000), "market-token")
    seeds = CertifiedSeedPlan(
        "build-1", "catalog", ({"ticker": "TEST", "backtest_session": DAY,
                                  "source_checkpoint_hash": "a" * 64,
                                  "source_plan_hash": "c" * 64,
                                  "input_policy": POLICY,
                                  "level_count": 1},), "seed-token", False)
    return market, seeds


def test_certified_v7_intervals_are_causal_and_read_only(monkeypatch):
    monkeypatch.setattr(store, "verify_tables", lambda _client: None)
    market, seeds = plans()
    plan = store.certify_v7_interval_plan(
        market, seeds, session_date=DAY, candidate_tickers=("TEST",),
        client=Reader())
    assert plan.levels("TEST", boundary_ms=1000)[0]["unified_level_id"] == "level-1"
    assert plan.coverage[0].bars_attempt_id == BAR_ATTEMPT


def test_changed_clock_hash_blocks_certificate(monkeypatch):
    import pytest
    monkeypatch.setattr(store, "verify_tables", lambda _client: None)
    market, seeds = plans()
    with pytest.raises(RuntimeError, match="children differ"):
        store.certify_v7_interval_plan(
            market, seeds, session_date=DAY, candidate_tickers=("TEST",),
            client=Reader(changed_hash=True))
