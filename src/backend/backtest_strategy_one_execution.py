"""Bind the numbered Strategy 1 sparse tape to shared Backtest authorities.

No frame spool, market builder, per-row indicator calculation, SQLite, or
disk journal belongs in this lane. Market inputs are preflight-certified
persisted products; the broker and Portfolio/OMS alone mutate financial state.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date
from typing import Any, Awaitable, Callable, Sequence

import numpy as np

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_market_data import CertifiedMarketDayPlan, project_market_day_plan
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_strategy_one_activation import (
    CertifiedActivationPlan, project_activation_plan,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CertifiedCandidatePlan, project_candidate_plan,
)
from src.backend.backtest_strategy_one_coordinator import (
    StrategyOneProposalCounts, run_strategy_one_proposals,
)
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_views
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, StrategyOneBoundaryWork,
    build_certified_strategy_one_scheduler,
)
from src.backend.backtest_strategy_one_static_gate import (
    StrategyOneStaticGate, compile_static_entry_gate,
    project_static_survivors,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


async def run_certified_strategy_one_session(
    *, market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    activations: CertifiedActivationPlan, pivots: CertifiedPivotPlan,
    hod: CertifiedHodPlan, seeds: CertifiedSeedPlan,
    entry: CertifiedEntryEvidencePlan, prices: PriceLevelPlan,
    through_boundary_ms: int, runtime: Any,
    assignments: Sequence[StrategyAssignment],
    client_factory: Callable[[], Any], tick_for_ticker: Callable[[str], float],
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    max_workers: int = 4,
) -> StrategyOneProposalCounts:
    """Compose the certified sparse route without legacy frames or events.

    Preflight must already have sealed every full-population input. Projection
    is only a run-horizon/read-I/O optimization; it cannot revise those seals.
    """
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(activations, CertifiedActivationPlan)
            or not isinstance(pivots, CertifiedPivotPlan)
            or not isinstance(hod, CertifiedHodPlan)
            or not isinstance(seeds, CertifiedSeedPlan)
            or not isinstance(entry, CertifiedEntryEvidencePlan)
            or not isinstance(prices, PriceLevelPlan)
            or len(market.sessions) != 1
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100
            or type(max_workers) is not int or not 1 <= max_workers <= 16
            or any(not callable(callback) for callback in (
                client_factory, tick_for_ticker, before_boundary, finish_boundary))):
        raise ValueError("Strategy 1 session lacks pinned 100ms inputs")
    visible = project_candidate_plan(
        candidates, through_boundary_ms=through_boundary_ms)
    if not visible.prepared:
        raise RuntimeError("Strategy 1 zero-candidate terminal authority is not typed")
    visible_activations = project_activation_plan(
        activations, candidates, through_boundary_ms=through_boundary_ms)
    full_gate = compile_static_entry_gate(visible, entry)
    survivors, activation_schedule = project_static_survivors(
        visible, visible_activations, full_gate)
    # The scheduler sees only survivors. Its local gate must index exactly
    # those rows, while the full mask remains a separate certified reduction.
    surviving_facts = tuple(full_gate.facts[index]
                            for index in full_gate.eligible_indices)
    surviving_gate = StrategyOneStaticGate(
        surviving_facts, np.zeros(len(surviving_facts), dtype=np.uint8),
        np.arange(len(surviving_facts), dtype=np.int64))
    selected = tuple(row.ticker for row in visible.prepared)
    projected = project_market_day_plan(market, selected)
    projected_prices = prices.projected(projected)
    scheduler = build_certified_strategy_one_scheduler(
        projected, survivors, activations=activation_schedule,
        price_plan=projected_prices, through_boundary_ms=through_boundary_ms,
        client_factory=client_factory, max_workers=max_workers,
        activation_source_candidates=visible)
    try:
        reader = client_factory()
    except BaseException:
        scheduler.close()
        raise
    if reader is None or not callable(getattr(reader, "close", None)):
        scheduler.close()
        raise TypeError("Strategy 1 V7 source needs a closable read client")
    with closing(reader):
        try:
            evidence = StrategyOneCausalEvidence(
                market_plan=projected, seed_plan=seeds,
                pivot_plan=pivots, hod_plan=hod,
                session=date.fromisoformat(projected.sessions[0]), client=reader)
            manager = StrategyOneManagementRunner(
                runtime=runtime, evidence=evidence,
                tick_for_ticker=tick_for_ticker)
            return await run_strategy_one_fixed_session(
                scheduler, entry, evidence, manager, runtime=runtime,
                static_gate=surviving_gate, assignments=assignments,
                before_boundary=before_boundary,
                finish_boundary=finish_boundary)
        finally:
            scheduler.close()


async def run_strategy_one_fixed_session(
    scheduler: StrategyOneBoundaryScheduler,
    entry: CertifiedEntryEvidencePlan, evidence: StrategyOneCausalEvidence,
    manager: StrategyOneManagementRunner, *, runtime: Any,
    static_gate: StrategyOneStaticGate,
    assignments: Sequence[StrategyAssignment],
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
) -> StrategyOneProposalCounts:
    """Run one causal 100 ms session with broker-first global boundaries.

    The finish callback is the run controller's ordered journal/UI boundary
    authority. It must not create market products or reinterpret broker bars.
    """
    config = getattr(runtime, "config", None)
    broker = getattr(runtime, "broker", None)
    order_manager = getattr(runtime, "order_manager", None)
    if (not isinstance(scheduler, StrategyOneBoundaryScheduler)
            or not isinstance(entry, CertifiedEntryEvidencePlan)
            or not isinstance(evidence, StrategyOneCausalEvidence)
            or not isinstance(manager, StrategyOneManagementRunner)
            or not isinstance(static_gate, StrategyOneStaticGate)
            or manager.runtime is not runtime or manager.evidence is not evidence
            or scheduler.session_date != entry.session_date
            or not isinstance(getattr(runtime, "journal", None), BacktestMemoryJournal)
            or config is None or config.mode != RunMode.BACKTEST
            or config.strategy_id != STRATEGY_ID
            or config.strategy_revision != STRATEGY_NUMBER
            or not callable(getattr(runtime, "process_liquidity_boundary", None))
            or not callable(getattr(broker, "financially_active_tickers", None))
            or not callable(getattr(broker, "positions", None))
            or not callable(getattr(order_manager, "snapshots", None))
            or not callable(finish_boundary)
            or not callable(before_boundary)
            or not isinstance(assignments, (tuple, list)) or not assignments
            or any(not isinstance(row, StrategyAssignment)
                   or (row.strategy_id, row.strategy_revision)
                   != (STRATEGY_ID, STRATEGY_NUMBER)
                   for row in assignments)):
        raise ValueError("Strategy 1 fixed session lacks numbered shared authorities")
    by_ticker: dict[str, list[StrategyAssignment]] = defaultdict(list)
    identities = set()
    for assignment in assignments:
        identity = (assignment.account_id, assignment.assignment_id)
        if identity in identities:
            raise ValueError("Strategy 1 assignment identity is duplicated")
        identities.add(identity)
        by_ticker[assignment.ticker].append(assignment)
    session = date.fromisoformat(scheduler.session_date)
    if evidence.session != session:
        raise ValueError("Strategy 1 V7 evidence differs from fixed session")
    if any(entry.lookup(fact.ticker, fact.boundary_ms) != fact
           for fact in static_gate.facts):
        raise ValueError("Strategy 1 vectorized gate differs from certified entry facts")

    async def process_broker(work: StrategyOneBoundaryWork) -> None:
        rows = [resolutions[100] for _, resolutions in work.broker_rows
                if 100 in resolutions]
        if rows:
            await runtime.process_liquidity_boundary(
                rows, at=market_day_boundary(session, work.boundary_ms))

    async def financial_views(ticker: str, _boundary_ms: int):
        selected = by_ticker.get(ticker)
        if not selected:
            raise ValueError("Strategy 1 market ticker lacks a numbered assignment")
        return await read_strategy_one_financial_views(
            tuple(selected), broker, order_manager)

    def financially_active_tickers() -> tuple[str, ...]:
        tickers = broker.financially_active_tickers()
        if (not isinstance(tickers, tuple)
                or any(ticker not in by_ticker for ticker in tickers)):
            raise RuntimeError("Strategy 1 broker owns an unassigned active ticker")
        return tickers

    return await run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=process_broker,
        before_boundary=before_boundary,
        financial_views=financial_views,
        on_entry_proposal=manager.on_entry_proposal,
        on_management=manager.on_management,
        position_source_owned=manager.owns_position_source,
        financially_active_tickers=financially_active_tickers,
        finish_boundary=finish_boundary,
        observe_activation=evidence.observe_activation,
        observe_completed_seconds=evidence.observe_completed_seconds,
        static_gate=static_gate)
