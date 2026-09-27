"""The fixed Strategy 1 composer must pass only static survivors to its tape."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from src.backend import backtest_strategy_one_execution as subject
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.backtest_strategy_one_activation import CertifiedActivationPlan
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)


def test_execution_ticks_are_pinned_and_consistent_across_accounts():
    def assignment(account, tick):
        return StrategyAssignment(
            account, "early-squeeze-strategy", 1, account, "AAA", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True),
            {"execution": {"tick_size": tick}})

    assert subject.pinned_strategy_one_ticks(
        (assignment("DU1", .01), assignment("DU2", .01))) == {"AAA": .01}
    with pytest.raises(ValueError, match="disagree"):
        subject.pinned_strategy_one_ticks(
            (assignment("DU1", .01), assignment("DU2", .0001)))
    with pytest.raises(ValueError, match="missing or invalid"):
        subject.pinned_strategy_one_ticks((assignment("DU1", 0),))


def test_composition_prunes_before_market_read_and_closes_reader(monkeypatch):
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "d" * 64,
        ("2026-08-18",), ("AAA",), (), (100,), "m" * 64)
    candidates = CertifiedCandidatePlan(
        "build", "r" * 64, "s" * 64, (),
        (SimpleNamespace(ticker="AAA"),), "c" * 64)
    activations = CertifiedActivationPlan((), "a" * 64)
    entry = CertifiedEntryEvidencePlan("build", "2026-08-18", (), (), (), "e" * 64)
    prices = PriceLevelPlan("build", (), "p" * 64)
    calls = []
    fact_a, fact_b = object(), object()
    full_gate = StrategyOneStaticGate(
        (fact_a, fact_b), np.array([1, 0], dtype=np.uint8),
        np.array([1], dtype=np.int64))
    monkeypatch.setattr(subject, "project_candidate_plan",
                        lambda source, **_: source)
    monkeypatch.setattr(subject, "project_activation_plan",
                        lambda source, *_args, **_kwargs: source)
    monkeypatch.setattr(subject, "compile_static_entry_gate",
                        lambda *_args: full_gate)
    monkeypatch.setattr(subject, "project_static_survivors",
                        lambda *_args: (candidates, activations))
    monkeypatch.setattr(subject, "project_market_day_plan",
                        lambda _market, tickers: market if tickers == ("AAA",) else None)
    monkeypatch.setattr(PriceLevelPlan, "projected", lambda self, _market: self)
    scheduler = SimpleNamespace(close=lambda: calls.append("scheduler_closed"))

    def build(_market, _survivors, *, activation_source_candidates, **_kwargs):
        assert activation_source_candidates is candidates
        calls.append("scheduler_built")
        return scheduler

    monkeypatch.setattr(subject, "build_certified_strategy_one_scheduler", build)
    reader = SimpleNamespace(close=lambda: calls.append("reader_closed"))
    def preload_seeds(_selected, *, client_factory, max_workers):
        assert max_workers == 1
        assert callable(client_factory)
        calls.append("seed_preloaded")

    monkeypatch.setattr(subject, "StrategyOneCausalEvidence",
                        lambda **_kwargs: SimpleNamespace(v7=SimpleNamespace(
                            preload_seeds=preload_seeds)))
    monkeypatch.setattr(subject, "StrategyOneManagementRunner",
                        lambda **_kwargs: SimpleNamespace())

    async def run(_scheduler, _entry, _evidence, _manager, *, static_gate,
                  **_kwargs):
        assert static_gate.facts == (fact_b,)
        assert static_gate.rejection_mask.tolist() == [0]
        calls.append("executed")
        return "complete"

    monkeypatch.setattr(subject, "run_strategy_one_fixed_session", run)

    async def boundary(_work):
        pass

    result = asyncio.run(subject.run_certified_strategy_one_session(
        market=market, candidates=candidates, activations=activations,
        pivots=CertifiedPivotPlan("build", "2026-08-18", (), (), "i" * 64),
        hod=CertifiedHodPlan("build", "2026-08-18", (), "h" * 64),
        seeds=CertifiedSeedPlan("build", "v" * 64, (), "z" * 64, True),
        entry=entry, prices=prices, through_boundary_ms=19_800_000,
        runtime=object(), assignments=(StrategyAssignment(
            "A1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True),
            {"execution": {"tick_size": .01}}),),
        client_factory=lambda: reader, before_boundary=boundary,
        finish_boundary=boundary))
    assert result == "complete"
    assert calls == ["scheduler_built", "seed_preloaded", "executed",
                     "scheduler_closed", "reader_closed"]


@pytest.mark.parametrize("active", [(), ("AAA",)])
def test_empty_causal_horizon_never_reads_market_or_invents_boundary(monkeypatch, active):
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "d" * 64,
        ("2026-08-18",), ("AAA",), (), (100,), "m" * 64)
    candidates = CertifiedCandidatePlan(
        "build", "r" * 64, "s" * 64, (), (), "c" * 64)
    monkeypatch.setattr(subject, "project_candidate_plan",
                        lambda *_args, **_kwargs: candidates)
    runtime = SimpleNamespace(broker=SimpleNamespace(
        financially_active_tickers=lambda: active))

    async def boundary(_work):
        pytest.fail("Empty candidate horizon fabricated a market boundary")

    args = dict(
        market=market, candidates=candidates,
        activations=CertifiedActivationPlan((), "a" * 64),
        pivots=CertifiedPivotPlan("build", "2026-08-18", (), (), "i" * 64),
        hod=CertifiedHodPlan("build", "2026-08-18", (), "h" * 64),
        seeds=CertifiedSeedPlan("build", "v" * 64, (), "z" * 64, True),
        entry=CertifiedEntryEvidencePlan("build", "2026-08-18", (), (), (), "e" * 64),
        prices=PriceLevelPlan("build", (), "p" * 64),
        through_boundary_ms=60_000, runtime=runtime,
        assignments=(StrategyAssignment(
            "A1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True),
            {"execution": {"tick_size": .01}}),),
        client_factory=lambda: pytest.fail("Empty horizon opened a market reader"),
        before_boundary=boundary, finish_boundary=boundary)
    if active:
        with pytest.raises(RuntimeError, match="active broker state"):
            asyncio.run(subject.run_certified_strategy_one_session(**args))
    else:
        result = asyncio.run(subject.run_certified_strategy_one_session(**args))
        assert result == subject.StrategyOneProposalCounts(0, 0, 0, 0)
