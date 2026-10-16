"""Native financial captures for future staged-clock qualification.

A capture is not a committed prefix or publication permission. The existing
qualification gate continues to require its independent financial proof.
"""
from __future__ import annotations
from dataclasses import dataclass, replace, field
from types import MappingProxyType
from datetime import datetime
from typing import TYPE_CHECKING
import re
from hashlib import sha256

_CAPTURE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _OwnedClockCapture:
    controller: object = field(repr=False)
    runtime: object = field(repr=False)
    journal: object = field(repr=False)
    actor: object = field(repr=False)
    source: object = field(repr=False)
    manager: object = field(repr=False)
    portfolio: object = field(repr=False)
    broker: object = field(repr=False)
    config: object = field(repr=False)
    digest: str
    token: object = field(repr=False)

if TYPE_CHECKING:
    from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchSnapshotRows
    from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot, PreparedPortfolioSnapshot
    from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage


@dataclass(frozen=True, slots=True)
class ProgramClockFinancialBasis:
    run_id: str
    journal_sequence: int
    boundary_at: datetime
    source_boundary: tuple[int, str, datetime]
    release_hash: str
    broker: BrokerMatchSnapshotRows
    portfolio: tuple[CapturedPortfolioSnapshot, ...]
    oms: TypedOmsActorImage
    policy_hash: str = ""
    _evidence: object = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PreparedProgramClockFinancialBasis:
    capture: ProgramClockFinancialBasis
    broker: BrokerMatchSnapshotRows
    portfolio: tuple[PreparedPortfolioSnapshot, ...]


def _clock_capture_digest(capture):
    from src.trading_runtime.journal_contract import canonical_json
    from src.trading_runtime.arte_oms_projection import freeze_oms_group
    from src.trading_runtime.arte_portfolio_snapshot import _snapshot_rows, _state_hash
    prepared = prepare_program_clock_financial_basis(capture)
    value = dict(run_id=capture.run_id, sequence=capture.journal_sequence,
        at=capture.boundary_at, source=capture.source_boundary, release=capture.release_hash,
        policy=capture.policy_hash, broker=prepared.broker.snapshot["content_hash"],
        portfolio=[(row.account_id, _state_hash(_snapshot_rows(row.run_id, row.account_id,
            row.state_revision, row.snapshot_month, row.rows))) for row in prepared.portfolio],
        oms=(capture.oms.run_id, capture.oms.strategy_id, capture.oms.strategy_revision,
            [(identity, freeze_oms_group(group), group.broker_order_state_fingerprints)
             for identity, group in sorted(capture.oms.groups.items())],
            capture.oms.group_by_client_id, capture.oms.group_by_broker_id,
            sorted(capture.oms.protection_versions.items()), sorted(capture.oms.body_entry_group_ids)))
    return sha256(canonical_json(value).encode()).hexdigest()


def verify_owned_program_clock_financial_basis(controller, capture):
    """Check capture custody/content without claiming a committed prefix."""
    if type(capture) is not ProgramClockFinancialBasis:
        raise TypeError("Owned clock verification requires its native capture")
    evidence = capture._evidence
    if (type(evidence) is not _OwnedClockCapture or evidence.token is not _CAPTURE_TOKEN
            or evidence.controller is not controller or controller._runtime is not evidence.runtime
            or controller._journal is not evidence.journal or controller._program_strategy is not evidence.actor
            or controller._program_signal_runtime is not evidence.source
            or evidence.runtime.order_manager is not evidence.manager
            or evidence.runtime.portfolio is not evidence.portfolio or evidence.runtime.broker is not evidence.broker
            or evidence.runtime.config is not evidence.config
            or evidence.runtime.last_event_time != capture.boundary_at
            or evidence.source.typed_source_boundary(capture.boundary_at) != capture.source_boundary
            or re.fullmatch(r"[0-9a-f]{64}", capture.policy_hash) is None
            or _clock_capture_digest(capture) != evidence.digest):
        raise ValueError("Owned financial clock capture changed or lost controller custody")
    return prepare_program_clock_financial_basis(capture)


def prepare_program_clock_financial_basis(capture):
    """Validate shared snapshot rows; this still grants no publication proof."""
    from .backtest_market_data import market_day_boundary
    from src.trading_runtime.strategy_one_broker_match_snapshot import verify_broker_match_snapshot
    from src.trading_runtime.arte_portfolio_snapshot import prepare_captured_portfolio_snapshot
    from src.trading_runtime.arte_oms_actor_restore import verify_typed_oms_broker_open_orders
    if type(capture) is not ProgramClockFinancialBasis:
        raise TypeError("Financial clock preparation requires its native capture")
    if (type(capture.journal_sequence) is not int or capture.journal_sequence < 1
            or len(capture.source_boundary) != 3 or type(capture.source_boundary[0]) is not int
            or capture.source_boundary[0] < 1 or capture.source_boundary[2] != capture.boundary_at
            or re.fullmatch(r"[0-9a-f]{64}", capture.source_boundary[1]) is None
            or re.fullmatch(r"[0-9a-f]{64}", capture.release_hash) is None):
        raise ValueError("Financial clock capture source scope differs")
    broker = verify_broker_match_snapshot(capture.broker)
    if capture.oms.run_id != capture.run_id:
        raise ValueError("Financial clock OMS scope differs from capture")
    verify_typed_oms_broker_open_orders(capture.oms, broker)
    root = broker.snapshot
    if (root["run_id"] != capture.run_id or root["checkpoint_sequence"] != capture.journal_sequence
            or market_day_boundary(root["session_date"], root["boundary_ms"]) != capture.boundary_at):
        raise ValueError("Financial clock broker rows differ from capture scope")
    accounts = {row["account_id"] for row in broker.accounts}
    if (not capture.portfolio or len({row.account_id for row in capture.portfolio}) != len(capture.portfolio)
            or {row.account_id for row in capture.portfolio} != accounts
            or any((row.run_id, row.state_revision, row.snapshot_at) !=
                   (capture.run_id, capture.journal_sequence, capture.boundary_at) for row in capture.portfolio)):
        raise ValueError("Financial clock Portfolio coverage differs from broker basis")
    prepared = tuple(prepare_captured_portfolio_snapshot(row) for row in capture.portfolio)
    broker = replace(broker, snapshot=MappingProxyType(dict(broker.snapshot)), **{
        name: tuple(MappingProxyType(dict(row)) for row in getattr(broker, name))
        for name in ("accounts", "positions", "open_orders", "tickers", "marks")})
    return PreparedProgramClockFinancialBasis(capture, broker, prepared)


async def capture_program_clock_financial_basis(controller, *, at):
    """Refresh passive native marks and capture the financial basis in memory."""
    from .replay_run_service import ReplayRunController
    from .signal_stream_runtime_service import SignalStreamRuntime
    from .backtest_market_data import market_day_boundary
    from .qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from src.trading_runtime.arte_oms_actor_restore import capture_typed_oms_actor_image
    from src.trading_runtime.canonical_session import CanonicalBrokerSession
    from .program_policy_recovery import program_policy_fingerprint
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
    manager, portfolio_engine, native_broker = runtime.order_manager, runtime.portfolio, runtime.broker
    config, canonical = runtime.config, runtime._canonical_session
    if type(canonical) is not CanonicalBrokerSession:
        raise ValueError("Financial clock capture lacks its native canonical broker session")
    async with runtime._portfolio_sync_lock:
        # Fills already enter the native canonical projector at liquidity time.
        # Passive price changes still change equity between fills. Refresh the
        # current broker image without rescanning all historical executions or
        # adding a journal event before the captured policy sequence. The exact
        # refreshed Portfolio image is part of the qualified decision receipt.
        await canonical.reconcile(include_executions=False)
        if (controller._runtime is not runtime or controller._journal is not journal
                or controller._program_strategy is not actor or controller._program_signal_runtime is not source
                or runtime.order_manager is not manager or runtime.portfolio is not portfolio_engine
                or runtime.broker is not native_broker or runtime.config is not config
                or runtime._canonical_session is not canonical or runtime.last_event_time != at
                or journal.latest_sequence(controller.run_id) != sequence
                or source.typed_source_boundary(at) != boundary):
            raise ValueError("Financial clock ownership changed during broker refresh")
        snapshot = canonical.projector.snapshot()
        if snapshot.as_of > at:
            raise ValueError("Financial clock capture cannot use a future broker snapshot")
        portfolio_engine.synchronize_canonical(snapshot, persist=False)
    day = controller.definition.session_date
    elapsed = (at - market_day_boundary(day, 0)).total_seconds() * 1000
    if elapsed != int(elapsed):
        raise ValueError("Financial clock capture has a fractional broker clock")
    state = native_broker.broker_match_snapshot_state()
    portfolio = tuple(portfolio_engine.capture_recovery_snapshot(account,
        state_revision=sequence, snapshot_at=at) for account in runtime.config.account_ids)
    oms = capture_typed_oms_actor_image(manager)
    policy_hash = program_policy_fingerprint(actor)
    if (oms.run_id, oms.strategy_id, oms.strategy_revision) != (
            runtime.run_id, runtime.config.strategy_id, runtime.config.strategy_revision):
        raise ValueError("Financial clock OMS identity differs from runtime")
    broker = await _owned_worker(project_broker_match_snapshot, run_id=controller.run_id,
        session_date=day, checkpoint_sequence=sequence, boundary_ms=int(elapsed), state=state)
    if (controller._runtime is not runtime or controller._journal is not journal
            or controller._program_strategy is not actor or controller._program_signal_runtime is not source
            or runtime.order_manager is not manager or runtime.portfolio is not portfolio_engine
            or runtime.broker is not native_broker or runtime.config is not config
            or runtime.last_event_time != at or journal.latest_sequence(controller.run_id) != sequence
            or source.typed_source_boundary(at) != boundary):
        raise ValueError("Financial clock capture changed during projection")
    broker = replace(broker, snapshot=MappingProxyType(dict(broker.snapshot)), **{
        name: tuple(MappingProxyType(dict(row)) for row in getattr(broker, name))
        for name in ("accounts", "positions", "open_orders", "tickers", "marks")})
    capture = ProgramClockFinancialBasis(controller.run_id, sequence, at, boundary,
        actor.program_release.header["content_hash"], broker, portfolio, oms, policy_hash)
    digest = await _owned_worker(_clock_capture_digest, capture)
    if (controller._runtime is not runtime or controller._journal is not journal
            or controller._program_strategy is not actor or controller._program_signal_runtime is not source
            or runtime.order_manager is not manager or runtime.portfolio is not portfolio_engine
            or runtime.broker is not native_broker or runtime.config is not config
            or source.typed_source_boundary(at) != boundary
            or runtime.last_event_time != at or journal.latest_sequence(controller.run_id) != sequence
            or program_policy_fingerprint(actor) != policy_hash):
        raise ValueError("Financial clock capture changed during sealing")
    return replace(capture, _evidence=_OwnedClockCapture(controller, runtime, journal,
        actor, source, manager, portfolio_engine, native_broker, config, digest, _CAPTURE_TOKEN))
