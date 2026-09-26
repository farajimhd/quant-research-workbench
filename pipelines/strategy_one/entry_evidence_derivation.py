"""Per-ticker causal producer lane for reusable Strategy 1 entry evidence.

This computes from certified ARTE inputs once, without financial state. The
Backtest consumer never imports this producer and never repairs missing rows.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Mapping

from pipelines.strategy_one.entry_evidence_publication import EntryPublicationScope
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, project_market_day_plan,
)
from src.backend.backtest_strategy_one_activation import CertifiedActivationPlan
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_entry_product import (
    ActivationFact, CandidateFact, content_hash, project_activation,
    project_candidate,
)
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_scheduler import (
    build_certified_strategy_one_scheduler, run_strategy_one_boundaries,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_entry_evidence_schema import TICK_SIZE


def publication_scope(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    activations: CertifiedActivationPlan, pivots: CertifiedPivotPlan,
    hod: CertifiedHodPlan, seeds: CertifiedSeedPlan, *, ticker: str,
) -> EntryPublicationScope:
    """Pin every upstream attempt and plan before any expensive derivation."""
    if (len(market.sessions) != 1 or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or any(plan_id != market.build_id for plan_id in (
                candidates.source_build_id, pivots.source_build_id,
                hod.source_build_id, seeds.build_id))
            or pivots.session_date != market.sessions[0]
            or hod.session_date != market.sessions[0]):
        raise ValueError("Strategy 1 producer lacks pinned 100ms source plans")
    day = market.sessions[0]
    prepared = [row for row in candidates.prepared if row.ticker == ticker]
    candidate_coverage = [row for row in candidates.coverage
                          if row.ticker == ticker and row.session_date == day]
    bars = [row for row in market.units if row.stage == "bars"
            and row.ticker == ticker and row.session_date == day]
    source_activations = [row for row in activations.rows if row.ticker == ticker]
    if (len(prepared) != 1 or len(candidate_coverage) != 1 or len(bars) != 1
            or len([row for row in pivots.intervals if row[0] == ticker]) != 1
            or len([row for row in hod.contexts if row[0] == ticker]) != 1
            or len([row for row in seeds.units if row["ticker"] == ticker
                    and row["backtest_session"] == day]) != 1):
        raise ValueError("Strategy 1 producer ticker lacks exact source coverage")
    starts = tuple(sorted({int(value) for value in prepared[0].episode_start_ms}))
    clocks = tuple(int(value) for value in prepared[0].boundary_ms)
    if (len(clocks) != candidate_coverage[0].candidate_count
            or candidate_coverage[0].source_attempts[0] != bars[0].attempt_id
            or tuple(row.boundary_ms for row in source_activations) != starts
            or len(starts) != len(source_activations)
            or not clocks or clocks[-1] > 57_600_000):
        raise ValueError("Strategy 1 producer clocks differ from certified sources")
    return EntryPublicationScope(
        market.build_id, day, ticker, bars[0].attempt_id,
        candidate_coverage[0].derivation_attempt_id,
        candidate_coverage[0].content_hash,
        candidates.token, activations.token, pivots.token, hod.token,
        seeds.token, clocks, starts)


async def derive_unit(
    scope: EntryPublicationScope, market: CertifiedMarketDayPlan,
    candidates: CertifiedCandidatePlan, activations: CertifiedActivationPlan,
    pivots: CertifiedPivotPlan, hod: CertifiedHodPlan, seeds: CertifiedSeedPlan,
    prices: PriceLevelPlan, *, client_factory: Callable[[], Any],
) -> tuple[tuple[ActivationFact, ...], tuple[CandidateFact, ...]]:
    """Run the same completed-boundary V7/BOS lane used by Strategy 1."""
    expected = publication_scope(
        market, candidates, activations, pivots, hod, seeds,
        ticker=scope.ticker)
    if expected != scope or not callable(client_factory):
        raise ValueError("Strategy 1 derivation differs from publication scope")
    selected = [row for row in candidates.prepared if row.ticker == scope.ticker]
    activation_rows = tuple(row for row in activations.rows
                            if row.ticker == scope.ticker)
    projected = project_market_day_plan(market, (scope.ticker,))
    projected_candidates = replace(candidates, prepared=tuple(selected))
    projected_activations = replace(activations, rows=activation_rows)
    scheduler = build_certified_strategy_one_scheduler(
        projected, projected_candidates, activations=projected_activations,
        price_plan=prices.projected(projected),
        through_boundary_ms=scope.candidate_boundaries[-1],
        client_factory=client_factory, max_workers=1,
        max_candidate_rows=len(scope.candidate_boundaries))
    activation_facts: list[ActivationFact] = []
    candidate_facts: list[CandidateFact] = []
    with closing(client_factory()) as reader:
        evidence = StrategyOneCausalEvidence(
            market_plan=projected, seed_plan=seeds, pivot_plan=pivots,
            hod_plan=hod, session=date.fromisoformat(scope.session_date),
            client=reader)

        async def no_broker(ticker: str, rows: Mapping,
                            boundary: int) -> None:
            # The scheduler presents each sparse candidate's completed 100ms
            # liquidity row to the broker callback before its decision. This
            # producer owns no broker or orders, but validates that row clock.
            row = rows.get(100)
            if (ticker != scope.ticker or row is None
                    or row.get("ticker") != ticker
                    or row.get("boundary_ms") != boundary
                    or boundary not in scope.candidate_boundaries):
                raise RuntimeError("Strategy 1 producer received active broker data")

        async def evaluate(_ticker: str, _rows: Mapping, candidate: Any) -> None:
            if candidate is None:
                raise RuntimeError("Candidate-only producer received an active symbol")
            value = await evidence.entry_evidence(candidate, tick=TICK_SIZE)
            candidate_facts.append(project_candidate(value))

        async def activate(value: Any) -> None:
            activation_facts.append(project_activation(
                await evidence.observe_activation(value)))

        async def finish(_work: Any) -> None:
            return None

        count = await run_strategy_one_boundaries(
            scheduler, process_broker_row=no_broker, evaluate_ticker=evaluate,
            financially_active_tickers=lambda: (), finish_boundary=finish,
            observe_activation=activate,
            observe_completed_seconds=evidence.observe_completed_seconds)
    result = tuple(activation_facts), tuple(candidate_facts)
    if (count < len(scope.candidate_boundaries)
            or tuple(value.episode_start_ms for value in result[0])
            != scope.episode_starts
            or tuple(value.boundary_ms for value in result[1])
            != scope.candidate_boundaries):
        raise RuntimeError("Strategy 1 causal derivation omitted certified clocks")
    content_hash(*result)
    return result


def derive_unit_sync(*args: Any, **kwargs: Any):
    """One worker thread owns one event loop and one independent ticker lane."""
    return asyncio.run(derive_unit(*args, **kwargs))
