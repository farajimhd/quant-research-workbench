"""Keeper-registered two-phase transport for typed live dispatch cursors.

The intent fence precedes activation publication; the ACK fence follows a
durable activation receipt. A missing response remains pending and makes the
session impossible to cold-close. This is not wired into live startup yet.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Any, Mapping

from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT,
)
from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _committed, _identity, _path,
)
from src.trading_runtime.journal_contract import canonical_json


_ZERO = "0" * 64
_TABLES = ((INTENT.name, INTENT_COMMIT.name),
           (ACK.name, ACK_COMMIT.name))


def dispatch_run_id(session_key: str, configuration_revision_id: str) -> str:
    from datetime import date
    if (not isinstance(session_key, str) or
            date.fromisoformat(session_key).isoformat() != session_key):
        raise ValueError("Dispatch session key is invalid")
    _identity(configuration_revision_id, "configuration revision")
    return (f"dispatch:{session_key}:" +
            sha256(configuration_revision_id.encode()).hexdigest())


def _gate_path(run_id: str) -> str:
    return _path("signal_dispatch_insert_gate", run_id)


def _receipt_path(run_id: str, sequence: int) -> str:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("Dispatch receipt sequence is invalid")
    return _path("signal_dispatch_insert_receipt", run_id, str(sequence))


def dispatch_batch_proof(sequence: int, intent_hash: str,
                         ack_hash: str) -> str:
    if (type(sequence) is not int or sequence < 1 or
            any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (intent_hash, ack_hash))):
        raise ValueError("Dispatch batch proof identity is invalid")
    return sha256(f"{sequence}\x00{intent_hash}\x00{ack_hash}".encode()).hexdigest()


def dispatch_family_hash(content_hashes: tuple[str, ...]) -> str:
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None
           for value in content_hashes):
        raise ValueError("Dispatch family content hash is invalid")
    return sha256(canonical_json(content_hashes).encode()).hexdigest()


@dataclass(frozen=True)
class _Operation:
    status: str = "empty"
    sql_hash: str = _ZERO
    row_hash: str = _ZERO


@dataclass(frozen=True)
class _Gate:
    mode: str = "open"
    phase: str = "intent"
    sequence: int = 1
    active: bool = False
    intent_hash: str = _ZERO
    count: int = 0
    proof_xor: str = _ZERO
    operations: tuple[_Operation, _Operation] = (_Operation(), _Operation())

    def wire(self) -> bytes:
        fields = ["1", self.mode, self.phase, str(self.sequence),
                  "1" if self.active else "0", self.intent_hash,
                  str(self.count), self.proof_xor]
        for op in self.operations:
            fields.extend((op.status, op.sql_hash, op.row_hash))
        return "\n".join(fields).encode("ascii")


def _decode(wire: bytes) -> _Gate:
    try:
        parts = wire.decode("ascii").split("\n")
        if len(parts) != 14 or parts[0] != "1" or parts[4] not in {"0", "1"}:
            raise ValueError("gate arity or version")
        ops = (_Operation(*parts[8:11]), _Operation(*parts[11:14]))
        gate = _Gate(parts[1], parts[2], int(parts[3]), parts[4] == "1",
                     parts[5], int(parts[6]), parts[7], ops)
        if (gate.mode not in {"open", "closed"}
                or gate.phase not in {"intent", "ack"}
                or gate.sequence < 1 or gate.count < 0
                or gate.sequence != gate.count + 1
                or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (gate.intent_hash, gate.proof_xor))
                or (gate.phase == "intent") != (gate.intent_hash == _ZERO)
                or (not gate.active) != all(op.status == "empty" for op in ops)
                or any(op.status not in {"empty", "none", "pending", "ack", "sealed"}
                       or re.fullmatch(r"[0-9a-f]{64}", op.sql_hash) is None
                       or re.fullmatch(r"[0-9a-f]{64}", op.row_hash) is None
                       or (op.status == "empty") != (op.row_hash == _ZERO)
                       or (op.status in {"empty", "none"}) != (op.sql_hash == _ZERO)
                       for op in ops)
                or (gate.mode == "closed" and (gate.active or gate.phase != "intent"))
                or gate.wire() != wire):
            raise ValueError("invalid gate")
        return gate
    except (UnicodeError, ValueError, TypeError) as exc:
        raise KeeperUnavailable("Typed dispatch INSERT gate is corrupt") from exc


class SignalDispatchInsertDispatch:
    """Serial session writer; all operations are registered before HTTP INSERT."""

    def __init__(self, keeper: Any) -> None:
        self.keeper = keeper

    def initialize_new_session(self, run_id: str, *, has_ch_rows: bool) -> None:
        _identity(run_id, "dispatch run")
        if type(has_ch_rows) is not bool or has_ch_rows:
            raise KeeperUnavailable("Dispatch session has unregistered ClickHouse rows")
        self.keeper.ensure_path(_path("signal_dispatch_insert_gate"))
        self.keeper.ensure_path(_path("signal_dispatch_insert_receipt"))
        try:
            self.keeper.create(_gate_path(run_id), _Gate().wire())
        except Exception as exc:
            raise KeeperUnavailable("Dispatch session already exists") from exc

    def _read(self, run_id: str) -> tuple[_Gate, int]:
        try:
            value, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Dispatch session gate is absent") from exc
        return _decode(value), stat.version

    def _cas(self, run_id: str, version: int, gate: _Gate) -> bool:
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), gate.wire(), version=version)
        return _committed(txn.commit())

    def reserve(self, run_id: str, *, sequence: int, phase: str,
                row_hashes: Mapping[str, str | None],
                intent_commit_hash: str | None = None) -> None:
        if phase not in {"intent", "ack"}:
            raise ValueError("Dispatch phase is invalid")
        tables = _TABLES[0 if phase == "intent" else 1]
        if (set(row_hashes) != set(tables) or row_hashes[tables[1]] is None
                or any(value is not None and
                       re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in row_hashes.values())):
            raise ValueError("Dispatch family hashes are invalid")
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Dispatch session is cold-fenced")
            if (gate.sequence != sequence or gate.phase != phase or gate.active
                    or (phase == "ack" and gate.intent_hash != intent_commit_hash)
                    or (phase == "intent" and intent_commit_hash is not None)):
                raise KeeperUnavailable("Dispatch phase or intent predecessor differs")
            operations = tuple(
                _Operation("none", _ZERO, row_hashes[name])
                if row_hashes[name] is not None else _Operation()
                for name in tables)
            if self._cas(run_id, version, replace(
                    gate, active=True, operations=operations)):
                return
        raise KeeperUnavailable("Dispatch phase reservation CAS contended")

    def execute(self, client: Any, *, run_id: str, sequence: int,
                phase: str, table: str, row_hash: str, sql: str) -> None:
        tables = _TABLES[0 if phase == "intent" else 1] if phase in {"intent", "ack"} else ()
        token = f"dispatch:{run_id}:{sequence}:{table}:{row_hash}"
        if (table not in tables or re.fullmatch(r"[0-9a-f]{64}", row_hash) is None
                or not sql.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or f"insert_deduplication_token='{token}'" not in sql):
            raise ValueError("Dispatch typed INSERT contract is invalid")
        index = tables.index(table)
        sql_hash = sha256(sql.encode()).hexdigest()
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.sequence != sequence
                    or gate.phase != phase or not gate.active):
                raise KeeperUnavailable("Dispatch typed INSERT lacks reservation")
            op = gate.operations[index]
            expected = _Operation("ack", sql_hash, row_hash)
            if op == expected:
                return
            if op.status != "none" or op.row_hash != row_hash:
                raise KeeperUnavailable("Dispatch typed INSERT is ambiguous or conflicting")
            operations = list(gate.operations)
            operations[index] = _Operation("pending", sql_hash, row_hash)
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                break
        else:
            raise KeeperUnavailable("Dispatch typed INSERT registration CAS contended")
        client.execute(sql, query_id="arte_dispatch_" + sha256(token.encode()).hexdigest())
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.sequence != sequence
                    or gate.phase != phase or not gate.active
                    or gate.operations[index] != _Operation("pending", sql_hash, row_hash)):
                raise KeeperUnavailable("Dispatch typed INSERT acknowledgement lost gate")
            operations = list(gate.operations)
            operations[index] = expected
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                return
        raise KeeperUnavailable("Dispatch typed INSERT acknowledgement CAS contended")

    def seal_readback(self, *, run_id: str, sequence: int,
                      phase: str, table: str, row_hash: str) -> None:
        tables = _TABLES[0 if phase == "intent" else 1] if phase in {"intent", "ack"} else ()
        if table not in tables:
            raise ValueError("Dispatch typed seal table is invalid")
        index = tables.index(table)
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.sequence != sequence
                    or gate.phase != phase or not gate.active):
                raise KeeperUnavailable("Dispatch typed seal lacks reservation")
            op = gate.operations[index]
            if op.status not in {"ack", "sealed"} or op.row_hash != row_hash:
                raise KeeperUnavailable("Dispatch typed row lacks acknowledged INSERT")
            if op.status == "sealed":
                return
            operations = list(gate.operations)
            operations[index] = replace(op, status="sealed")
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                return
        raise KeeperUnavailable("Dispatch typed seal CAS contended")

    def finish_intent(self, *, run_id: str, sequence: int,
                      intent_commit_hash: str, commit_family_hash: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", intent_commit_hash) is None:
            raise ValueError("Dispatch intent commit hash is invalid")
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.phase != "intent"
                    or gate.sequence != sequence or not gate.active
                    or any(op.status not in {"sealed", "empty"} for op in gate.operations)
                    or gate.operations[1].row_hash != commit_family_hash
                    or commit_family_hash != dispatch_family_hash(
                        (intent_commit_hash,))):
                raise KeeperUnavailable("Dispatch intent phase has unresolved INSERTs")
            if self._cas(run_id, version, replace(
                    gate, phase="ack", active=False,
                    intent_hash=intent_commit_hash,
                    operations=(_Operation(), _Operation()))):
                return
        raise KeeperUnavailable("Dispatch intent compaction CAS contended")

    def finish_ack(self, *, run_id: str, sequence: int,
                   intent_commit_hash: str, ack_commit_hash: str,
                   commit_family_hash: str) -> str:
        proof = dispatch_batch_proof(sequence, intent_commit_hash, ack_commit_hash)
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.phase != "ack"
                    or gate.sequence != sequence or not gate.active
                    or gate.intent_hash != intent_commit_hash
                    or any(op.status not in {"sealed", "empty"} for op in gate.operations)
                    or gate.operations[1].row_hash != commit_family_hash
                    or commit_family_hash != dispatch_family_hash(
                        (ack_commit_hash,))):
                raise KeeperUnavailable("Dispatch ACK phase has unresolved INSERTs")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.create(_receipt_path(run_id, sequence), proof.encode(),
                       ephemeral=False)
            txn.set_data(_gate_path(run_id), _Gate(
                sequence=sequence + 1, count=gate.count + 1,
                proof_xor=f"{int(gate.proof_xor, 16) ^ int(proof, 16):064x}"
            ).wire(), version=version)
            if _committed(txn.commit()):
                return proof
        raise KeeperUnavailable("Dispatch ACK compaction CAS contended")

    def close_for_cold(self, run_id: str) -> None:
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode == "closed":
                return
            if gate.active or gate.phase != "intent":
                raise KeeperUnavailable("Dispatch cursor has unresolved phase")
            if self._cas(run_id, version, replace(gate, mode="closed")):
                return
        raise KeeperUnavailable("Dispatch cold fence CAS contended")

    def assert_cold_receipts(self, run_id: str,
                             expected: Mapping[int, tuple[str, str]]) -> None:
        gate, _ = self._read(run_id)
        if gate.mode != "closed" or gate.phase != "intent" or gate.active:
            raise KeeperUnavailable("Dispatch cursor is not cold-fenced")
        if set(expected) != set(range(1, gate.sequence)):
            raise KeeperUnavailable("Dispatch cursor prefix is incomplete")
        proofs = {sequence: dispatch_batch_proof(sequence, *pair)
                  for sequence, pair in expected.items()}
        digest = 0
        for proof in proofs.values():
            digest ^= int(proof, 16)
        if (gate.count != len(proofs) or gate.proof_xor != f"{digest:064x}"):
            raise KeeperUnavailable("Dispatch cursor receipt inventory differs")
        for sequence, proof in proofs.items():
            try:
                wire, _ = self.keeper.get(_receipt_path(run_id, sequence))
            except Exception as exc:
                raise KeeperUnavailable("Dispatch cursor receipt is absent") from exc
            if wire != proof.encode():
                raise KeeperUnavailable("Dispatch cursor receipt differs")
        if self._read(run_id)[0] != gate:
            raise KeeperUnavailable("Dispatch cold fence changed during audit")
