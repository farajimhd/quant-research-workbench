"""The fixed Strategy 1 composer must pass only static survivors to its tape."""
import asyncio
from types import SimpleNamespace

import numpy as np

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
    monkeypatch.setattr(subject, "StrategyOneCausalEvidence",
                        lambda **_kwargs: SimpleNamespace())
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
        runtime=object(), assignments=(), client_factory=lambda: reader,
        tick_for_ticker=lambda _: .01, before_boundary=boundary,
        finish_boundary=boundary))
    assert result == "complete"
    assert calls == ["scheduler_built", "executed", "scheduler_closed", "reader_closed"]
