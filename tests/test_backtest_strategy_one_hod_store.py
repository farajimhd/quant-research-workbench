"""Backtest can only SELECT exact covered HOD candidate context."""

import json

import numpy as np
import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CandidateCoverage, CertifiedCandidatePlan,
)
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_hod_product import (
    HodContext, context_content_hash,
)
from src.trading_runtime.strategy_one_hod_schema import PRODUCT_DIGEST


ATTEMPTS = tuple(f"00000000-0000-0000-0000-{number:012d}"
                 for number in range(1, 6))


def plans():
    day, ticker = "2026-08-18", "TEST"
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (day,), (ticker,),
        tuple(MarketDayUnit("build", day, ticker, stage, ATTEMPTS[index],
                            "source", 1, "hash")
              for index, stage in enumerate(("bars", "technical", "broker_100ms"))),
        (100,), "market-token")
    coverage = CandidateCoverage(day, ticker, ATTEMPTS[3], ATTEMPTS[:3],
                                 1, "a" * 64)
    prepared = PreparedStrategyOneTicker(
        ticker, 1, np.array([0]), np.array([300_100]),
        np.array([300_000]), np.array([[300_000] * 4]),
        np.array([300_000]), np.array([90_000]))
    candidates = CertifiedCandidatePlan(
        "build", "b" * 64, "c" * 64, (coverage,), (prepared,), "d" * 64)
    seeds = CertifiedSeedPlan(
        "build", "e" * 64,
        ({"ticker": ticker, "backtest_session": day},), "f" * 64, True)
    return market, candidates, seeds


class Client:
    def __init__(self, *, wrong_candidate=False):
        self.queries = []
        self.wrong_candidate = wrong_candidate

    def execute(self, sql):
        self.queries.append(sql)
        if "strategy_one_hod_coverage_v1" in sql:
            rows = [dict(ticker="TEST", derivation_attempt_text=ATTEMPTS[4],
                         bars_attempt_text=ATTEMPTS[0],
                         candidate_attempt_text=(ATTEMPTS[0] if self.wrong_candidate
                                                 else ATTEMPTS[3]),
                         candidate_content_hash="a" * 64,
                         v7_seed_plan_token="f" * 64,
                         product_digest=PRODUCT_DIGEST,
                         context_count=1,
                         content_hash=context_content_hash((
                             HodContext(300_100, 100_000, 120_000, True, "r11"),)))]
        elif "strategy_one_hod_context_v1" in sql:
            rows = [dict(ticker="TEST", derivation_attempt_text=ATTEMPTS[4],
                         boundary_ms=300_100, session_open_int=100_000,
                         prior_hod_int=120_000, late_mode=1,
                         gate_level_id="r11")]
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_exact_hod_context_is_certified_read_only(monkeypatch):
    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_hod_store.verify_tables",
        lambda _client: None)
    client = Client()
    result = certify_hod_plan(*plans(), client=client)
    assert result.lookup("TEST", 300_100).gate_level_id == "r11"
    assert len(result.token) == 64
    assert all(sql.lstrip().startswith("SELECT") for sql in client.queries)
    with pytest.raises(ValueError, match="lacks certified"):
        result.lookup("TEST", 300_200)


def test_hod_coverage_rejects_wrong_candidate_attempt(monkeypatch):
    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_hod_store.verify_tables",
        lambda _client: None)
    with pytest.raises(RuntimeError, match="lineage"):
        certify_hod_plan(*plans(), client=Client(wrong_candidate=True))
