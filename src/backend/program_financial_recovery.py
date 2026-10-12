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
        observations, strategy_id, strategy_revision, cutoff_at, account_ids):
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
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or type(protection) is not CompleteProtectionHistory
            or protection.run_id != prefix.run_id
            or protection.through_sequence != prefix.last_sequence
            or protection.committed_batch_ids != prefix.batch_ids
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not account_ids or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Program OMS recovery lacks one shared committed prefix")
    broker = verify_broker_match_snapshot(broker)
    observations = verify_oms_observation_snapshot(observations)
    root = broker.snapshot
    observed = observations.root
    if (root["run_id"] != prefix.run_id
            or root["checkpoint_sequence"] != prefix.last_sequence
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
        through_sequence=prefix.last_sequence, cutoff_at=cutoff_at)
    image = attach_typed_oms_observations(image, observations,
        through_sequence=prefix.last_sequence)
    verify_typed_oms_broker_open_orders(image, broker)
    return image


async def recover_program_portfolio_async(reader_factory, prefix, *, profiles, cutoff_at):
    """Read exact account revisions off-loop and reject movement of the fence."""
    profiles = tuple(profiles)
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or prefix.last_sequence < 1 or not prefix.batch_ids
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not profiles or any(type(row) is not PortfolioAccountProfile for row in profiles)
            or any(row.mode != "backtest" for row in profiles)
            or len({row.account_id for row in profiles}) != len(profiles)):
        raise ValueError("Program financial recovery requires a running typed backtest prefix")

    def read():
        client = reader_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed before cold read")
            for profile in profiles:
                snapshot = load_portfolio_snapshot(client, run_id=prefix.run_id,
                    account_id=profile.account_id, state_revision=prefix.last_sequence)
                if (snapshot is None or snapshot.get("state_revision") != prefix.last_sequence
                        or snapshot.get("snapshot_at") != cutoff_at.astimezone(timezone.utc).isoformat()):
                    raise ValueError("Program financial snapshot differs from its completed source clock")
            image = recover_portfolio_engine_state(client, run_id=prefix.run_id,
                profiles=profiles,
                state_revisions={row.account_id: prefix.last_sequence for row in profiles},
                cutoff_at=cutoff_at)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed during cold read")
            return image
        finally:
            client.close()

    return await _owned_worker(read)
