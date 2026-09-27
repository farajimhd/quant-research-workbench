"""Inactive live typed-sync bootstrap and cold fence; no service cutover.

The fresh-run operation atomically creates both Keeper transport gates. A
legacy run, including one missing either gate, cannot be repaired by this API.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import re
from typing import Any, Mapping

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration, certify_strategy_one_configuration,
)
from src.backend.live_strategy_one_approval import (
    ApprovalHeadReader, verify_selected_approval,
)
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _RUN_CONFIG_FIELDS, _literal, _rows, _wire_row,
    load_typed_run_context, publish_typed_run, publish_typed_run_context,
    typed_row,
)
from src.trading_runtime.arte_live_run_allocation import LiveRunAllocation, LiveRunAllocator
from src.trading_runtime.arte_portfolio_sync import (
    audit_attested_portfolio_sync_transitions,
    load_attested_portfolio_sync_transition,
)
from src.trading_runtime.arte_portfolio_sync_dispatch import (
    PortfolioSyncDispatch, SyncColdBarrier, _Gate as _SyncGate,
    _gate_path as _sync_gate_path,
)
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, _Gate as _CoreGate, _ZERO_BATCH, _ZERO_HASH,
    _gate_path as _core_gate_path,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _ROOT, _committed, _identity
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_EXISTING_TABLES = (
    "trading_run_v1", "trading_run_context_commit_v1", "trading_commit_v1",
    "trading_portfolio_sync_snapshot_marker_v1", "trading_portfolio_sync_fence_v1",
)


def _run_fact_tables(client: Any) -> tuple[str, ...]:
    """Inventory every installed normalized trading family with run identity.

    A stale child fact can exist without a run/context row after an interrupted
    insert. Checking only the core tables would let a fresh allocation collide
    with that orphan; new typed families must be covered automatically.
    """
    rows = _rows(client,
        "SELECT table FROM system.columns WHERE database='arte' "
        "AND startsWith(table,'trading_') AND name='run_id' "
        "ORDER BY table FORMAT JSONEachRow")
    names = tuple(str(row.get("table") or "") for row in rows)
    if (len(names) != len(set(names)) or tuple(sorted(names)) != names
            or not set(_EXISTING_TABLES) <= set(names)
            or any(set(row) != {"table"} or
                   re.fullmatch(r"trading_[a-z0-9_]+", name) is None
                   for row, name in zip(rows, names))):
        raise KeeperUnavailable("Live run fact-table inventory is incomplete or ambiguous")
    return names


def validate_new_strategy_one_live_context(
    run: Mapping[str, Any], config: Mapping[str, Any],
    account_ids: tuple[str, ...], release: CertifiedStrategyOneConfiguration,
) -> str:
    """Reject incompatible typed facts before creating Keeper gates or CH rows.

    This only validates a proposed context. Operator approval, allocation,
    broker reconciliation and durable journal receipts are separate gates.
    """
    if (not isinstance(release, CertifiedStrategyOneConfiguration)
            or not isinstance(run, Mapping) or not isinstance(config, Mapping)
            or set(run) != {name for name, _ in _CONTRACTS["trading_run_v1"].columns}
            or set(config) != _RUN_CONFIG_FIELDS):
        raise ValueError("Strategy 1 live context is not fully typed")
    strategy = release.payload.get("strategy")
    plan = release.payload.get("run_plan")
    if (not isinstance(strategy, Mapping) or not isinstance(plan, Mapping)
            or strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("revision") != STRATEGY_NUMBER
            or strategy.get("execution_interval") != "100ms"
            or run.get("mode") != "live"
            or run.get("evaluation_interval_ms") != 100
            or run.get("configuration_hash") != release.payload_hash
            or config.get("strategy_id") != STRATEGY_ID
            or type(config.get("strategy_revision")) is not int
            or config["strategy_revision"] != STRATEGY_NUMBER
            or config.get("run_plan_id") != plan.get("run_plan_id")
            or config.get("anchor_date") != run.get("session_date")
            or config.get("safety_supervisor_enabled") is not True
            or type(config.get("checkpoint_interval_events")) is not int
            or config["checkpoint_interval_events"] < 1
            or any(type(config.get(key)) is not bool for key in (
                "safety_supervisor_enabled", "write_progress_checkpoints"))
            or type(account_ids) is not tuple or not 1 <= len(account_ids) <= 65535
            or any(type(account) is not str or not account.strip()
                   for account in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Strategy 1 live run differs from its numbered release")
    if (not isinstance(run.get("run_id"), str) or not run["run_id"].startswith("live:v2:")
            or any(re.fullmatch(r"[0-9a-f]{64}", str(run.get(field))) is None
                   for field in ("configuration_hash", "code_hash"))):
        raise ValueError("Strategy 1 live run identity or code hash is invalid")
    wire = _wire_row("trading_run_v1", run)
    if wire["run_month"] != wire["started_at"][:7] + "-01":
        raise ValueError("Strategy 1 live run month differs from UTC start")
    typed_row("trading_runtime_config_v1", {
        "run_id": run["run_id"], "run_month": wire["run_month"],
        **{key: (int(value) if key in {
            "safety_supervisor_enabled", "write_progress_checkpoints"} else value)
           for key, value in config.items()},
    })
    for ordinal, account in enumerate(account_ids):
        typed_row("trading_run_account_v1", {
            "run_id": run["run_id"], "run_month": wire["run_month"],
            "ordinal": ordinal, "account_id": account,
        })
    return run["run_id"]


def initialize_new_live_sync_run(*, run_id: str, writer_client: Any,
                                 read_client: Any,
                                 owner_id: str,
                                 core_dispatch: TypedInsertDispatch,
                                 sync_dispatch: PortfolioSyncDispatch,
                                 allocator: LiveRunAllocator,
                                 allocation: LiveRunAllocation) -> None:
    """Create two gates in one Keeper CAS, only for an absent live run ID.

    This is not a live launcher. Caller must provide an independently allocated
    never-reused run ID and publish/verify typed run context before admission.
    """
    _identity(run_id, "run")
    _identity(owner_id, "owner")
    if (not run_id.startswith("live:") or writer_client is read_client
            or not isinstance(core_dispatch, TypedInsertDispatch)
            or not isinstance(sync_dispatch, PortfolioSyncDispatch)
            or not isinstance(allocator, LiveRunAllocator)
            or not isinstance(allocation, LiveRunAllocation)
            or allocation.run_id != run_id
            or allocation.owner_id != owner_id
            or core_dispatch.keeper is not sync_dispatch.keeper
            or allocator.keeper is not core_dispatch.keeper
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or getattr(writer_client, "typed_insert_dispatch", None) is not core_dispatch
            or getattr(writer_client, "typed_sync_insert_dispatch", None) is not None):
        raise ValueError("Fresh live sync requires distinct strict typed authorities")
    allocator.assert_status(allocation, "allocated")
    for table in _run_fact_tables(read_client):
        rows = _rows(read_client,
            f"SELECT run_id FROM arte.{table} WHERE run_id={_literal(run_id)} "
            "LIMIT 1 FORMAT JSONEachRow")
        if rows:
            raise KeeperUnavailable("Live run ID already has ClickHouse facts")
    keeper = core_dispatch.keeper
    for family in ("typed_dispatch_gate", "typed_dispatch_operation",
                   "typed_dispatch_context_receipt", "typed_dispatch_terminal_receipt",
                   "typed_dispatch_snapshot_head", "typed_dispatch_policy_gate",
                   "portfolio_sync_dispatch"):
        keeper.ensure_path(f"{_ROOT}/{family}")
    txn = keeper.transaction()
    txn.create(_core_gate_path(run_id), _CoreGate(
        "open", 0, 1, 0, 0, _ZERO_BATCH, _ZERO_HASH, _ZERO_BATCH).wire(),
        ephemeral=False)
    txn.create(_sync_gate_path(run_id), _SyncGate().wire(), ephemeral=False)
    allocator.add_gate_binding(txn, allocation)
    try:
        committed = _committed(txn.commit())
    except Exception as exc:
        raise KeeperUnavailable("Fresh live run gate outcome is ambiguous") from exc
    if not committed:
        raise KeeperUnavailable("Fresh live run gates already exist or CAS conflicted")
    core_dispatch._read_gate(run_id)
    sync_dispatch._read(run_id)
    writer_client.typed_sync_insert_dispatch = sync_dispatch


def publish_new_strategy_one_live_context(
    *, run: Mapping[str, Any], config: Mapping[str, Any],
    account_ids: tuple[str, ...], release: CertifiedStrategyOneConfiguration,
    approval_reader: ApprovalHeadReader, writer_client: Any,
    read_client: Any, terminal_client: Any, owner_id: str,
    core_dispatch: TypedInsertDispatch, sync_dispatch: PortfolioSyncDispatch,
    allocator: LiveRunAllocator, allocation: LiveRunAllocation,
) -> LiveRunAllocation:
    """Publish one approved typed live context, without enabling trading.

    Every irreversible operation follows local validation and a Keeper-selected
    approval read. Any ambiguous INSERT or changed approval leaves the run
    fenced for operator reconciliation; it is never retried under a new ID.
    """
    run_id = validate_new_strategy_one_live_context(
        run, config, account_ids, release)
    if (len({id(writer_client), id(read_client), id(terminal_client)}) != 3
            or not isinstance(allocation, LiveRunAllocation)
            or run_id != allocation.run_id or owner_id != allocation.owner_id):
        raise ValueError("Strategy 1 live publication lacks distinct allocated authorities")
    if certify_strategy_one_configuration(read_client) != release:
        raise ValueError("Strategy 1 live release differs from certified ClickHouse rows")
    selected = verify_selected_approval(
        read_client, approval_reader, mode="live", release=release)
    initialize_new_live_sync_run(
        run_id=run_id, writer_client=writer_client,
        read_client=read_client, owner_id=owner_id,
        core_dispatch=core_dispatch, sync_dispatch=sync_dispatch,
        allocator=allocator, allocation=allocation)
    publish_typed_run(writer_client, run)
    publish_typed_run_context(
        writer_client, run_id=run_id, config=config, account_ids=account_ids)
    bound = allocator.bind_context(
        read_client, core_dispatch, replace(allocation, status="gates_bound"))
    context = allocator.verify_context_bound(read_client, core_dispatch, bound)
    if (context != load_typed_run_context(terminal_client, run_id)
            or context.get("mode") != "live"
            or tuple(context.get("account_ids") or ()) != account_ids
            or certify_strategy_one_configuration(read_client) != release
            or verify_selected_approval(
                read_client, approval_reader, mode="live", release=release) != selected):
        raise KeeperUnavailable("Strategy 1 live context or approval changed after publication")
    return bound


@dataclass(frozen=True)
class LiveSyncColdResult:
    run_id: str
    transition_count: int
    context: dict[str, Any]
    prefix: Any
    barrier: SyncColdBarrier


def verify_live_sync_cold_start(*, run_id: str, read_client: Any,
                                core_dispatch: TypedInsertDispatch,
                                sync_dispatch: PortfolioSyncDispatch,
                                keeper: Any, allocator: LiveRunAllocator,
                                allocation: LiveRunAllocation) -> LiveSyncColdResult:
    """Close sync first, then core; verify both before reading transitions.

    No gate is reopened on failure. Broker reconciliation and every other live
    typed family remain separate prerequisites for actual order admission.
    """
    _identity(run_id, "run")
    if (not isinstance(core_dispatch, TypedInsertDispatch)
            or not isinstance(sync_dispatch, PortfolioSyncDispatch)
            or not isinstance(allocator, LiveRunAllocator)
            or core_dispatch.keeper is not sync_dispatch.keeper
            or allocator.keeper is not core_dispatch.keeper
            or allocation.run_id != run_id):
        raise ValueError("Live sync cold audit needs one Keeper transport")
    allocator.assert_status(allocation, "context_bound")
    sync_dispatch.close_for_cold(run_id)
    base = core_dispatch.acquire_cold_barrier(run_id)
    context = base.verify_run_context_receipt(read_client)
    if allocator.verify_context_bound(read_client, core_dispatch, allocation) != context:
        raise KeeperUnavailable("Allocated run context differs from cold context")
    if context.get("mode") != "live":
        raise KeeperUnavailable("Cold sync run context is not live")
    prefix = base.verify_committed_prefix(read_client, journal_profile="v1")
    barrier = SyncColdBarrier(sync_dispatch, run_id, base, keeper)
    barrier.assert_fenced(run_id)
    count = audit_attested_portfolio_sync_transitions(
        read_client, keeper, run_id, quiescence=barrier)
    barrier.assert_fenced(run_id)
    return LiveSyncColdResult(run_id, count, context, prefix, barrier)


def recover_attested_live_portfolio(*, cold: LiveSyncColdResult,
                                    read_client: Any, keeper: Any,
                                    profiles: tuple[Any, ...],
                                    cutoff_at: datetime) -> Any:
    """Recover every live account from its latest sealed sync revision.

    The cold audit must have inspected the entire marker/fence inventory first.
    A fresh run with no account snapshot cannot inherit a fabricated zero state;
    initial broker synchronization is a separate durable publication step.
    """
    from src.trading_runtime.arte_portfolio_recovery import (
        recover_portfolio_engine_state,
    )
    from src.trading_runtime.portfolio import PortfolioAccountProfile

    if (not isinstance(cold, LiveSyncColdResult)
            or type(cutoff_at) is not datetime or cutoff_at.tzinfo is None
            or not profiles
            or any(type(profile) is not PortfolioAccountProfile for profile in profiles)):
        raise ValueError("Live portfolio recovery requires a cold proof and profiles")
    accounts = tuple(profile.account_id for profile in profiles)
    context_accounts = tuple(cold.context.get("account_ids") or ())
    if (len(set(accounts)) != len(accounts)
            or len(context_accounts) != len(accounts)
            or set(accounts) != set(context_accounts)
            or cold.context.get("mode") != "live"):
        raise ValueError("Live portfolio profiles differ from the sealed run context")
    cold.barrier.assert_fenced(cold.run_id)
    heads = {}
    revisions = {}
    for account_id in accounts:
        head = keeper.load_portfolio_sync_transition_head(cold.run_id, account_id)
        if (head is None or head[0].run_id != cold.run_id
                or head[0].account_id != account_id
                or head[0].proof_count < 1 or head[0].last_revision < 1):
            raise RuntimeError("Live account lacks an attested broker-sync revision")
        heads[account_id] = head
        revisions[account_id] = head[0].last_revision
        load_attested_portfolio_sync_transition(
            read_client, keeper, run_id=cold.run_id, account_id=account_id,
            state_revision=revisions[account_id])
    if sum(head[0].proof_count for head in heads.values()) != cold.transition_count:
        raise RuntimeError("Live account heads differ from the audited sync inventory")
    recovered = recover_portfolio_engine_state(
        read_client, run_id=cold.run_id, profiles=profiles,
        state_revisions=revisions, cutoff_at=cutoff_at)
    if any(keeper.load_portfolio_sync_transition_head(cold.run_id, account_id)
           != head for account_id, head in heads.items()):
        raise RuntimeError("Live portfolio sync heads changed during recovery")
    cold.barrier.assert_fenced(cold.run_id)
    return recovered


def recover_strategy_one_live_oms(*, cold: LiveSyncColdResult,
                                  read_client: Any) -> tuple[Any, ...]:
    """Select normalized OMS heads under the same cold run fence.

    Broker open-order and execution reconciliation remains mandatory before
    these states can be installed in an order manager or admit new orders.
    """
    from src.trading_runtime.arte_oms_projection import (
        load_latest_committed_oms_groups,
    )
    from src.trading_runtime.strategy_one_contract import (
        STRATEGY_ID, STRATEGY_NUMBER,
    )

    if not isinstance(cold, LiveSyncColdResult) or cold.context.get("mode") != "live":
        raise ValueError("Strategy 1 OMS recovery requires a cold live run")
    accounts = tuple(cold.context.get("account_ids") or ())
    if not accounts or len(set(accounts)) != len(accounts):
        raise ValueError("Strategy 1 OMS recovery lacks exact account membership")
    cold.barrier.assert_fenced(cold.run_id)
    groups = load_latest_committed_oms_groups(
        read_client, cold.prefix, allowed_accounts=frozenset(accounts),
        strategy_identity=(STRATEGY_ID, STRATEGY_NUMBER),
        require_tactic=True)
    if any(group.group["account_id"] not in accounts
           or group.group["strategy_id"] != STRATEGY_ID
           or group.group["strategy_revision"] != STRATEGY_NUMBER
           or not group.tactic_recorded
           for group in groups):
        raise RuntimeError("Live OMS group differs from Strategy 1 run authority")
    cold.barrier.assert_fenced(cold.run_id)
    return groups
