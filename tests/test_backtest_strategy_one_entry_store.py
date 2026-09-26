"""The Backtest entry reader accepts only fully sealed, exact source rows."""
from dataclasses import replace
import json
from uuid import uuid4

import numpy as np
import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_activation import (
    CertifiedActivationPlan, StrategyOneActivation,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CandidateCoverage, CertifiedCandidatePlan,
)
from src.backend.backtest_strategy_one_entry_product import (
    ActivationFact, CandidateFact, content_hash,
)
from src.backend.backtest_strategy_one_entry_store import (
    certify_entry_evidence_plan,
)
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_entry_evidence_schema import PRODUCT_DIGEST


def _plans():
    build, session = "b" * 16, "2026-08-18"
    bar_attempt, candidate_attempt = str(uuid4()), str(uuid4())
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), build, "d" * 64, (session,), ("AAA",),
        (MarketDayUnit(build, session, "AAA", "bars", bar_attempt,
                       "s" * 64, 1, "o" * 64),), (100,), "m" * 64)
    prepared = PreparedStrategyOneTicker(
        "AAA", 1, np.array([0]), np.array([31_000]),
        np.array([30_000]), np.array([[31_000] * 4]),
        np.array([30_000]), np.array([99_000]))
    candidates = CertifiedCandidatePlan(
        build, "r" * 64, "q" * 64,
        (CandidateCoverage(session, "AAA", candidate_attempt,
                           (bar_attempt, str(uuid4()), str(uuid4())),
                           1, "c" * 64),), (prepared,), "p" * 64)
    activations = CertifiedActivationPlan(
        (StrategyOneActivation(30_000, "AAA", 100_000),), "a" * 64)
    pivots = CertifiedPivotPlan(build, session, (), (("AAA", ()),), "i" * 64)
    hod = CertifiedHodPlan(build, session, (("AAA", ()),), "h" * 64)
    seeds = CertifiedSeedPlan(build, "x" * 64,
                              ({"ticker": "AAA", "backtest_session": session},),
                              "v" * 64, True)
    return market, candidates, activations, pivots, hod, seeds


class Reader:
    def __init__(self, plans):
        market, candidates, activations, pivots, hod, seeds = plans
        self.queries = []
        self.activation = ActivationFact("AAA", 30_000, 100_000, .5, ("R1",))
        self.candidate = CandidateFact(
            "AAA", 31_000, 30_000, 30_000, "P1", 101_000,
            "v7_resistance", "R3", "P1", True, 9.89, 12.0, "R3", 3)
        self.coverage = dict(
            ticker="AAA", attempt_id=str(uuid4()),
            bars_attempt_id=market.units[0].attempt_id,
            candidate_attempt_id=candidates.coverage[0].derivation_attempt_id,
            candidate_content_hash=candidates.coverage[0].content_hash,
            candidate_plan_token=candidates.token,
            activation_plan_token=activations.token,
            pivot_plan_token=pivots.token, hod_plan_token=hod.token,
            v7_seed_plan_token=seeds.token, product_digest=PRODUCT_DIGEST,
            activation_count=1, resistance_count=1, evidence_count=1,
            content_hash=content_hash((self.activation,), (self.candidate,)))

    def execute(self, query):
        self.queries.append(query)
        assert query.lstrip().startswith("SELECT ")
        if "FROM arte.strategy_one_entry_coverage_v1" in query:
            rows = [self.coverage]
        elif "FROM arte.strategy_one_entry_activation_resistance_v1" in query:
            rows = [dict(ticker="AAA", **self.activation.resistance_rows()[0])]
        elif "FROM arte.strategy_one_entry_activation_v1" in query:
            rows = [dict(ticker="AAA", **self.activation.row())]
        elif "FROM arte.strategy_one_entry_evidence_v1" in query:
            rows = [dict(ticker="AAA", **self.candidate.row())]
        else:
            raise AssertionError(query)
        return "\n".join(json.dumps(row) for row in rows)


def test_entry_store_certifies_exact_rows_without_any_write(monkeypatch):
    monkeypatch.setattr("src.backend.backtest_strategy_one_entry_store.verify_tables",
                        lambda client: None)
    plans = _plans()
    reader = Reader(plans)
    result = certify_entry_evidence_plan(*plans, client=reader)
    assert result.lookup("AAA", 31_000) == reader.candidate
    assert len(result.token) == 64
    assert len(reader.queries) == 4
    with pytest.raises(ValueError, match="lacks certified entry evidence"):
        result.lookup("AAA", 31_100)


@pytest.mark.parametrize("field,value", [
    ("candidate_plan_token", "z" * 64),
    ("activation_plan_token", "z" * 64),
    ("content_hash", "z" * 64),
])
def test_entry_store_rejects_stale_or_tampered_coverage(monkeypatch, field, value):
    monkeypatch.setattr("src.backend.backtest_strategy_one_entry_store.verify_tables",
                        lambda client: None)
    plans = _plans()
    reader = Reader(plans)
    reader.coverage[field] = value
    with pytest.raises(RuntimeError, match="coverage differs|child rows differ"):
        certify_entry_evidence_plan(*plans, client=reader)


def test_entry_store_rejects_missing_and_duplicate_children(monkeypatch):
    monkeypatch.setattr("src.backend.backtest_strategy_one_entry_store.verify_tables",
                        lambda client: None)
    plans = _plans()
    reader = Reader(plans)
    original = reader.execute
    reader.execute = lambda query: ("" if "activation_resistance_v1" in query
                                    else original(query))
    with pytest.raises(RuntimeError, match="activation children"):
        certify_entry_evidence_plan(*plans, client=reader)


def test_entry_store_rejects_duplicate_source_coverage():
    plans = _plans()
    market, candidates, *rest = plans
    candidates = replace(candidates, coverage=candidates.coverage * 2)
    with pytest.raises(ValueError, match="source ticker sets differ"):
        certify_entry_evidence_plan(market, candidates, *rest, client=None)
