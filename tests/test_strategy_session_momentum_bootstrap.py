"""Real async bootstrap selects momentum policy before downstream price I/O."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_declared_momentum_growth import FractionSource, native
from src.backend import backtest_strategy_one_execution as subject
from src.backend.backtest_strategy_one_activation import (
    CertifiedActivationPlan, StrategyOneActivation,
)
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_declared_initial_momentum import CertifiedDeclaredInitialMomentumPlan
from src.backend.backtest_strategy_initial_ten_percent import CertifiedInitialTenPercentPlan
from src.backend.backtest_strategy_one_candidate_store import _token
from src.trading_runtime.strategy_engine import (
    StrategyAssignment, AssignmentStatus, StrategyPermissions,
)
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy


class SelectionObserved(Exception):
    """Stop before external pricing, after the real native policy compilation."""


@pytest.mark.parametrize("number", (42, 50, 57, 58, 59, 60, 61))
def test_real_session_bootstrap_reaches_exact_selected_momentum_parent(monkeypatch, number):
    market, prepared, _ = native(61, first=1.2, current=1.27)
    candidates, entry = prepared.candidates, prepared.entry
    counts = {row.ticker: len(row.boundary_ms) for row in candidates.prepared}
    candidates = replace(candidates, coverage=tuple(
        replace(row, candidate_count=counts.get(row.ticker, 0))
        for row in candidates.coverage))
    candidates = replace(candidates, token=_token(
        candidates.source_build_id, candidates.candidate_rule_digest,
        candidates.scan_query_sha256, candidates.coverage))
    activations = CertifiedActivationPlan(tuple(
        StrategyOneActivation(start, ticker, 101)
        for ticker, start in sorted({(row.ticker, int(start))
                                    for row in candidates.prepared
                                    for start in row.episode_start_ms})), "a" * 64)
    clients, observed = [], []

    class Reader(FractionSource):
        def __init__(self):
            super().__init__(first=1.2, current=1.27)
            self.closed = False

        def close(self):
            self.closed = True

    def client_factory():
        reader = Reader()
        clients.append(reader)
        return reader

    def after_selection(actual_market, parent, *, client):
        assert actual_market is market and client is clients[-1]
        assert parent.candidates is candidates and parent.entry is entry
        policy = numbered_fixed_strategy(number).entry_momentum_growth_policy
        if policy is None:
            assert type(parent) is CertifiedInitialTenPercentPlan
            assert parent.eligible_mask.tolist() == [True, False]
        else:
            assert type(parent) is CertifiedDeclaredInitialMomentumPlan
            assert parent.policy == policy
            assert parent.first_indices.tolist() == [0, 0]
            assert parent.eligible_mask.tolist() == ([True, False] if number == 60 else [True, True])
        observed.append(parent)
        raise SelectionObserved()

    from src.backend import backtest_strategy_first_price_source
    monkeypatch.setattr(backtest_strategy_first_price_source, "load_first_price_source", after_selection)

    async def unused_boundary(work):
        pytest.fail("Policy bootstrap must not process a market boundary")

    with pytest.raises(SelectionObserved):
        asyncio.run(subject.run_certified_strategy_one_session(
            market=market, candidates=candidates, activations=activations,
            pivots=CertifiedPivotPlan("build", "2026-08-18", (), (), "i" * 64),
            hod=CertifiedHodPlan("build", "2026-08-18", (), "h" * 64),
            seeds=CertifiedSeedPlan("build", "v" * 64, (), "z" * 64, True),
            entry=entry, prices=PriceLevelPlan("build", (), "p" * 64),
            through_boundary_ms=57_600_000,
            runtime=SimpleNamespace(config=SimpleNamespace(strategy_revision=number)),
            assignments=(StrategyAssignment(
                "A1", "early-squeeze-strategy", number, "DU1", "AAA", 123,
                AssignmentStatus.WATCHING, StrategyPermissions(enter=True),
                {"execution": {"tick_size": .01}}),),
            client_factory=client_factory, before_boundary=unused_boundary,
            finish_boundary=unused_boundary))
    assert len(observed) == 1 and len(clients) == 2
    assert all(client.closed for client in clients)
