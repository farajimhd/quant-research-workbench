"""Bind the numbered Strategy 1 sparse tape to shared Backtest authorities.

No frame spool, market builder, per-row indicator calculation, SQLite, or
disk journal belongs in this lane. Market inputs are preflight-certified
persisted products; the broker and Portfolio/OMS alone mutate financial state.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from math import isfinite
from time import perf_counter
from typing import Any, Awaitable, Callable, Sequence

import numpy as np

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import (
    load_previous_completed_100ms_closes_batch, market_day_boundary,
)
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
from src.backend.backtest_strategy_one_evidence import (
    StrategyOneCausalEvidence, StrategyOneEmptyEvidence, StrategyOneEvidenceState,
)
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_views
from src.backend.backtest_strategy_one_management import (
    StrategyOneManagementRunner, StrategyOneManagementState,
)
from src.backend.backtest_strategy_one_market import DEFAULT_SPARSE_READ_WORKERS
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
from src.trading_runtime.strategy_one_stateful import StrategyOneReentryWitness
from src.trading_runtime.numbered_fixed_strategy import resolve_numbered_fixed_strategy


def pinned_strategy_one_ticks(
    assignments: Sequence[StrategyAssignment],
) -> dict[str, float]:
    """Resolve each symbol's immutable strategy execution tick before reads."""
    if not isinstance(assignments, (tuple, list)) or not assignments:
        raise ValueError("Strategy 1 needs pinned execution tick assignments")
    ticks: dict[str, float] = {}
    for assignment in assignments:
        if (not isinstance(assignment, StrategyAssignment)
                or (assignment.strategy_id, assignment.strategy_revision)
                != (STRATEGY_ID, assignments[0].strategy_revision)):
            raise ValueError("Strategy 1 execution tick has a foreign assignment")
        resolve_numbered_fixed_strategy(assignment.strategy_id, assignment.strategy_revision)
        execution = assignment.parameters.get("execution")
        raw = execution.get("tick_size") if isinstance(execution, dict) else None
        if type(raw) not in (int, float) or not isfinite(raw) or raw <= 0:
            raise ValueError("Strategy 1 execution tick is missing or invalid")
        tick = float(raw)
        prior = ticks.setdefault(assignment.ticker, tick)
        if prior != tick:
            raise ValueError("Strategy 1 accounts disagree on ticker execution tick")
    return ticks


async def run_certified_strategy_one_session(
    *, market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    activations: CertifiedActivationPlan | None, pivots: CertifiedPivotPlan | None,
    hod: CertifiedHodPlan | None, seeds: CertifiedSeedPlan | None,
    entry: CertifiedEntryEvidencePlan | None, prices: PriceLevelPlan,
    through_boundary_ms: int, runtime: Any,
    assignments: Sequence[StrategyAssignment],
    client_factory: Callable[[], Any],
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    manager_ready: Callable[[StrategyOneManagementRunner], None] | None = None,
    first_price_ready: Callable[[object], None] | None = None,
    max_workers: int = DEFAULT_SPARSE_READ_WORKERS,
    stage_time: Callable[[str, float], None] | None = None,
    interval_plan: CertifiedV7IntervalPlan | None = None,
    start_after_boundary_ms: int = 0,
    flat_start_boundary_ms: int = 0,
    resume_evidence_state: StrategyOneEvidenceState | None = None,
    resume_manager_state: StrategyOneManagementState | None = None,
) -> StrategyOneProposalCounts:
    """Compose the certified sparse route without legacy frames or events.

    Preflight must already have sealed every full-population input. Projection
    is only a run-horizon/read-I/O optimization; it cannot revise those seals.
    """
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or (bool(candidates.prepared) and (
                not isinstance(activations, CertifiedActivationPlan)
                or not isinstance(pivots, CertifiedPivotPlan)
                or not isinstance(hod, CertifiedHodPlan)
                or not isinstance(seeds, CertifiedSeedPlan)
                or not isinstance(entry, CertifiedEntryEvidencePlan)))
            or (not candidates.prepared and not (
                all(value is None for value in (
                    activations, pivots, hod, seeds, entry, interval_plan))
                or (isinstance(activations, CertifiedActivationPlan)
                    and isinstance(pivots, CertifiedPivotPlan)
                    and isinstance(hod, CertifiedHodPlan)
                    and isinstance(seeds, CertifiedSeedPlan)
                    and isinstance(entry, CertifiedEntryEvidencePlan))))
            or interval_plan is not None
            and not isinstance(interval_plan, CertifiedV7IntervalPlan)
            or not isinstance(prices, PriceLevelPlan)
            or len(market.sessions) != 1
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100
            or type(start_after_boundary_ms) is not int
            or not 0 <= start_after_boundary_ms <= through_boundary_ms
            or start_after_boundary_ms % 100
            or type(flat_start_boundary_ms) is not int
            or not 0 <= flat_start_boundary_ms < through_boundary_ms
            or flat_start_boundary_ms % 100
            or flat_start_boundary_ms and start_after_boundary_ms != 0
            or (start_after_boundary_ms == 0) != (resume_evidence_state is None)
            or (start_after_boundary_ms == 0) != (resume_manager_state is None)
            or resume_evidence_state is not None and (
                not isinstance(resume_evidence_state, StrategyOneEvidenceState)
                or resume_evidence_state.boundary_ms != start_after_boundary_ms)
            or resume_manager_state is not None and (
                not isinstance(resume_manager_state, StrategyOneManagementState)
                or resume_manager_state.boundary_ms != start_after_boundary_ms)
            or type(max_workers) is not int or not 1 <= max_workers <= 16
            or any(not callable(callback) for callback in (
                client_factory, before_boundary, finish_boundary))
            or manager_ready is not None and not callable(manager_ready)
            or first_price_ready is not None and not callable(first_price_ready)):
        raise ValueError("Strategy 1 session lacks pinned 100ms inputs")
    if flat_start_boundary_ms:
        active = getattr(getattr(runtime, "broker", None),
                         "financially_active_tickers", None)
        if not callable(active) or active() != ():
            raise RuntimeError("Strategy 1 flat start has active broker state")
    # A fresh intraday account skips financial history; a recovered account
    # instead carries the verified evidence and manager images above.
    start_after_boundary_ms = max(start_after_boundary_ms, flat_start_boundary_ms)
    ticks = pinned_strategy_one_ticks(assignments)
    visible = project_candidate_plan(
        candidates, through_boundary_ms=through_boundary_ms)
    if not visible.prepared:
        # The full product and its empty causal prefix have both been sealed.
        # At a flat start there is no broker boundary to replay, but runtime
        # still owns the typed terminal lifecycle/account snapshot. Never
        # turn an empty prefix into a full-universe market read.
        broker = getattr(runtime, "broker", None)
        active = getattr(broker, "financially_active_tickers", None)
        if not callable(active) or active() != ():
            raise RuntimeError(
                "Strategy 1 empty candidate horizon has active broker state")
        evidence = StrategyOneEmptyEvidence()
        if resume_evidence_state is not None:
            evidence.restore_recovery_state(resume_evidence_state)
        elif flat_start_boundary_ms:
            evidence.advance_empty_boundary(flat_start_boundary_ms)
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=evidence,
            tick_for_ticker=ticks.__getitem__)
        if resume_manager_state is not None:
            if (resume_manager_state.submitted or resume_manager_state.positions
                    or resume_manager_state.pending_breaks
                    or resume_manager_state.position_highs
                    or resume_manager_state.closed_positions):
                raise RuntimeError("Empty Strategy 1 prefix has recovered financial state")
            manager.restore_state(resume_manager_state)
        if manager_ready is not None:
            manager_ready(manager)
        return StrategyOneProposalCounts(0, 0, 0, 0)
    visible_activations = project_activation_plan(
        activations, candidates, through_boundary_ms=through_boundary_ms)
    momentum_plan = None
    initial_momentum_plan = None
    if runtime.config.strategy_revision in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
        from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
        base_gate = compile_static_entry_gate(visible, entry, strategy_number=12)
        with closing(client_factory()) as momentum_client:
            momentum_plan = load_rising_momentum_plan(
                market, visible, client=momentum_client,
                candidate_indices=base_gate.eligible_indices)
        if runtime.config.strategy_revision == 18:
            from src.backend.backtest_strategy_initial_momentum import compile_initial_momentum_plan
            initial_momentum_plan = compile_initial_momentum_plan(visible, entry, momentum_plan)
        elif runtime.config.strategy_revision in (19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
            from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
            if runtime.config.strategy_revision in (26, 27, 28, 29, 30, 31, 32, 33):
                from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
                initial_momentum_plan = compile_initial_ten_percent_plan(visible, entry, momentum_plan)
            else:
                initial_momentum_plan = compile_initial_momentum_growth_plan(visible, entry, momentum_plan)
            if runtime.config.strategy_revision in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
                from src.backend.backtest_strategy_first_price_source import load_first_price_source
                from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
                with closing(client_factory()) as price_client:
                    source = load_first_price_source(market, initial_momentum_plan, client=price_client)
                initial_momentum_plan = compile_certified_price_break_plan(source)
    if runtime.config.strategy_revision in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
        from src.backend.backtest_strategy_certified_price_break import (
            compile_certified_price_static_gate, CertifiedPriceReadbackAuthority,
        )
        if not callable(first_price_ready):
            raise ValueError("Strategy20 session lacks its price-source publication binding")
        price_authority = CertifiedPriceReadbackAuthority(runtime.run_id, initial_momentum_plan)
        runtime.bind_strategy_one_price_source(price_authority)
        first_price_ready(price_authority)
        full_gate = compile_certified_price_static_gate(initial_momentum_plan)
    else:
        full_gate = compile_static_entry_gate(
            visible, entry, strategy_number=runtime.config.strategy_revision,
            momentum_plan=momentum_plan, initial_momentum_plan=initial_momentum_plan)
    survivors, activation_schedule = project_static_survivors(
        visible, visible_activations, full_gate)
    # The scheduler sees only survivors. Its local gate must index exactly
    # those rows, while the full mask remains a separate certified reduction.
    surviving_facts = tuple(full_gate.facts[index]
                            for index in full_gate.eligible_indices
                            if start_after_boundary_ms == 0 or
                            full_gate.facts[index].boundary_ms > start_after_boundary_ms)
    surviving_gate = StrategyOneStaticGate(
        surviving_facts, np.zeros(len(surviving_facts), dtype=np.uint8),
        np.arange(len(surviving_facts), dtype=np.int64))
    # Only episodes with a surviving future candidate need historical
    # activation evidence. Their original timestamps and certified expiry
    # facts remain intact; warming evidence never reactivates an episode.
    future_episodes = ({(fact.ticker, fact.episode_start_ms)
                        for fact in surviving_facts}
                       if flat_start_boundary_ms else set())
    warm_activations = tuple(row for row in activation_schedule.rows
                            if row.boundary_ms <= flat_start_boundary_ms
                            and (row.ticker, row.boundary_ms) in future_episodes)
    selected = tuple(row.ticker for row in visible.prepared)
    if set(selected) - ticks.keys():
        raise ValueError("Strategy 1 candidate lacks a pinned execution tick")
    projected = project_market_day_plan(market, selected)
    evidence_market = project_market_day_plan(
        market, tuple(row.ticker for row in candidates.prepared))
    projected_prices = prices.projected(projected)
    sparse_started = perf_counter() if stage_time is not None else 0.0
    scheduler = build_certified_strategy_one_scheduler(
        projected, survivors, activations=activation_schedule,
        price_plan=projected_prices, through_boundary_ms=through_boundary_ms,
        client_factory=client_factory, max_workers=max_workers,
        activation_source_candidates=visible, stage_time=stage_time,
        start_after_boundary_ms=start_after_boundary_ms)
    contract = resolve_numbered_fixed_strategy(
        assignments[0].strategy_id, assignments[0].strategy_revision)
    if contract.allows_session_exit:
        scheduler.install_session_clocks(tuple(boundary for boundary in (
            19_500_000, 19_740_000, 19_800_000, 57_000_000, 57_300_000, 57_600_000)
            if start_after_boundary_ms < boundary <= through_boundary_ms))
    if stage_time is not None:
        stage_time("strategy_one_sparse_load", sparse_started)
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
            evidence_started = perf_counter() if stage_time is not None else 0.0
            evidence = StrategyOneCausalEvidence(
                # V7 seeds, pivots and HOD cover all certified candidates,
                # not only the shortened run horizon. Keep that exact scope
                # together; only scheduler I/O is horizon-projected.
                market_plan=evidence_market, seed_plan=seeds,
                pivot_plan=pivots, hod_plan=hod,
                session=date.fromisoformat(projected.sessions[0]), client=reader,
                interval_plan=interval_plan,
                precomputed_entry_facts=True,
                stage_time=stage_time)
            if stage_time is not None:
                stage_time("strategy_one_evidence_init", evidence_started)
            activation_started = perf_counter() if stage_time is not None else 0.0
            await asyncio.to_thread(
                evidence.v7.preload_activation_seconds,
                tuple((row.ticker, row.boundary_ms)
                      for row in activation_schedule.rows
                      if row.boundary_ms > start_after_boundary_ms) +
                tuple((row.ticker, row.boundary_ms) for row in warm_activations) +
                tuple((fact.ticker, fact.boundary_ms)
                      for fact in surviving_gate.facts
                      if fact.boundary_ms > start_after_boundary_ms),
                client_factory=client_factory, max_workers=8)
            if stage_time is not None:
                stage_time("strategy_one_v7_activation_preload", activation_started)
            if len(selected) <= 64:
                seed_started = perf_counter() if stage_time is not None else 0.0
                await asyncio.to_thread(
                    evidence.v7.preload_seeds, selected,
                    client_factory=client_factory,
                    # Seed construction is read-only and ticker-independent.
                    # Keep it bounded separately from the four-lane market
                    # scheduler so cold start uses the workstation's CPUs.
                    max_workers=min(8, len(selected)))
                if stage_time is not None:
                    stage_time("strategy_one_v7_seed_preload", seed_started)
            if resume_evidence_state is not None:
                active = runtime.broker.financially_active_tickers()
                await evidence.restore_recovery_state(
                    resume_evidence_state, financially_active_tickers=active)
            elif flat_start_boundary_ms:
                # Certified V7 intervals already include prior-session seeds
                # and completed intraday bars. Read their causal geometry at
                # each activation; never run broker/strategy warmup trades.
                for activation in warm_activations:
                    await evidence.observe_activation(activation)
                evidence.advance_empty_boundary(flat_start_boundary_ms)
            manager = StrategyOneManagementRunner(
                runtime=runtime, evidence=evidence,
                tick_for_ticker=ticks.__getitem__)
            if resume_manager_state is not None:
                if runtime.config.strategy_revision in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
                    manager.restore_state(resume_manager_state, first_price_source=price_authority)
                else:
                    manager.restore_state(resume_manager_state)
            if manager_ready is not None:
                manager_ready(manager)
            return await run_strategy_one_fixed_session(
                scheduler, entry, evidence, manager, runtime=runtime,
                static_gate=surviving_gate, assignments=assignments, momentum_plan=momentum_plan,
                initial_momentum_plan=initial_momentum_plan,
                market_plan=projected, client_factory=client_factory,
                before_boundary=before_boundary,
                finish_boundary=finish_boundary,
                stage_time=stage_time)
        finally:
            scheduler.close()


async def run_strategy_one_fixed_session(
    scheduler: StrategyOneBoundaryScheduler,
    entry: CertifiedEntryEvidencePlan, evidence: StrategyOneCausalEvidence,
    manager: StrategyOneManagementRunner, *, runtime: Any,
    static_gate: StrategyOneStaticGate,
    momentum_plan=None,
    initial_momentum_plan=None,
    assignments: Sequence[StrategyAssignment],
    market_plan: CertifiedMarketDayPlan | None = None,
    client_factory: Callable[[], Any] | None = None,
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    stage_time: Callable[[str, float], None] | None = None,
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
            or config.strategy_revision not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33)
            or not callable(getattr(runtime, "process_liquidity_boundary", None))
            or not callable(getattr(broker, "financially_active_tickers", None))
            or not callable(getattr(broker, "positions", None))
            or not callable(getattr(order_manager, "snapshots", None))
            or not callable(finish_boundary)
            or not callable(before_boundary)
            or not isinstance(assignments, (tuple, list)) or not assignments
            or any(not isinstance(row, StrategyAssignment)
                   or (row.strategy_id, row.strategy_revision)
                   != (STRATEGY_ID, config.strategy_revision)
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

    async def observe_numbered_boundary(work: StrategyOneBoundaryWork) -> None:
        # Consume the bucket ending at the cutoff first. Cancel acquisition
        # remainder at its completed clock before any later bucket can fill.
        if config.strategy_revision in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33):
            await runtime.advance_numbered_session_clock(work.boundary_ms)
        await evidence.observe_completed_seconds(work)

    async def finish_numbered_boundary(work: StrategyOneBoundaryWork) -> None:
        await finish_boundary(work)
        if config.strategy_revision in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33) and work.boundary_ms in (19_800_000, 57_600_000):
            active = broker.financially_active_tickers()
            if active:
                raise RuntimeError(f"Strategy {config.strategy_revision} session ended with residual exposure/orders: {active}")

    async def financial_views(ticker: str, _boundary_ms: int):
        selected = by_ticker.get(ticker)
        if not selected:
            raise ValueError("Strategy 1 market ticker lacks a numbered assignment")
        return await read_strategy_one_financial_views(
            tuple(selected), broker, order_manager)

    candidate_boundaries: dict[str, list[int]] = defaultdict(list)
    for fact in static_gate.facts:
        candidate_boundaries[fact.ticker].append(fact.boundary_ms)
    prior_close_cache: dict[str, dict[int, int | None]] = {}
    # One lazy ASOF batch per ticker replaces an HTTP round trip per re-entry
    # candidate. The worker owns its read-only connection and exact attempt.
    prior_close_pool = ThreadPoolExecutor(max_workers=1,
                                          thread_name_prefix="s1-prior-close")
    prior_close_reader: Any | None = None

    def read_previous(ticker: str) -> dict[int, int | None]:
        nonlocal prior_close_reader
        if prior_close_reader is None:
            prior_close_reader = client_factory()
            if prior_close_reader is None or not callable(
                    getattr(prior_close_reader, "close", None)):
                raise TypeError("Strategy 1 prior-close reader must be closable")
        boundaries = tuple(sorted(set(candidate_boundaries.get(ticker, ()))))
        if not boundaries:
            raise ValueError("Strategy 1 re-entry lacks a sealed candidate batch")
        return load_previous_completed_100ms_closes_batch(
            market_plan, session_date=scheduler.session_date,
            ticker=ticker, boundaries_ms=boundaries,
            client=prior_close_reader)

    async def reentry_witness(financial, candidate):
        prior = manager.last_closed_position(financial)
        if prior is None or market_plan is None or client_factory is None:
            return None
        row = candidate.market_row
        current_close = row.get("close_int")
        if (type(current_close) is not int or current_close <= 0
                or row.get("ticker") != financial.ticker):
            raise ValueError("Strategy 1 re-entry lacks completed current close")
        ticker = financial.ticker
        boundary_ms = candidate.evidence.boundary_ms
        if ticker not in prior_close_cache:
            began = perf_counter() if stage_time is not None else 0.0
            prior_close_cache[ticker] = await asyncio.get_running_loop().run_in_executor(
                prior_close_pool, read_previous, ticker)
            if stage_time is not None:
                stage_time("strategy_one_reentry_previous_close", began)
        if boundary_ms not in prior_close_cache[ticker]:
            raise ValueError("Strategy 1 re-entry candidate was not in its sealed batch")
        previous_close = prior_close_cache[ticker][boundary_ms]
        if previous_close is None:
            return None
        return StrategyOneReentryWitness(
            prior.closed_boundary_ms, prior.entry_resistance_id,
            prior.high_int, previous_close, current_close)

    def financially_active_tickers() -> tuple[str, ...]:
        tickers = broker.financially_active_tickers()
        if (not isinstance(tickers, tuple)
                or any(ticker not in by_ticker for ticker in tickers)):
            raise RuntimeError("Strategy 1 broker owns an unassigned active ticker")
        return tickers

    try:
        return await run_strategy_one_proposals(
            scheduler, entry, process_broker_boundary=process_broker,
            before_boundary=before_boundary,
            financial_views=financial_views,
            on_entry_proposal=manager.on_entry_proposal,
            on_management=manager.on_management,
            reentry_witness=reentry_witness,
            position_source_owned=manager.owns_position_source,
            financially_active_tickers=financially_active_tickers,
            finish_boundary=finish_numbered_boundary,
            observe_activation=evidence.observe_activation,
            observe_completed_seconds=observe_numbered_boundary,
            static_gate=static_gate, stage_time=stage_time,
            strategy_number=config.strategy_revision, momentum_plan=momentum_plan,
            initial_momentum_plan=initial_momentum_plan)
    finally:
        try:
            if prior_close_reader is not None:
                await asyncio.get_running_loop().run_in_executor(
                    prior_close_pool, prior_close_reader.close)
        finally:
            prior_close_pool.shutdown(wait=True)
