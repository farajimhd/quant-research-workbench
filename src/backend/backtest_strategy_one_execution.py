"""Bind the numbered Strategy 1 sparse tape to shared Backtest authorities.

No frame spool, market builder, per-row indicator calculation, SQLite, or
disk journal belongs in this lane. Market inputs are preflight-certified
persisted products; the broker and Portfolio/OMS alone mutate financial state.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Awaitable, Callable, Sequence

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_coordinator import (
    StrategyOneProposalCounts, run_strategy_one_proposals,
)
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_views
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_strategy_one_scheduler import (
    StrategyOneBoundaryScheduler, StrategyOneBoundaryWork,
)
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


async def run_strategy_one_fixed_session(
    scheduler: StrategyOneBoundaryScheduler,
    entry: CertifiedEntryEvidencePlan, evidence: StrategyOneCausalEvidence,
    manager: StrategyOneManagementRunner, *, runtime: Any,
    assignments: Sequence[StrategyAssignment],
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
        financial_views=financial_views,
        on_entry_proposal=manager.on_entry_proposal,
        on_management=manager.on_management,
        financially_active_tickers=financially_active_tickers,
        finish_boundary=finish_boundary,
        observe_activation=evidence.observe_activation,
        observe_completed_seconds=evidence.observe_completed_seconds)
