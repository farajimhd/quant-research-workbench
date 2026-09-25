"""Inactive Keeper-fenced cold verification of the complete V3 running chain."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterator

from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3
from src.backend.backtest_squeeze_episode_v3 import (
    V3CommittedPrefix, load_verified_squeeze_v3_prefix,
)
from src.trading_runtime.arte_journal_writer import _literal, _rows
from src.trading_runtime.arte_typed_insert_dispatch import (
    ColdDispatchBarrier, TypedInsertDispatch,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable


@dataclass(slots=True)
class V3ColdFence:
    prefix: V3CommittedPrefix
    barrier: Any
    retain_on_failure: bool = False


def verify_retained_v3_cold_gate(
    client: Any, dispatch: TypedInsertDispatch, *, run_id: str,
    expected_market_plan_token: str, expected_query_sha256: str,
) -> V3ColdFence:
    """Re-adopt a closed V3 gate for operator-held terminal reconciliation.

    This does not acquire or release the gate. The caller must hold a separate
    Keeper reconciliation claim and pinned terminal account leases.
    """
    gate, _ = dispatch._read_gate(run_id)
    if (gate.mode != "closed" or gate.inflight or gate.registered
            or gate.active_batch_id != "00000000-0000-0000-0000-000000000000"):
        raise KeeperUnavailable("V3 retained cold gate is not quiescent")
    barrier = ColdDispatchBarrier(dispatch, run_id, gate.epoch)
    barrier.verify_run_context_receipt(client)
    prefix = load_verified_squeeze_v3_prefix(
        client, run_id, expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256)
    if prefix is None or (gate.compacted_through, gate.compacted_batch_id) != (
            prefix.last_sequence, prefix.last_batch_id):
        raise KeeperUnavailable("V3 retained gate differs from committed prefix")
    columns = ",".join(name for name, _ in SQUEEZE_COMMIT_V3.columns
                       if name not in {"run_month", "committed_at"})
    rows = _rows(client,
        f"SELECT {columns} FROM arte.trading_commit_v3 "
        f"WHERE run_id={_literal(run_id)} "
        f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
        "FORMAT JSONEachRow")
    if (len(rows) != 1 or sha256(canonical_json(rows[0]).encode()).hexdigest()
            != gate.compacted_commit_hash):
        raise KeeperUnavailable("V3 retained gate commit hash differs")
    barrier.prefix_verified = True
    barrier.assert_fenced(run_id)
    return V3ColdFence(prefix, barrier, retain_on_failure=True)


@contextmanager
def attested_squeeze_v3_barrier(
    client: Any, dispatch: TypedInsertDispatch, *, run_id: str,
    expected_market_plan_token: str, expected_query_sha256: str,
) -> Iterator[V3ColdFence]:
    """Close admission, audit all V3 facts, and compare the durable CAS watermark.

    A terminal suffix must not be based on a merely visible CH commit. An
    un-compacted or ambiguous INSERT remains a hard failure here.
    """
    if not isinstance(dispatch, TypedInsertDispatch):
        raise TypeError("V3 cold verification needs persistent typed dispatch")
    barrier = dispatch.acquire_cold_barrier(run_id)
    fence = None
    completed = False
    try:
        barrier.verify_run_context_receipt(client)
        prefix = load_verified_squeeze_v3_prefix(
            client, run_id, expected_market_plan_token=expected_market_plan_token,
            expected_query_sha256=expected_query_sha256)
        if prefix is None:
            raise KeeperUnavailable("V3 cold prefix is absent")
        gate, _ = dispatch._read_gate(run_id)
        if (gate.compacted_through != prefix.last_sequence
                or gate.compacted_batch_id != prefix.last_batch_id):
            raise KeeperUnavailable("V3 commit chain differs from Keeper watermark")
        columns = ",".join(name for name, _ in SQUEEZE_COMMIT_V3.columns
                           if name not in {"run_month", "committed_at"})
        rows = _rows(client,
            f"SELECT {columns} FROM arte.trading_commit_v3 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
            "FORMAT JSONEachRow")
        if (len(rows) != 1 or sha256(canonical_json(rows[0]).encode()).hexdigest()
                != gate.compacted_commit_hash):
            raise KeeperUnavailable("V3 last commit differs from Keeper receipt")
        barrier.prefix_verified = True
        barrier.assert_fenced(run_id)
        fence = V3ColdFence(prefix, barrier)
        yield fence
        barrier.assert_fenced(run_id)
        completed = True
    finally:
        # Once a terminal INSERT has been admitted, an uncertain response must
        # leave the run closed for explicit cold reconciliation.
        if completed or fence is None or not fence.retain_on_failure:
            barrier.release()


def load_keeper_attested_squeeze_v3_prefix(
    client: Any, dispatch: TypedInsertDispatch, *, run_id: str,
    expected_market_plan_token: str, expected_query_sha256: str,
) -> V3CommittedPrefix:
    """Read-only convenience that releases the gate after the cold audit."""
    with attested_squeeze_v3_barrier(
        client, dispatch, run_id=run_id,
        expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256) as fence:
        return fence.prefix
