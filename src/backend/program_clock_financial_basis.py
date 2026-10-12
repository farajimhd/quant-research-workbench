"""Native financial captures for future staged-clock qualification.

A capture is not a committed prefix or publication permission. The existing
qualification gate continues to require its independent financial proof.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from types import MappingProxyType
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchSnapshotRows
    from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot


@dataclass(frozen=True, slots=True)
class ProgramClockFinancialBasis:
    run_id: str
    journal_sequence: int
    boundary_at: datetime
    source_boundary: tuple[int, str, datetime]
    release_hash: str
    broker: BrokerMatchSnapshotRows
    portfolio: tuple[CapturedPortfolioSnapshot, ...]


async def capture_program_clock_financial_basis(controller, *, at):
    """Capture existing broker/Portfolio contracts without database I/O."""
    from .replay_run_service import ReplayRunController
    from .signal_stream_runtime_service import SignalStreamRuntime
    from .backtest_market_data import market_day_boundary
    from .qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    if type(controller) is not ReplayRunController:
        raise TypeError("Financial clock capture requires the native controller")
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Financial clock capture needs a timezone-aware boundary")
    runtime, journal, source = controller._runtime, controller._journal, controller._program_signal_runtime
    actor = controller._program_strategy
    if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
            or type(runtime.broker) is not SimulatedBrokerAdapter
            or runtime.journal is not journal or runtime.strategy is not actor
            or type(actor) is not AssignedProgramStrategy
            or runtime.run_id != controller.run_id or journal.run_id != controller.run_id
            or (runtime.config.strategy_id, runtime.config.strategy_revision) != (actor.strategy_id, actor.revision)
            or set(runtime.portfolio.states) != set(runtime.config.account_ids)
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or runtime.last_event_time != at or at.tzinfo is None):
        raise ValueError("Financial clock capture lacks its owned completed runtime")
    sequence = journal.latest_sequence(controller.run_id)
    boundary = source.typed_source_boundary(at)
    if sequence < 1 or boundary[0] < 1 or boundary[2] != at:
        raise ValueError("Financial clock capture differs from journal/source basis")
    day = controller.definition.session_date
    elapsed = (at - market_day_boundary(day, 0)).total_seconds() * 1000
    if elapsed != int(elapsed):
        raise ValueError("Financial clock capture has a fractional broker clock")
    state = runtime.broker.broker_match_snapshot_state()
    portfolio = tuple(runtime.portfolio.capture_recovery_snapshot(account,
        state_revision=sequence, snapshot_at=at) for account in runtime.config.account_ids)
    broker = await _owned_worker(project_broker_match_snapshot, run_id=controller.run_id,
        session_date=day, checkpoint_sequence=sequence, boundary_ms=int(elapsed), state=state)
    if (controller._runtime is not runtime or controller._journal is not journal
            or controller._program_strategy is not actor or controller._program_signal_runtime is not source
            or runtime.last_event_time != at or journal.latest_sequence(controller.run_id) != sequence
            or source.typed_source_boundary(at) != boundary):
        raise ValueError("Financial clock capture changed during projection")
    broker = replace(broker, snapshot=MappingProxyType(dict(broker.snapshot)), **{
        name: tuple(MappingProxyType(dict(row)) for row in getattr(broker, name))
        for name in ("accounts", "positions", "open_orders", "tickers", "marks")})
    return ProgramClockFinancialBasis(controller.run_id, sequence, at, boundary,
        actor.program_release.header["content_hash"], broker, portfolio)
