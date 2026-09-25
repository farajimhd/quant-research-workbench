"""Inactive Keeper-fenced transport for typed arte INSERTs.

No recovery claim is valid for a run that ever used an unwrapped writer. A
lost HTTP response leaves a durable pending operation and prevents cold drain.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _ROOT, _committed, _identity, _path,
)


def _gate_path(run_id: str) -> str:
    return _path("typed_dispatch_gate", run_id)


def _operation_path(run_id: str, query_id: str) -> str:
    return _path("typed_dispatch_operation", run_id, query_id)


def typed_insert_query_id(run_id: str, table: str, token: str) -> str:
    for value, label in ((run_id, "run"), (table, "table"), (token, "token")):
        _identity(value, label)
    return "arte_typed_" + sha256(f"{run_id}\x00{table}\x00{token}".encode()).hexdigest()


@dataclass(frozen=True)
class _Gate:
    mode: str
    inflight: int
    epoch: int
    registered: int

    def wire(self) -> bytes:
        return f"2\n{self.mode}\n{self.inflight}\n{self.epoch}\n{self.registered}".encode()


def _decode_gate(value: bytes) -> _Gate:
    try:
        version, mode, raw_count, raw_epoch, raw_registered = value.decode().split("\n")
        gate = _Gate(mode, int(raw_count), int(raw_epoch), int(raw_registered))
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("Typed dispatch gate is corrupt") from exc
    if (version != "2" or mode not in {"open", "closed"} or gate.inflight < 0
            or gate.epoch < 1 or gate.registered < gate.inflight
            or gate.wire() != value):
        raise KeeperUnavailable("Typed dispatch gate is invalid")
    return gate


def _operation_wire(run_id: str, table: str, query_id: str,
                    token: str, sql: str, status: str) -> bytes:
    if status not in {"pending", "acknowledged", "sealed"}:
        raise ValueError("Typed dispatch operation status is invalid")
    return ("1\n" + "\n".join((run_id, table, query_id,
            sha256(token.encode()).hexdigest(), sha256(sql.encode()).hexdigest(),
            status))).encode()


class TypedInsertDispatch:
    """Blocking coordinator; inject only into a dedicated typed writer client."""

    def __init__(self, keeper: Any, *, max_operations: int = 10_000) -> None:
        if type(max_operations) is not int or not 1 <= max_operations <= 1_000_000:
            raise ValueError("Typed dispatch operation cap is invalid")
        self.keeper = keeper
        self.max_operations = max_operations

    def _read_gate(self, run_id: str) -> tuple[_Gate, int]:
        try:
            value, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Typed dispatch run gate is absent or unavailable") from exc
        return _decode_gate(value), stat.version

    def initialize_new_run(self, run_id: str) -> None:
        """Only a fresh-run bootstrap may call this; never repair a missing gate."""
        _identity(run_id, "run")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_gate")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_operation")
        try:
            self.keeper.create(_gate_path(run_id), _Gate("open", 0, 1, 0).wire())
        except Exception as exc:
            raise KeeperUnavailable("Typed dispatch gate already exists or cannot initialize") from exc

    def execute_typed_insert(self, client: Any, *, run_id: str, table: str,
                             token: str, sql: str) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9_]*", table) is None
                or not sql.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or "insert_deduplication_token=" not in sql):
            raise ValueError("Typed dispatch requires the acknowledged arte INSERT contract")
        query_id = typed_insert_query_id(run_id, table, token)
        path = _operation_path(run_id, query_id)
        pending = _operation_wire(run_id, table, query_id, token, sql, "pending")
        completed = _operation_wire(run_id, table, query_id, token, sql, "acknowledged")
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Typed dispatch run is cold-fenced")
            if gate.registered >= self.max_operations:
                raise KeeperUnavailable("Typed dispatch operation cap reached; stop the run")
            try:
                existing, _ = self.keeper.get(path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("Cannot inspect typed dispatch operation") from exc
            else:
                if existing == completed:
                    return  # Higher-level typed readback still verifies the row.
                raise KeeperUnavailable("Typed dispatch has an ambiguous pending INSERT")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.set_data(_gate_path(run_id),
                         _Gate("open", gate.inflight + 1, gate.epoch,
                               gate.registered + 1).wire(),
                         version=version)
            txn.create(path, pending, ephemeral=False)
            if _committed(txn.commit()):
                break
        else:
            raise KeeperUnavailable("Typed dispatch gate CAS contended")
        # Do not catch/clear transport errors: the server may still commit.
        client.execute(sql, query_id=query_id)
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open" or gate.inflight < 1:
                raise KeeperUnavailable("Typed dispatch gate changed before acknowledgement")
            stored, stat = self.keeper.get(path)
            if stored != pending:
                raise KeeperUnavailable("Typed dispatch operation changed before acknowledgement")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.set_data(_gate_path(run_id),
                         _Gate("open", gate.inflight, gate.epoch,
                               gate.registered).wire(),
                         version=version)
            txn.set_data(path, completed, version=stat.version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed dispatch acknowledgement CAS contended")

    def seal_verified_operation(self, *, run_id: str, table: str, token: str,
                                sql: str | None = None,
                                required: bool = True) -> None:
        """Caller must invoke only after exact parent late-fence readback.

        Unwired parent publishers leave acknowledged operations in-flight,
        intentionally blocking cold recovery rather than assuming an INSERT
        acknowledgement completed a multi-table journal transition.
        """
        query_id = typed_insert_query_id(run_id, table, token)
        path = _operation_path(run_id, query_id)
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Typed dispatch cannot seal outside open parent")
            try:
                stored, stat = self.keeper.get(path)
            except Exception as exc:
                if type(exc).__name__ == "NoNodeError" and not required:
                    return
                raise KeeperUnavailable("Typed dispatch parent lacks operation identity") from exc
            parts = stored.decode("utf-8").split("\n")
            if (len(parts) != 7 or parts[:5] != ["1", run_id, table, query_id,
                    sha256(token.encode()).hexdigest()]
                    or re.fullmatch(r"[0-9a-f]{64}", parts[5]) is None
                    or (sql is not None and parts[5] != sha256(sql.encode()).hexdigest())):
                raise KeeperUnavailable("Typed dispatch operation differs from parent identity")
            if parts[6] == "sealed":
                return
            if parts[6] != "acknowledged" or gate.inflight < 1:
                raise KeeperUnavailable("Typed dispatch operation lacks acknowledged parent")
            sealed = ("\n".join((*parts[:6], "sealed"))).encode()
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.set_data(_gate_path(run_id),
                         _Gate("open", gate.inflight - 1, gate.epoch,
                               gate.registered).wire(), version=version)
            txn.set_data(path, sealed, version=stat.version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed dispatch parent seal CAS contended")

    def acquire_cold_barrier(self, run_id: str) -> "ColdDispatchBarrier":
        gate, version = self._read_gate(run_id)
        if gate.mode != "open" or gate.inflight:
            raise KeeperUnavailable("Typed dispatch has pending or ambiguous INSERTs")
        closed = _Gate("closed", 0, gate.epoch + 1, gate.registered)
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), closed.wire(), version=version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("Typed dispatch cold barrier lost CAS race")
        barrier = ColdDispatchBarrier(self, run_id, closed.epoch)
        barrier.assert_fenced(run_id)
        return barrier


@dataclass(frozen=True)
class ColdDispatchBarrier:
    authority: TypedInsertDispatch
    run_id: str
    epoch: int

    def assert_fenced(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise KeeperUnavailable("Typed dispatch barrier run differs")
        gate, _ = self.authority._read_gate(run_id)
        if gate != _Gate("closed", 0, self.epoch, gate.registered):
            raise KeeperUnavailable("Typed dispatch cold barrier was lost")

    def release(self) -> None:
        gate, version = self.authority._read_gate(self.run_id)
        if gate != _Gate("closed", 0, self.epoch, gate.registered):
            raise KeeperUnavailable("Typed dispatch cold barrier was lost")
        txn = self.authority.keeper.transaction()
        txn.check(_gate_path(self.run_id), version=version)
        txn.set_data(_gate_path(self.run_id),
                     _Gate("open", 0, self.epoch, gate.registered).wire(), version=version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("Typed dispatch cold barrier release lost CAS race")
