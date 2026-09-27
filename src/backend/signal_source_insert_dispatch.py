"""Keeper-registered serial transport for normalized Signal Stream source rows.

An unacknowledged HTTP INSERT is never retried or inferred from a later SELECT.
The active live route remains disabled until this transport, exact family
readback, source-head attestation, and downstream recovery are wired together.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import re
from typing import Any

from src.backend.signal_stream_typed_cursor import TABLES as CURSOR_TABLES
from src.backend.signal_stream_typed_occurrence import TABLES as OCCURRENCE_TABLES
from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _committed, _identity, _path,
)


_ZERO = "0" * 64
_TABLES = frozenset(table.name for table in (*OCCURRENCE_TABLES, *CURSOR_TABLES))


def source_insert_run_id(session_key: str, configuration_revision: str) -> str:
    if (type(session_key) is not str
            or date.fromisoformat(session_key).isoformat() != session_key):
        raise ValueError("Signal source session key is invalid")
    _identity(configuration_revision, "configuration revision")
    return (f"signal-source:{session_key}:"
            + sha256(configuration_revision.encode()).hexdigest())


def _gate_path(run_id: str) -> str:
    return _path("signal_source_insert_gate", run_id)


@dataclass(frozen=True)
class _Gate:
    mode: str = "open"
    sequence: int = 1
    active: bool = False
    status: str = "empty"
    table: str = ""
    token_hash: str = _ZERO
    sql_hash: str = _ZERO
    row_hash: str = _ZERO
    count: int = 0
    operations_hash: str = _ZERO
    last_commit_hash: str = _ZERO

    def wire(self) -> bytes:
        return "\n".join(("1", self.mode, str(self.sequence),
                          "1" if self.active else "0", self.status, self.table,
                          self.token_hash, self.sql_hash, self.row_hash,
                          str(self.count), self.operations_hash,
                          self.last_commit_hash)).encode("ascii")


def _decode(value: bytes) -> _Gate:
    try:
        parts = value.decode("ascii").split("\n")
        gate = _Gate(parts[1], int(parts[2]), parts[3] == "1", parts[4],
                     parts[5], parts[6], parts[7], parts[8], int(parts[9]),
                     parts[10], parts[11])
        if (len(parts) != 12 or parts[0] != "1" or parts[3] not in {"0", "1"}
                or gate.mode not in {"open", "closed"}
                or gate.sequence < 1 or gate.count < 0
                or gate.status not in {"empty", "pending", "ack"}
                or (gate.status == "empty") != (gate.table == "")
                or (gate.status != "empty" and gate.table not in _TABLES)
                or (not gate.active and (gate.status != "empty" or gate.count))
                or (gate.mode == "closed" and gate.active)
                or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for digest in (gate.token_hash, gate.sql_hash,
                                      gate.row_hash, gate.operations_hash,
                                      gate.last_commit_hash))
                or (gate.status == "empty" and any(digest != _ZERO for digest in (
                    gate.token_hash, gate.sql_hash, gate.row_hash)))
                or gate.wire() != value):
            raise ValueError("invalid source gate")
        return gate
    except (ValueError, IndexError, UnicodeError, TypeError) as exc:
        raise KeeperUnavailable("Signal source INSERT gate is corrupt") from exc


class SignalSourceInsertDispatch:
    """One source-session writer; all transitions are control-plane I/O."""

    def __init__(self, keeper: Any) -> None:
        self.keeper = keeper

    def initialize_new_session(self, run_id: str, *, has_ch_rows: bool) -> None:
        _identity(run_id, "source run")
        if type(has_ch_rows) is not bool or has_ch_rows:
            raise KeeperUnavailable("Signal source has unregistered ClickHouse rows")
        self.keeper.ensure_path(_path("signal_source_insert_gate"))
        try:
            self.keeper.create(_gate_path(run_id), _Gate().wire())
        except Exception as exc:
            raise KeeperUnavailable("Signal source INSERT session already exists") from exc

    def _read(self, run_id: str) -> tuple[_Gate, int]:
        try:
            value, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Signal source INSERT gate is absent") from exc
        return _decode(value), stat.version

    def _cas(self, run_id: str, version: int, gate: _Gate) -> bool:
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), gate.wire(), version=version)
        return _committed(txn.commit())

    def begin_batch(self, run_id: str, *, sequence: int,
                    previous_commit_hash: str) -> None:
        if type(sequence) is not int or sequence < 1:
            raise ValueError("Signal source batch sequence is invalid")
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.active or gate.sequence != sequence
                    or gate.last_commit_hash != previous_commit_hash):
                raise KeeperUnavailable("Signal source batch does not extend fenced prefix")
            if self._cas(run_id, version, replace(gate, active=True)):
                return
        raise KeeperUnavailable("Signal source batch reservation CAS contended")

    def execute(self, client: Any, *, run_id: str, sequence: int,
                table: str, token: str, row_hash: str, sql: str) -> None:
        if (table not in _TABLES or not isinstance(token, str)
                or re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", token) is None
                or re.fullmatch(r"[0-9a-f]{64}", row_hash) is None
                or not sql.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or f"insert_deduplication_token='{token}'" not in sql):
            raise ValueError("Signal source typed INSERT contract is invalid")
        token_hash = sha256(token.encode()).hexdigest()
        sql_hash = sha256(sql.encode()).hexdigest()
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or not gate.active
                    or gate.sequence != sequence or gate.status != "empty"):
                raise KeeperUnavailable("Signal source INSERT lacks a free batch gate")
            pending = replace(gate, status="pending", table=table,
                              token_hash=token_hash, sql_hash=sql_hash,
                              row_hash=row_hash)
            if self._cas(run_id, version, pending):
                break
        else:
            raise KeeperUnavailable("Signal source INSERT registration CAS contended")
        # A lost response may mean the server still commits. Leave pending.
        client.execute(sql, query_id="arte_signal_source_" + sha256(
            f"{run_id}\x00{sequence}\x00{token}".encode()).hexdigest())
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate != pending:
                raise KeeperUnavailable("Signal source INSERT ACK lost its gate")
            if self._cas(run_id, version, replace(gate, status="ack")):
                return
        raise KeeperUnavailable("Signal source INSERT acknowledgement CAS contended")

    def seal_readback(self, *, run_id: str, sequence: int, table: str,
                      token: str, row_hash: str) -> None:
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or not gate.active or gate.sequence != sequence
                    or gate.status != "ack" or gate.table != table
                    or gate.token_hash != sha256(token.encode()).hexdigest()
                    or gate.row_hash != row_hash):
                raise KeeperUnavailable("Signal source row lacks acknowledged INSERT")
            digest = sha256((gate.operations_hash + "\x00" + table + "\x00"
                             + gate.token_hash + "\x00" + row_hash).encode()).hexdigest()
            if self._cas(run_id, version, replace(
                    gate, status="empty", table="", token_hash=_ZERO,
                    sql_hash=_ZERO, row_hash=_ZERO, count=gate.count + 1,
                    operations_hash=digest)):
                return
        raise KeeperUnavailable("Signal source readback seal CAS contended")

    def finish_batch(self, *, run_id: str, sequence: int,
                     commit_hash: str, expected_operations: int) -> None:
        if (re.fullmatch(r"[0-9a-f]{64}", commit_hash) is None
                or type(expected_operations) is not int or expected_operations < 1):
            raise ValueError("Signal source commit receipt is invalid")
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or not gate.active or gate.sequence != sequence
                    or gate.status != "empty" or gate.count != expected_operations):
                raise KeeperUnavailable("Signal source batch has unsealed INSERTs")
            if self._cas(run_id, version, replace(
                    gate, sequence=sequence + 1, active=False, count=0,
                    operations_hash=_ZERO, last_commit_hash=commit_hash)):
                return
        raise KeeperUnavailable("Signal source batch commit CAS contended")

    def acquire_cold_barrier(self, run_id: str) -> tuple[int, str]:
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open" or gate.active or gate.status != "empty":
                raise KeeperUnavailable("Signal source has pending or ambiguous INSERTs")
            if self._cas(run_id, version, replace(gate, mode="closed")):
                return gate.sequence - 1, gate.last_commit_hash
        raise KeeperUnavailable("Signal source cold barrier CAS contended")

    def release_cold_barrier(self, run_id: str, *, sequence: int,
                             commit_hash: str) -> None:
        """Reopen only the exact closed prefix after independent cold audit."""
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "closed" or gate.active or gate.status != "empty"
                    or gate.sequence - 1 != sequence
                    or gate.last_commit_hash != commit_hash):
                raise KeeperUnavailable("Signal source cold barrier prefix differs")
            if self._cas(run_id, version, replace(gate, mode="open")):
                return
        raise KeeperUnavailable("Signal source cold release CAS contended")

    def assert_cold_prefix(self, run_id: str, *, sequence: int,
                           commit_hash: str) -> None:
        gate, _ = self._read(run_id)
        if (gate.mode != "closed" or gate.active or gate.status != "empty"
                or gate.sequence - 1 != sequence
                or gate.last_commit_hash != commit_hash):
            raise KeeperUnavailable("Signal source cold prefix changed")
