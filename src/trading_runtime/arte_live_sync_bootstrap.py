"""Inactive live typed-sync bootstrap and cold fence; no service cutover.

The fresh-run operation atomically creates both Keeper transport gates. A
legacy run, including one missing either gate, cannot be repaired by this API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.trading_runtime.arte_journal_writer import _literal, _rows
from src.trading_runtime.arte_live_run_allocation import LiveRunAllocation, LiveRunAllocator
from src.trading_runtime.arte_portfolio_sync import audit_attested_portfolio_sync_transitions
from src.trading_runtime.arte_portfolio_sync_dispatch import (
    PortfolioSyncDispatch, SyncColdBarrier, _Gate as _SyncGate,
    _gate_path as _sync_gate_path,
)
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, _Gate as _CoreGate, _ZERO_BATCH, _ZERO_HASH,
    _gate_path as _core_gate_path,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _ROOT, _committed, _identity


_EXISTING_TABLES = (
    "trading_run_v1", "trading_run_context_commit_v1", "trading_commit_v1",
    "trading_portfolio_sync_snapshot_marker_v1", "trading_portfolio_sync_fence_v1",
)


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
    for table in _EXISTING_TABLES:
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
