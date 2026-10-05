"""Causal Strategy 1 proposal dispatch over the certified sparse tape.

This layer joins a post-broker financial view to sealed entry facts. It does
not choose size, reserve cash, submit orders, or persist a journal. The caller
must supply the shared Portfolio/OMS authority for those steps. Rejected
candidate-only boundaries never wake the stateful evaluator; active financial
tickers still receive management callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter
from typing import Awaitable, Callable, Mapping

from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, StrategyOneBoundaryWork,
    run_strategy_one_boundaries,
)
from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
from src.backend.backtest_strategy_one_stateful import propose_certified_strategy_one_entry
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
    StrategyOneReentryWitness,
)


@dataclass(frozen=True, slots=True)
class StrategyOneProposalCounts:
    completed_boundaries: int
    candidate_decisions: int
    entry_proposals: int
    management_evaluations: int
    declared_entry_rejections: tuple[tuple[str, int], ...] = ()


async def run_strategy_one_proposals(
    scheduler: StrategyOneBoundaryScheduler,
    entry: CertifiedEntryEvidencePlan, *,
    process_broker_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    financial_views: Callable[[str, int], Awaitable[tuple[StrategyOneFinancialView, ...]]],
    on_entry_proposal: Callable[[StrategyOneEntryProposal], Awaitable[None]],
    on_management: Callable[[StrategyOneFinancialView, Mapping[int, Mapping], int], Awaitable[None]],
    position_source_owned: Callable[[StrategyOneFinancialView], bool],
    financially_active_tickers: Callable[[], tuple[str, ...]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    observe_activation: Callable[[object], Awaitable[None]],
    observe_completed_seconds: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    before_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]] | None = None,
    reentry_witness: Callable[[StrategyOneFinancialView, object],
                              Awaitable[StrategyOneReentryWitness | None]] | None = None,
    static_gate: StrategyOneStaticGate | None = None,
    stage_time: Callable[[str, float], None] | None = None,
    strategy_number: int = 1,
    momentum_plan=None,
    initial_momentum_plan=None,
    entry_spread_risk_source=None,
) -> StrategyOneProposalCounts:
    """Dispatch certified entry proposals after broker liquidity at each clock."""
    if (not isinstance(scheduler, StrategyOneBoundaryScheduler)
            or not isinstance(entry, CertifiedEntryEvidencePlan)
            or scheduler.session_date != entry.session_date
            or static_gate is not None
            and not isinstance(static_gate, StrategyOneStaticGate)
            or reentry_witness is not None and not callable(reentry_witness)
            or any(not callable(callback) for callback in (
                process_broker_boundary, financial_views, on_entry_proposal,
                on_management, position_source_owned, financially_active_tickers, finish_boundary,
                observe_activation, observe_completed_seconds))):
        raise ValueError("Strategy 1 proposal lane lacks pinned causal callbacks")
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(strategy_number)
    if contract.entry_spread_risk_policy is not None:
        from .backtest_declared_entry_quote_source import entry_spread_risk_authority_type
        if (type(entry_spread_risk_source) is not entry_spread_risk_authority_type(strategy_number)
                or entry_spread_risk_source.strategy_number != strategy_number
                or entry_spread_risk_source.plan.parent.activity.parent is not initial_momentum_plan):
            raise ValueError('Declared entry cost coordinator lacks independent full source')
    elif entry_spread_risk_source is not None:
        raise ValueError('Earlier coordinator cannot carry undeclared entry cost')
    if strategy_number in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58):
        from src.backend.backtest_strategy_rising_momentum import CertifiedRisingMomentumPlan
        if (not isinstance(momentum_plan, CertifiedRisingMomentumPlan)
                or momentum_plan.source_build_id != entry.source_build_id):
            raise ValueError("Strategy 13 coordinator lacks certified momentum source")
    if strategy_number in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58):
        from src.backend.backtest_strategy_initial_momentum import CertifiedInitialMomentumPlan
        from src.backend.backtest_strategy_initial_momentum_growth import CertifiedInitialMomentumGrowthPlan
        from src.backend.backtest_strategy_certified_price_break import CertifiedInitialPriceBreakPlan
        expected_type = (CertifiedInitialPriceBreakPlan
                         if strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58) else
                         CertifiedInitialMomentumGrowthPlan
                         if strategy_number == 19 else CertifiedInitialMomentumPlan)
        if (type(initial_momentum_plan) is not expected_type
                or initial_momentum_plan.entry is not entry
                or initial_momentum_plan.momentum is not momentum_plan):
            raise ValueError("Strategy 18 coordinator lacks certified first-setup selection")
    elif initial_momentum_plan is not None:
        raise ValueError("Earlier coordinator cannot carry initial selection")
    activations = {(row.ticker, row.episode_start_ms): row
                   for row in entry.activations}
    if len(activations) != len(entry.activations):
        raise ValueError("Strategy 1 entry activations are duplicated")
    candidate_count = proposal_count = management_count = 0

    async def evaluate(ticker: str, resolutions: Mapping[int, Mapping],
                       candidate) -> None:
        nonlocal candidate_count, proposal_count, management_count
        boundary = next(iter(resolutions.values()))["boundary_ms"]
        async def timed(stage: str, operation):
            if stage_time is None:
                return await operation
            started = perf_counter()
            try:
                return await operation
            finally:
                stage_time(stage, started)
        # One market row can serve several account assignments. The broker
        # runs once globally; financial decisions serialize by stable account
        # and assignment identity so shared cash cannot race across workers.
        async def current_views() -> dict[tuple[str, str], StrategyOneFinancialView]:
            views = await financial_views(ticker, boundary)
            if (not isinstance(views, tuple) or not views
                    or any(not isinstance(view, StrategyOneFinancialView)
                           or view.ticker != ticker for view in views)
                    or len({(view.account_id, view.assignment_id) for view in views})
                       != len(views)):
                raise ValueError("Strategy 1 ticker lacks distinct typed financial views")
            return {(view.account_id, view.assignment_id): view for view in views}

        if candidate is not None and entry_spread_risk_source is not None:
            entry_spread_risk_source.witness(ticker, boundary)
        current_by_id = await timed("strategy_one_financial_views", current_views())
        ordered_ids = tuple(sorted(current_by_id))
        if candidate is None:
            for index, identity in enumerate(ordered_ids):
                current = current_by_id[identity]
                if (ticker in scheduler.active_tickers
                        or current.position_quantity > 0 or current.pending_entry
                        or current.pending_exit or current.pending_capital_request):
                    management_count += 1
                    await timed("strategy_one_management", on_management(current, resolutions, boundary))
                    if index + 1 < len(ordered_ids):
                        refreshed = await timed("strategy_one_financial_views", current_views())
                        if set(refreshed) != set(ordered_ids):
                            raise ValueError("Strategy 1 assignment roster changed within boundary")
                        current_by_id = refreshed
            return
        row = candidate.market_row
        if row.get("ticker") != ticker or row.get("boundary_ms") != boundary:
            raise ValueError("Strategy 1 proposal candidate differs from broker clock")
        fact = entry.lookup(ticker, boundary)
        activation = activations.get((ticker, fact.episode_start_ms))
        if activation is None:
            raise ValueError("Strategy 1 proposal lacks frozen activation")
        for index, identity in enumerate(ordered_ids):
            current = current_by_id[identity]
            # A broker exit may have flattened this assignment earlier in
            # the same aggregate bucket. Retire its old source first, and
            # never order a new entry after an unordered same-bucket fill.
            owned = position_source_owned(current)
            if type(owned) is not bool:
                raise TypeError("Strategy 1 source ownership must be boolean")
            if owned:
                management_count += 1
                await timed("strategy_one_management", on_management(current, resolutions, boundary))
                if index + 1 < len(ordered_ids):
                    refreshed = await timed("strategy_one_financial_views", current_views())
                    if set(refreshed) != set(ordered_ids):
                        raise ValueError("Strategy 1 assignment roster changed within boundary")
                    current_by_id = refreshed
                continue
            if (not contract.entry_allowed(boundary)
                    or not contract.activation_allowed(boundary, fact.episode_start_ms)):
                if current.position_quantity > 0 or current.pending_entry or current.pending_exit:
                    management_count += 1
                    await timed("strategy_one_management", on_management(current, resolutions, boundary))
                continue
            reentry = (await timed("strategy_one_reentry", reentry_witness(current, candidate))
                       if current.completed_entries and reentry_witness is not None else None)
            if strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58):
                from src.backend.backtest_strategy_certified_price_break import propose_certified_price_entry
                decision = propose_certified_price_entry(initial_momentum_plan,
                    candidate, fact, activation, current, reentry=reentry,
                    strategy_number=strategy_number)
            else:
                decision = propose_certified_strategy_one_entry(
                    candidate, fact, activation, current,
                    strategy_number=strategy_number,
                    momentum=(momentum_plan.lookup(fact.ticker, fact.boundary_ms)
                              if strategy_number in (13, 14, 15, 16, 17, 18, 19) and momentum_plan is not None else None),
                    initial_momentum=(initial_momentum_plan.selection_witness(fact.ticker, fact.boundary_ms)
                                      if strategy_number in (18, 19) else None),
                    reentry=reentry)
            candidate_count += 1
            if decision.proposal is not None:
                proposal_count += 1
                await timed("strategy_one_entry_proposal", on_entry_proposal(
                    replace(decision.proposal, strategy_number=strategy_number)))
            elif current.position_quantity > 0 or ticker in scheduler.active_tickers:
                management_count += 1
                await timed("strategy_one_management", on_management(current, resolutions, boundary))
            else:
                continue
            if index + 1 < len(ordered_ids):
                refreshed = await timed("strategy_one_financial_views", current_views())
                if set(refreshed) != set(ordered_ids):
                    raise ValueError("Strategy 1 assignment roster changed within boundary")
                current_by_id = refreshed

    completed = await run_strategy_one_boundaries(
        scheduler, before_boundary=before_boundary,
        process_broker_boundary=process_broker_boundary,
        evaluate_ticker=evaluate,
        financially_active_tickers=financially_active_tickers,
        finish_boundary=finish_boundary,
        observe_activation=observe_activation,
        observe_completed_seconds=observe_completed_seconds,
        static_gate=static_gate, stage_time=stage_time)
    return StrategyOneProposalCounts(
        completed, candidate_count, proposal_count, management_count)
