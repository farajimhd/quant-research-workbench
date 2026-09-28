"""Producer derives V7 intervals only from certified completed ARTE bars."""
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from pipelines.strategy_one import v7_interval_derivation as derivation
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan, load_seed
from src.trading_runtime.strategy_one_v7_intervals import levels_at
from tests.test_structural_v7_seed import Client


def _plans(prior):
    attempt = str(UUID(int=1))
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build-1", "definition-1",
        ("2026-08-18",), ("TEST",),
        (MarketDayUnit("build-1", "2026-08-18", "TEST", "bars",
                       attempt, "source", 2, "rows"),),
        (100, 1_000), "market-token")
    pinned = {"ticker": "TEST", "backtest_session": "2026-08-18",
              "source_checkpoint_hash": prior["source_checkpoint_hash"],
              "source_plan_hash": "b" * 64}
    seeds = CertifiedSeedPlan("build-1", "b" * 64, (pinned,),
                              "seed-token", True)
    return market, seeds


def test_producer_uses_pinned_seed_and_completed_seconds_only(monkeypatch):
    prior = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    market, seeds = _plans(prior)
    read = SimpleNamespace(execute=lambda sql: None,
                           iter_json_each_row=lambda sql: iter(()))
    bar = {"ticker": "TEST", "resolution_ms": 1_000,
           "bucket_index": 14_700, "price_valid": 1,
           "extremes_valid": 1, "open_int": 100_000,
           "high_int": 100_100, "low_int": 99_900,
           "close_int": 100_050, "volume": 100}
    seen = []
    monkeypatch.setattr(derivation, "load_seed",
                        lambda *_args, **kwargs: (
                            seen.append(kwargs["coverage"]) or prior))
    monkeypatch.setattr(derivation, "split_evidence",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(derivation, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: iter((
                            bar, {**bar, "bucket_index": 14_701,
                                  "price_valid": 0, "extremes_valid": 0})))
    result = derivation.derive_ticker_day(
        market=market, seeds=seeds, session_date="2026-08-18",
        ticker="TEST", reader=read)
    assert seen == [seeds.units[0]]
    assert result.bars_attempt_id == str(UUID(int=1))
    assert result.source_checkpoint_hash == prior["source_checkpoint_hash"]
    assert result.decoded_seed_hash == prior["checkpoint_hash"]
    assert result.valid_seconds == (301_000,)
    assert len(result.split_evidence_hash) == 64
    assert levels_at(boundary_ms=301_000,
                     seed_policy=result.seed_input_policy,
                     valid_seconds=result.valid_seconds,
                     intervals=result.intervals)


def test_producer_rejects_unordered_or_other_ticker_bar(monkeypatch):
    prior = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    market, seeds = _plans(prior)
    read = SimpleNamespace(execute=lambda sql: None,
                           iter_json_each_row=lambda sql: iter(()))
    monkeypatch.setattr(derivation, "load_seed", lambda *_args, **_kwargs: prior)
    monkeypatch.setattr(derivation, "split_evidence",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(derivation, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: iter(({
                            "ticker": "OTHER", "resolution_ms": 1_000,
                            "bucket_index": 14_700,
                        },)))
    with pytest.raises(ValueError, match="order or identity"):
        derivation.derive_ticker_day(
            market=market, seeds=seeds, session_date="2026-08-18",
            ticker="TEST", reader=read)
