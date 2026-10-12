"""Owned cold reads of shared financial families for columnar programs.

These images preserve blocked admission until broker/OMS synchronization. They
are neither a strategy checkpoint nor permission to place orders.
"""
from datetime import datetime, timezone
from copy import deepcopy

from src.backend.qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
from src.trading_runtime.portfolio import PortfolioAccountProfile
from src.trading_runtime.arte_portfolio_snapshot import load_portfolio_snapshot


def qualify_program_financial_runtime(runtime, cold, prefix, broker, *, oms_image,
        through_sequence=None):
    """Prove broker resynchronization preserved the exact durable cold state.

    Returns a detached synchronized comparison image, not a new persistence
    authority or permission to execute. The broker checkpoint must already
    have its registered storage/owner receipt proved by the cold reader.
    """
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
    from src.trading_runtime.portfolio import PortfolioSyncState
    from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
    from src.trading_runtime.arte_oms_projection import freeze_oms_group
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        verify_broker_match_snapshot, project_broker_match_snapshot,
    )
    from src.backend.backtest_market_data import market_day_boundary
    if (type(prefix) is not V4CommittedPrefix or through_sequence is not None and (
            type(through_sequence) is not int or not 1 <= through_sequence <= prefix.last_sequence)):
        raise ValueError("Program financial qualification historical basis differs")
    basis_sequence = prefix.last_sequence if through_sequence is None else through_sequence
    return _compare_native_program_financial_image(runtime, cold, broker, oms_image=oms_image,
        run_id=prefix.run_id, basis_sequence=basis_sequence, running=prefix.status == "running")


def compare_captured_program_financial_runtime(runtime, cold, capture):
    """Compare staged native images; return no commit or decision permission."""
    from .program_clock_financial_basis import prepare_program_clock_financial_basis
    prepared = prepare_program_clock_financial_basis(capture)
    return _compare_native_program_financial_image(runtime, cold, prepared.broker,
        oms_image=capture.oms, run_id=capture.run_id,
        basis_sequence=capture.journal_sequence, running=True)


def _compare_native_program_financial_image(runtime, cold, broker, *, oms_image,
        run_id, basis_sequence, running):
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
    from src.trading_runtime.portfolio import PortfolioSyncState
    from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
    from src.trading_runtime.arte_oms_projection import freeze_oms_group
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        verify_broker_match_snapshot, project_broker_match_snapshot,
    )
    from src.backend.backtest_market_data import market_day_boundary
    if (type(runtime) is not TradingRuntime or type(cold) is not PortfolioRecovery
            or not running
            or type(oms_image) is not TypedOmsActorImage
            or (oms_image.run_id, oms_image.strategy_id, oms_image.strategy_revision)
               != (runtime.run_id, runtime.config.strategy_id, runtime.config.strategy_revision)
            or runtime.config.mode is not RunMode.BACKTEST
            or runtime.run_id != cold.run_id or cold.run_id != run_id
            or not runtime._typed_oms_recovered or runtime._canonical_session is None
            or set(runtime.portfolio.states) != set(cold.states)
            or set(cold.states) != set(cold.revisions)
            or any(value != basis_sequence for value in cold.revisions.values())
            or runtime.journal.latest_sequence(runtime.run_id) != basis_sequence
            or runtime.journal.pending_record_count):
        raise ValueError("Program financial qualification lacks its initialized exact prefix")
    manager = runtime.order_manager
    if (manager is None or set(manager._groups) != set(oms_image.groups)
            or manager._group_by_client_id != oms_image.group_by_client_id
            or manager._group_by_broker_id != oms_image.group_by_broker_id
            or manager._protection_versions != oms_image.protection_versions
            or manager._body_entry_group_ids != set(oms_image.body_entry_group_ids)
            or any(freeze_oms_group(group) != freeze_oms_group(oms_image.groups[identity])
                   or group.broker_order_state_fingerprints != oms_image.groups[identity].broker_order_state_fingerprints
                   for identity, group in manager._groups.items())):
        raise ValueError("Program financial qualification OMS image changed")
    broker = verify_broker_match_snapshot(broker)
    root = broker.snapshot
    at = market_day_boundary(root["session_date"], root["boundary_ms"])
    if (root["run_id"] != run_id or root["checkpoint_sequence"] != basis_sequence
            or runtime.last_event_time != at):
        raise ValueError("Program financial qualification clock differs")
    actual = project_broker_match_snapshot(run_id=run_id,
        session_date=at.date(), checkpoint_sequence=basis_sequence,
        boundary_ms=root["boundary_ms"], state=runtime.broker.checkpoint_state())
    if actual != broker:
        raise ValueError("Program financial qualification broker image changed")
    portfolio = runtime.portfolio
    if portfolio._last_filled_by_reservation != cold.last_filled_by_reservation:
        raise ValueError("Program financial qualification filled reservation cache changed")
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    reference = build_program_portfolio_engine(cold,
        journal=BacktestMemoryJournal(run_id=run_id, initial_sequence=basis_sequence),
        strategy_id=runtime.config.strategy_id, strategy_revision=runtime.config.strategy_revision,
        groups=tuple(portfolio.groups.values()), event_clock=lambda: at,
        drawdown_measure_policy=portfolio.drawdown_measure_policy)
    reference.reconcile_recovered_backtest_canonical(
        runtime._canonical_session.projector.snapshot(), completed_at=at)
    for account, cold_state in cold.states.items():
        if (cold_state.summary is not None or cold_state.ledger is not None
                or cold_state.positions or cold_state.open_orders
                or cold_state.sync_state is not PortfolioSyncState.ENTRIES_BLOCKED):
            raise ValueError("Program financial qualification requires blocked cold evidence")
        expected_state, actual_state = reference.states[account], portfolio.states[account]
        if any(getattr(actual_state, field) != getattr(expected_state, field) for field in
               ("summary", "ledger", "positions", "open_orders", "component_watermarks")):
            raise ValueError("Program financial qualification synchronized broker state changed")
        expected = reference.capture_recovery_snapshot(account,
            state_revision=basis_sequence, snapshot_at=at)
        current = portfolio.capture_recovery_snapshot(account,
            state_revision=basis_sequence, snapshot_at=at)
        if current != expected:
            raise ValueError("Program financial qualification durable Portfolio image changed")
    return PortfolioRecovery(run_id, dict(cold.revisions), deepcopy(portfolio.states),
        deepcopy(portfolio.reservations), deepcopy(portfolio.allocations),
        deepcopy(portfolio.differences), deepcopy(portfolio._last_filled_by_reservation))


def build_program_portfolio_engine(recovery, *, journal, strategy_id,
        strategy_revision, groups=(), event_clock, drawdown_measure_policy=None):
    """Construct the existing engine from a detached, blocked cold image.

    The readback object stays untouched while broker synchronization mutates
    the actor. Balances and positions must come from that separate broker
    synchronization, never from a convenient warm-state copy.
    """
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
    from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioSyncState
    if (type(recovery) is not PortfolioRecovery
            or type(journal) is not BacktestMemoryJournal or journal.run_id != recovery.run_id
            or not recovery.states or set(recovery.states) != set(recovery.revisions)
            or len(set(recovery.revisions.values())) != 1
            or any(type(value) is not int or value < 1 for value in recovery.revisions.values())
            or any(state.profile.mode != "backtest" or state.summary is not None
                   or state.ledger is not None or state.positions or state.open_orders
                   or state.sync_state not in {PortfolioSyncState.ENTRIES_BLOCKED,
                       PortfolioSyncState.FULLY_BLOCKED, PortfolioSyncState.DISABLED}
                   for state in recovery.states.values())):
        raise ValueError("Program Portfolio construction requires a blocked cold typed image")
    detached = deepcopy(recovery)
    return PortfolioManagementEngine(tuple(state.profile for state in detached.states.values()),
        journal=journal, run_id=detached.run_id, strategy_id=strategy_id,
        strategy_revision=strategy_revision, groups=groups, event_clock=event_clock,
        typed_recovery=detached, drawdown_measure_policy=drawdown_measure_policy)


def reconstruct_program_oms_image(prefix, *, lineages, protection, broker,
        observations, strategy_id, strategy_revision, cutoff_at, account_ids,
        through_sequence=None):
    """Join verified shared OMS families; never infer source intent authority.

    Lineages must already have their causal source and Portfolio admission
    proved by the caller. This checks checkpoint agreement and builds the inert
    OMS actor image; it does not install actors or enable execution.
    """
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    from src.trading_runtime.arte_oms_actor_restore import (
        reconstruct_typed_oms_actor_image, attach_typed_oms_observations,
        verify_typed_oms_broker_open_orders,
    )
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        verify_broker_match_snapshot,
    )
    from src.trading_runtime.strategy_one_oms_observation_snapshot import (
        verify_oms_observation_snapshot,
    )
    lineages = tuple(lineages)
    account_ids = tuple(account_ids)
    if (type(prefix) is not V4CommittedPrefix or through_sequence is not None and (
            type(through_sequence) is not int or not 1 <= through_sequence <= prefix.last_sequence)):
        raise ValueError("Program OMS recovery historical basis differs from verified prefix")
    basis_sequence = prefix.last_sequence if through_sequence is None else through_sequence
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or type(protection) is not CompleteProtectionHistory
            or protection.run_id != prefix.run_id
            or protection.through_sequence != basis_sequence
            or protection.committed_batch_ids != prefix.batch_ids
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not account_ids or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Program OMS recovery lacks one shared committed prefix")
    broker = verify_broker_match_snapshot(broker)
    from src.trading_runtime.arte_oms_observation_snapshot import (
        SharedOmsObservationSnapshot, verified_oms_observation_codec_rows,
    )
    observations = (verified_oms_observation_codec_rows(observations)
        if type(observations) is SharedOmsObservationSnapshot
        else verify_oms_observation_snapshot(observations))
    root = broker.snapshot
    observed = observations.root
    if (root["run_id"] != prefix.run_id
            or root["checkpoint_sequence"] != basis_sequence
            or {row["account_id"] for row in broker.accounts} != set(account_ids)
            or len(broker.accounts) != len(account_ids)
            or any(observed[key] != root[key] for key in
                   ("run_id", "checkpoint_sequence", "session_date", "boundary_ms"))
            or market_day_boundary(root["session_date"], root["boundary_ms"]) != cutoff_at):
        raise ValueError("Program OMS recovery checkpoint or account scope differs")
    if any(row.state.group["account_id"] not in account_ids for row in lineages):
        raise ValueError("Program OMS lineage contains a foreign account")
    image = reconstruct_typed_oms_actor_image(tuple(lineages), protection,
        run_id=prefix.run_id, strategy_id=strategy_id, strategy_revision=strategy_revision,
        through_sequence=basis_sequence, cutoff_at=cutoff_at)
    image = attach_typed_oms_observations(image, observations,
        through_sequence=basis_sequence)
    verify_typed_oms_broker_open_orders(image, broker)
    return image


async def recover_program_portfolio_async(reader_factory, prefix, *, profiles, cutoff_at,
        through_sequence=None):
    """Read exact account revisions off-loop and reject movement of the fence."""
    profiles = tuple(profiles)
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or prefix.last_sequence < 1 or not prefix.batch_ids
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not profiles or any(type(row) is not PortfolioAccountProfile for row in profiles)
            or any(row.mode != "backtest" for row in profiles)
            or len({row.account_id for row in profiles}) != len(profiles)):
        raise ValueError("Program financial recovery requires a running typed backtest prefix")
    if through_sequence is not None and (type(through_sequence) is not int
            or not 1 <= through_sequence <= prefix.last_sequence):
        raise ValueError("Program financial historical revision differs from verified prefix")
    basis_sequence = prefix.last_sequence if through_sequence is None else through_sequence

    def read():
        client = reader_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed before cold read")
            for profile in profiles:
                snapshot = load_portfolio_snapshot(client, run_id=prefix.run_id,
                    account_id=profile.account_id, state_revision=basis_sequence)
                if (snapshot is None or snapshot.get("state_revision") != basis_sequence
                        or snapshot.get("snapshot_at") != cutoff_at.astimezone(timezone.utc).isoformat()):
                    raise ValueError("Program financial snapshot differs from its completed source clock")
            image = recover_portfolio_engine_state(client, run_id=prefix.run_id,
                profiles=profiles,
                state_revisions={row.account_id: basis_sequence for row in profiles},
                cutoff_at=cutoff_at)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed during cold read")
            return image
        finally:
            client.close()

    return await _owned_worker(read)
