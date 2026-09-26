"""Causal Strategy 1 proposal dispatch over the certified sparse tape.

This layer joins a post-broker financial view to sealed entry facts. It does
not choose size, reserve cash, submit orders, or persist a journal. The caller
must supply the shared Portfolio/OMS authority for those steps. Rejected
candidate-only boundaries never wake the stateful evaluator; active financial
tickers still receive management callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, StrategyOneBoundaryWork,
    run_strategy_one_boundaries,
)
from src.backend.backtest_strategy_one_stateful import propose_certified_strategy_one_entry
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
)


@dataclass(frozen=True, slots=True)
class StrategyOneProposalCounts:
    completed_boundaries: int
    candidate_decisions: int
    entry_proposals: int
    management_evaluations: int


async def run_strategy_one_proposals(
    scheduler: StrategyOneBoundaryScheduler,
    entry: CertifiedEntryEvidencePlan, *,
    process_broker_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    financial_view: Callable[[str, int], StrategyOneFinancialView],
    on_entry_proposal: Callable[[StrategyOneEntryProposal], Awaitable[None]],
    on_management: Callable[[str, Mapping[int, Mapping], int], Awaitable[None]],
    financially_active_tickers: Callable[[], tuple[str, ...]],
    finish_boundary: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
    observe_activation: Callable[[object], Awaitable[None]],
    observe_completed_seconds: Callable[[StrategyOneBoundaryWork], Awaitable[None]],
) -> StrategyOneProposalCounts:
    """Dispatch certified entry proposals after broker liquidity at each clock."""
    if (not isinstance(scheduler, StrategyOneBoundaryScheduler)
            or not isinstance(entry, CertifiedEntryEvidencePlan)
            or scheduler.session_date != entry.session_date
            or any(not callable(callback) for callback in (
                process_broker_boundary, financial_view, on_entry_proposal,
                on_management, financially_active_tickers, finish_boundary,
                observe_activation, observe_completed_seconds))):
        raise ValueError("Strategy 1 proposal lane lacks pinned causal callbacks")
    activations = {(row.ticker, row.episode_start_ms): row
                   for row in entry.activations}
    if len(activations) != len(entry.activations):
        raise ValueError("Strategy 1 entry activations are duplicated")
    candidate_count = proposal_count = management_count = 0

    async def evaluate(ticker: str, resolutions: Mapping[int, Mapping],
                       candidate) -> None:
        nonlocal candidate_count, proposal_count, management_count
        boundary = next(iter(resolutions.values()))["boundary_ms"]
        if candidate is None:
            management_count += 1
            await on_management(ticker, resolutions, boundary)
            return
        row = candidate.market_row
        if row.get("ticker") != ticker or row.get("boundary_ms") != boundary:
            raise ValueError("Strategy 1 proposal candidate differs from broker clock")
        fact = entry.lookup(ticker, boundary)
        activation = activations.get((ticker, fact.episode_start_ms))
        if activation is None:
            raise ValueError("Strategy 1 proposal lacks frozen activation")
        current = financial_view(ticker, boundary)
        decision = propose_certified_strategy_one_entry(
            candidate, fact, activation, current)
        candidate_count += 1
        if decision.proposal is not None:
            proposal_count += 1
            await on_entry_proposal(decision.proposal)
        elif current.position_quantity > 0:
            management_count += 1
            await on_management(ticker, resolutions, boundary)

    completed = await run_strategy_one_boundaries(
        scheduler, process_broker_boundary=process_broker_boundary,
        evaluate_ticker=evaluate,
        financially_active_tickers=financially_active_tickers,
        finish_boundary=finish_boundary,
        observe_activation=observe_activation,
        observe_completed_seconds=observe_completed_seconds)
    return StrategyOneProposalCounts(
        completed, candidate_count, proposal_count, management_count)
