"""Inactive bounded Keeper transport for the two portfolio-sync late facts.

One run gate holds at most one sync transition. Pending INSERTs are deliberately
irrecoverable by a negative/positive row read: a delayed server INSERT may still
arrive. Only acknowledged operations may be sealed after exact row readback.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Any

from src.trading_runtime.keeper_ownership import KeeperUnavailable, _committed, _identity, _path

_MARKER = "trading_portfolio_sync_snapshot_marker_v1"
_FENCE = "trading_portfolio_sync_fence_v1"
_ZERO = "0" * 64


def _gate_path(run_id: str) -> str:
    return _path("portfolio_sync_dispatch", run_id)


def _query_id(run_id: str, account_id: str, revision: int, table: str) -> str:
    return "arte_sync_" + sha256(
        f"{run_id}\x00{account_id}\x00{revision}\x00{table}".encode()).hexdigest()


@dataclass(frozen=True)
class _Operation:
    status: str = "none"
    sql_hash: str = _ZERO
    row_hash: str = _ZERO

    def fields(self) -> tuple[str, str, str]:
        return self.status, self.sql_hash, self.row_hash


@dataclass(frozen=True)
class _Gate:
    mode: str = "open"
    account_id: str = ""
    revision: int = 0
    count: int = 0
    proof_xor: str = _ZERO
    last_proof_hash: str = _ZERO
    last_account_id: str = ""
    last_revision: int = 0
    marker: _Operation = _Operation()
    fence: _Operation = _Operation()

    def wire(self) -> bytes:
        return ("3\n" + "\n".join(map(str, (
            self.mode, self.account_id, self.revision, self.count, self.proof_xor,
            self.last_proof_hash, self.last_account_id, self.last_revision,
            *self.marker.fields(), *self.fence.fields())))).encode()


def _decode(value: bytes) -> _Gate:
    try:
        parts = value.decode("ascii").split("\n")
        if len(parts) != 15 or parts[0] != "3":
            raise ValueError("version")
        gate = _Gate(parts[1], parts[2], int(parts[3]), int(parts[4]), parts[5],
                     parts[6], parts[7], int(parts[8]),
                     _Operation(*parts[9:12]), _Operation(*parts[12:15]))
        if (gate.mode not in {"open", "closed"} or gate.revision < 0 or gate.count < 0
                or bool(gate.account_id) != bool(gate.revision)
                or re.fullmatch(r"[0-9a-f]{64}", gate.proof_xor) is None
                or re.fullmatch(r"[0-9a-f]{64}", gate.last_proof_hash) is None
                or (gate.count == 0) != (gate.last_proof_hash == _ZERO)
                or bool(gate.last_account_id) != bool(gate.last_revision)
                or (gate.count == 0) != (gate.last_revision == 0)
                or any(op.status not in {"none", "pending", "ack", "sealed"}
                       or re.fullmatch(r"[0-9a-f]{64}", op.sql_hash) is None
                       or re.fullmatch(r"[0-9a-f]{64}", op.row_hash) is None
                       or (op.status == "none") != (op.sql_hash == op.row_hash == _ZERO)
                       for op in (gate.marker, gate.fence))
                or (not gate.revision and (gate.marker.status != "none" or
                                           gate.fence.status != "none"))
                or (gate.mode == "closed" and gate.revision)
                or gate.wire() != value):
            raise ValueError("invalid gate")
        if gate.account_id:
            _identity(gate.account_id, "account")
        if gate.last_account_id:
            _identity(gate.last_account_id, "account")
        return gate
    except (UnicodeError, ValueError, TypeError) as exc:
        raise KeeperUnavailable("Portfolio sync dispatch gate is corrupt") from exc


class PortfolioSyncDispatch:
    def __init__(self, keeper: Any) -> None:
        self.keeper = keeper

    def initialize_new_run(self, run_id: str) -> None:
        _identity(run_id, "run")
        self.keeper.ensure_path(_path("portfolio_sync_dispatch"))
        try:
            self.keeper.create(_gate_path(run_id), _Gate().wire())
        except Exception as exc:
            raise KeeperUnavailable("Portfolio sync dispatch gate already exists") from exc

    def _read(self, run_id: str) -> tuple[_Gate, int]:
        try:
            value, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Portfolio sync dispatch gate is absent") from exc
        return _decode(value), stat.version

    def _cas(self, run_id: str, version: int, gate: _Gate) -> bool:
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), gate.wire(), version=version)
        return _committed(txn.commit())

    def reserve(self, run_id: str, account_id: str, revision: int) -> None:
        _identity(account_id, "account")
        if type(revision) is not int or revision < 1:
            raise ValueError("Portfolio sync revision is invalid")
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Portfolio sync dispatch is cold-fenced")
            if (gate.account_id, gate.revision) == (account_id, revision):
                return
            if gate.revision:
                raise KeeperUnavailable("Competing portfolio sync owns the run gate")
            if (gate.last_account_id, gate.last_revision) == (account_id, revision):
                raise KeeperUnavailable("Portfolio sync revision is already compacted")
            if self._cas(run_id, version, replace(gate, account_id=account_id,
                                                 revision=revision)):
                return
        raise KeeperUnavailable("Portfolio sync reservation CAS contended")

    def execute(self, client: Any, *, run_id: str, account_id: str, revision: int,
                table: str, token: str, sql: str, row_hash: str) -> None:
        if (table not in {_MARKER, _FENCE} or
                token != f"portfolio-sync:{run_id}:{account_id}:{revision}" +
                (":marker" if table == _MARKER else "") or
                not sql.startswith(f"INSERT INTO arte.{table} (") or
                "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql or
                re.fullmatch(r"[0-9a-f]{64}", row_hash) is None):
            raise ValueError("Portfolio sync dispatch identity differs from INSERT")
        sql_hash = sha256(sql.encode()).hexdigest()
        attr = "marker" if table == _MARKER else "fence"
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or (gate.account_id, gate.revision) !=
                    (account_id, revision)):
                raise KeeperUnavailable("Portfolio sync INSERT lacks active reservation")
            op = getattr(gate, attr)
            expected = _Operation("ack", sql_hash, row_hash)
            if op == expected:
                return
            if op.status != "none":
                raise KeeperUnavailable("Portfolio sync INSERT is ambiguous or conflicting")
            if self._cas(run_id, version, replace(gate, **{
                    attr: _Operation("pending", sql_hash, row_hash)})):
                break
        else:
            raise KeeperUnavailable("Portfolio sync INSERT reservation contended")
        # A lost transport response must leave pending state forever; row
        # visibility alone cannot disprove delayed completion.
        client.execute(sql, query_id=_query_id(run_id, account_id, revision, table))
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or (gate.account_id, gate.revision) !=
                    (account_id, revision) or getattr(gate, attr) !=
                    _Operation("pending", sql_hash, row_hash)):
                raise KeeperUnavailable("Portfolio sync INSERT acknowledgement lost gate")
            if self._cas(run_id, version, replace(gate, **{attr: expected})):
                return
        raise KeeperUnavailable("Portfolio sync INSERT acknowledgement CAS contended")

    def seal_readback(self, *, run_id: str, account_id: str, revision: int,
                      table: str, row_hash: str) -> None:
        if table not in {_MARKER, _FENCE}:
            raise ValueError("Portfolio sync seal table is invalid")
        attr = "marker" if table == _MARKER else "fence"
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or (gate.account_id, gate.revision) !=
                    (account_id, revision)):
                raise KeeperUnavailable("Portfolio sync seal lacks reservation")
            op = getattr(gate, attr)
            if op.row_hash != row_hash or op.status not in {"ack", "sealed"}:
                raise KeeperUnavailable("Portfolio sync row lacks acknowledged dispatch")
            if op.status == "sealed":
                return
            if self._cas(run_id, version, replace(gate, **{
                    attr: replace(op, status="sealed")})):
                return
        raise KeeperUnavailable("Portfolio sync seal CAS contended")

    def compact(self, *, run_id: str, account_id: str, revision: int,
                marker_hash: str, fence_hash: str, proof: Any) -> None:
        if (proof.run_id, proof.account_id, proof.state_revision,
                proof.marker_hash, proof.fence_hash) != (
                run_id, account_id, revision, marker_hash, fence_hash):
            raise KeeperUnavailable("Portfolio sync proof differs from dispatch parent")
        digest = int.from_bytes(sha256(proof.wire()).digest(), "big")
        for _ in range(8):
            gate, version = self._read(run_id)
            if not gate.revision and gate.last_proof_hash == f"{digest:064x}":
                return
            if (gate.mode != "open" or (gate.account_id, gate.revision) !=
                    (account_id, revision) or gate.marker !=
                    _Operation("sealed", gate.marker.sql_hash, marker_hash) or
                    gate.fence != _Operation("sealed", gate.fence.sql_hash, fence_hash)):
                raise KeeperUnavailable("Portfolio sync has unresolved dispatch operations")
            done = _Gate(count=gate.count + 1,
                         proof_xor=f"{int(gate.proof_xor, 16) ^ digest:064x}",
                         last_proof_hash=f"{digest:064x}",
                         last_account_id=account_id, last_revision=revision)
            if self._cas(run_id, version, done):
                return
        raise KeeperUnavailable("Portfolio sync compaction CAS contended")

    def is_latest_compacted(self, run_id: str, proof: Any) -> bool:
        gate, _ = self._read(run_id)
        return (not gate.revision and gate.count > 0 and
                gate.last_proof_hash == sha256(proof.wire()).hexdigest())

    def acquire_cold_barrier(self, run_id: str, base_barrier: Any,
                             keeper: Any) -> "SyncColdBarrier":
        base_barrier.assert_fenced(run_id)
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open" or gate.revision:
                raise KeeperUnavailable("Portfolio sync has unresolved INSERTs")
            if self._cas(run_id, version, replace(gate, mode="closed")):
                break
        else:
            raise KeeperUnavailable("Portfolio sync cold fence CAS contended")
        barrier = SyncColdBarrier(self, run_id, base_barrier, keeper)
        barrier.assert_fenced(run_id)
        return barrier


@dataclass(frozen=True)
class SyncColdBarrier:
    dispatch: PortfolioSyncDispatch
    run_id: str
    base: Any
    keeper: Any

    def assert_fenced(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise KeeperUnavailable("Portfolio sync cold barrier run differs")
        gate, _ = self.dispatch._read(run_id)
        head = self.keeper.load_portfolio_sync_transition_head(run_id, None)
        if (gate.mode != "closed" or gate.revision or
                (head is None and gate.count != 0) or
                (head is not None and (head[0].proof_count,
                 head[0].proof_xor) != (gate.count, gate.proof_xor))):
            raise KeeperUnavailable("Portfolio sync cold barrier differs from proofs")
        self.base.assert_fenced(run_id)


def audit_cold_sync_with_dispatch(client: Any, *, run_id: str,
                                  base_barrier: Any,
                                  dispatch: PortfolioSyncDispatch,
                                  keeper: Any) -> int:
    """Cold path requires both the core run fence and sync-specific drain."""
    from src.trading_runtime.arte_portfolio_sync import (
        audit_attested_portfolio_sync_transitions,
    )
    barrier = dispatch.acquire_cold_barrier(run_id, base_barrier, keeper)
    return audit_attested_portfolio_sync_transitions(
        client, keeper, run_id, quiescence=barrier)
