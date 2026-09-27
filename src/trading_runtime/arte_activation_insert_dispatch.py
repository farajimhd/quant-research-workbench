"""Keeper-registered Strategy 1 activation INSERT transport.

This control-plane component is not a live admission switch. A fresh run scope
must have no legacy/unregistered rows; callers must verify every typed family
after ACK before sealing it. An uncertain HTTP response stays pending forever
and therefore cannot be mistaken for a drained cold prefix.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping

from src.trading_runtime.arte_activation_projection import (
    ActivationProjection, _family_hash, _require_first_watch_identity,
    _stored, _verify_rows, prepare_activation_commit_row,
    prepare_activation_rows,
)
from src.trading_runtime.arte_journal_schema import ACTIVATION_TABLES
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _committed, _identity, _path,
)


_TABLES = tuple(table.name for table in ACTIVATION_TABLES)
_ZERO = "0" * 64


def _gate_path(run_id: str) -> str:
    return _path("activation_insert_dispatch", run_id)


def _receipt_path(run_id: str, delivery_id: str) -> str:
    return _path("activation_insert_receipt", run_id, delivery_id)


def activation_insert_proof(delivery_id: str, parent_hash: str) -> str:
    _identity(delivery_id, "delivery")
    if re.fullmatch(r"[0-9a-f]{64}", parent_hash) is None:
        raise ValueError("Activation parent hash is invalid")
    return sha256(f"{delivery_id}\x00{parent_hash}".encode()).hexdigest()


@dataclass(frozen=True)
class _Operation:
    status: str = "empty"
    sql_hash: str = _ZERO
    row_hash: str = _ZERO


@dataclass(frozen=True)
class _Gate:
    mode: str = "open"
    active_delivery_hash: str = _ZERO
    count: int = 0
    proof_xor: str = _ZERO
    operations: tuple[_Operation, ...] = (_Operation(),) * len(_TABLES)

    def wire(self) -> bytes:
        fields: list[str] = ["1", self.mode, self.active_delivery_hash,
                             str(self.count), self.proof_xor]
        for operation in self.operations:
            fields.extend((operation.status, operation.sql_hash,
                           operation.row_hash))
        return "\n".join(fields).encode("ascii")


def _decode(wire: bytes) -> _Gate:
    try:
        fields = wire.decode("ascii").split("\n")
        if len(fields) != 5 + 3 * len(_TABLES) or fields[0] != "1":
            raise ValueError("version or arity")
        operations = tuple(_Operation(*fields[5 + 3 * index:8 + 3 * index])
                           for index in range(len(_TABLES)))
        gate = _Gate(fields[1], fields[2], int(fields[3]), fields[4], operations)
        if (gate.mode not in {"open", "closed"} or gate.count < 0
                or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for digest in (gate.active_delivery_hash, gate.proof_xor))
                or len(gate.operations) != len(_TABLES)
                or any(op.status not in {"empty", "none", "pending", "ack", "sealed"}
                       or re.fullmatch(r"[0-9a-f]{64}", op.sql_hash) is None
                       or re.fullmatch(r"[0-9a-f]{64}", op.row_hash) is None
                       or (op.status == "empty") != (op.row_hash == _ZERO)
                       or (op.status in {"empty", "none"}) != (op.sql_hash == _ZERO)
                       for op in operations)
                or (gate.active_delivery_hash == _ZERO) !=
                   all(op.status == "empty" for op in operations)
                or (gate.mode == "closed" and gate.active_delivery_hash != _ZERO)
                or gate.wire() != wire):
            raise ValueError("invalid gate")
        return gate
    except (UnicodeError, ValueError, TypeError) as exc:
        raise KeeperUnavailable("Activation INSERT dispatch gate is corrupt") from exc


class ActivationInsertDispatch:
    """Serial per-run publisher; network work belongs only on a writer thread."""

    def __init__(self, keeper: Any) -> None:
        self.keeper = keeper

    def initialize_new_run(self, run_id: str, *, has_ch_rows: bool) -> None:
        _identity(run_id, "activation run")
        if type(has_ch_rows) is not bool or has_ch_rows:
            raise KeeperUnavailable("Activation run has unregistered ClickHouse rows")
        self.keeper.ensure_path(_path("activation_insert_dispatch"))
        self.keeper.ensure_path(_path("activation_insert_receipt"))
        try:
            self.keeper.create(_gate_path(run_id), _Gate().wire())
        except Exception as exc:
            raise KeeperUnavailable("Activation dispatch run already exists") from exc

    def _read(self, run_id: str) -> tuple[_Gate, int]:
        try:
            wire, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Activation dispatch run is absent") from exc
        return _decode(wire), stat.version

    def assert_open(self, run_id: str) -> None:
        gate, _ = self._read(run_id)
        if gate.mode != "open" or gate.active_delivery_hash != _ZERO:
            raise KeeperUnavailable("Activation dispatch is not ready for new publication")

    def _cas(self, run_id: str, version: int, gate: _Gate) -> bool:
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), gate.wire(), version=version)
        return _committed(txn.commit())

    def reserve(self, run_id: str, delivery_id: str,
                row_hashes: Mapping[str, str | None]) -> None:
        _identity(delivery_id, "delivery")
        if (set(row_hashes) != set(_TABLES)
                or row_hashes[_TABLES[0]] is None
                or row_hashes[_TABLES[-1]] is None
                or any(value is not None and
                       re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in row_hashes.values())):
            raise ValueError("Activation dispatch row families are invalid")
        digest = sha256(delivery_id.encode()).hexdigest()
        try:
            self.keeper.get(_receipt_path(run_id, delivery_id))
        except Exception as exc:
            if type(exc).__name__ != "NoNodeError":
                raise KeeperUnavailable("Activation receipt cannot be inspected") from exc
        else:
            raise KeeperUnavailable("Activation delivery is already committed")
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Activation dispatch is cold-fenced")
            if gate.active_delivery_hash != _ZERO:
                raise KeeperUnavailable("Activation dispatch has unresolved delivery")
            operations = tuple(
                _Operation("none", _ZERO, row_hashes[name])
                if row_hashes[name] is not None else _Operation()
                for name in _TABLES)
            if self._cas(run_id, version, replace(
                    gate, active_delivery_hash=digest, operations=operations)):
                return
        raise KeeperUnavailable("Activation reservation CAS contended")

    def execute(self, client: Any, *, run_id: str, delivery_id: str,
                table: str, row_hash: str, sql: str) -> None:
        if (table not in _TABLES or
                re.fullmatch(r"[0-9a-f]{64}", row_hash) is None or
                not sql.startswith(f"INSERT INTO arte.{table} (") or
                "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql):
            raise ValueError("Activation INSERT contract is invalid")
        token = f"activation:{run_id}:{sha256(delivery_id.encode()).hexdigest()}:{table}:{row_hash}"
        if f"insert_deduplication_token='{token}'" not in sql:
            raise ValueError("Activation INSERT deduplication token differs")
        query_id = "arte_activation_" + sha256(token.encode()).hexdigest()
        sql_hash = sha256(sql.encode()).hexdigest()
        index = _TABLES.index(table)
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.active_delivery_hash !=
                    sha256(delivery_id.encode()).hexdigest()):
                raise KeeperUnavailable("Activation INSERT lacks reservation")
            op = gate.operations[index]
            expected = _Operation("ack", sql_hash, row_hash)
            if op == expected:
                return
            if op.status != "none" or op.row_hash != row_hash:
                raise KeeperUnavailable("Activation INSERT is ambiguous or conflicting")
            operations = list(gate.operations)
            operations[index] = _Operation("pending", sql_hash, row_hash)
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                break
        else:
            raise KeeperUnavailable("Activation INSERT registration CAS contended")
        # A lost response must leave a pending operation; no row scan clears it.
        client.execute(sql, query_id=query_id)
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.active_delivery_hash !=
                    sha256(delivery_id.encode()).hexdigest() or
                    gate.operations[index] != _Operation("pending", sql_hash, row_hash)):
                raise KeeperUnavailable("Activation INSERT acknowledgement lost gate")
            operations = list(gate.operations)
            operations[index] = expected
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                return
        raise KeeperUnavailable("Activation INSERT acknowledgement CAS contended")

    def seal_readback(self, *, run_id: str, delivery_id: str,
                      table: str, row_hash: str) -> None:
        if table not in _TABLES:
            raise ValueError("Activation seal table is invalid")
        index = _TABLES.index(table)
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.active_delivery_hash !=
                    sha256(delivery_id.encode()).hexdigest()):
                raise KeeperUnavailable("Activation seal lacks reservation")
            op = gate.operations[index]
            if op.row_hash != row_hash or op.status not in {"ack", "sealed"}:
                raise KeeperUnavailable("Activation row lacks acknowledged INSERT")
            if op.status == "sealed":
                return
            operations = list(gate.operations)
            operations[index] = replace(op, status="sealed")
            if self._cas(run_id, version, replace(gate, operations=tuple(operations))):
                return
        raise KeeperUnavailable("Activation seal CAS contended")

    def compact(self, *, run_id: str, delivery_id: str,
                parent_hash: str) -> str:
        proof = activation_insert_proof(delivery_id, parent_hash)
        digest = sha256(delivery_id.encode()).hexdigest()
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.active_delivery_hash != digest or
                    any(op.status not in {"sealed", "empty"}
                        for op in gate.operations) or
                    gate.operations[0].row_hash !=
                    _family_hash(({"content_hash": parent_hash},))):
                raise KeeperUnavailable("Activation has unresolved INSERT operations")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.create(_receipt_path(run_id, delivery_id), proof.encode(),
                       ephemeral=False)
            txn.set_data(_gate_path(run_id), _Gate(
                count=gate.count + 1,
                proof_xor=f"{int(gate.proof_xor, 16) ^ int(proof, 16):064x}"
            ).wire(), version=version)
            if _committed(txn.commit()):
                return proof
        raise KeeperUnavailable("Activation compaction CAS contended")

    def close_for_cold(self, run_id: str) -> None:
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode == "closed":
                return
            if gate.active_delivery_hash != _ZERO:
                raise KeeperUnavailable("Activation dispatch has unresolved INSERTs")
            if self._cas(run_id, version, replace(gate, mode="closed")):
                return
        raise KeeperUnavailable("Activation cold fence CAS contended")

    def assert_cold_receipts(self, run_id: str,
                             expected: Mapping[str, str]) -> None:
        gate, _ = self._read(run_id)
        if gate.mode != "closed" or gate.active_delivery_hash != _ZERO:
            raise KeeperUnavailable("Activation dispatch is not cold-fenced")
        if (gate.count != len(expected) or gate.proof_xor !=
                f"{_xor_proofs(expected.values()):064x}"):
            raise KeeperUnavailable("Activation dispatch proof inventory differs")
        for delivery_id, proof in expected.items():
            _identity(delivery_id, "delivery")
            try:
                value, _ = self.keeper.get(_receipt_path(run_id, delivery_id))
            except Exception as exc:
                raise KeeperUnavailable("Activation dispatch receipt is absent") from exc
            if value != proof.encode():
                raise KeeperUnavailable("Activation dispatch receipt differs")
        if self._read(run_id)[0] != gate:
            raise KeeperUnavailable("Activation dispatch fence changed during audit")


def _xor_proofs(values: Any) -> int:
    result = 0
    for value in values:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Activation dispatch proof is invalid")
        result ^= int(value, 16)
    return result


def publish_registered_activation(
    client: Any, dispatch: ActivationInsertDispatch,
    projected: ActivationProjection, *, run_id: str,
    committed_at: datetime | None = None,
) -> str:
    """Publish one Strategy 1 activation on a dedicated ordered writer lane.

    This function does blocking network work and must never run on the market
    execution thread. The caller owns fresh-run preflight and submission queue.
    """
    if not isinstance(projected, ActivationProjection):
        raise TypeError("Registered activation needs a frozen projection")
    prepared = prepare_activation_rows(projected, run_id=run_id)
    committed_at = committed_at or datetime.now(timezone.utc)
    commit = prepare_activation_commit_row(prepared, committed_at=committed_at)
    families = {**prepared, "trading_activation_commit_v1": (commit,)}
    parent = prepared["trading_activation_v1"][0]
    identity = {key: str(parent[key]) for key in (
        "run_id", "session_date", "run_plan_id", "ticker", "event_id")}
    delivery_id = str(parent["delivery_id"])
    _require_first_watch_identity(client, identity)
    hashes = {name: _family_hash(rows) if rows else None
              for name, rows in families.items()}
    dispatch.reserve(run_id, delivery_id, hashes)
    contracts = {table.name: table for table in ACTIVATION_TABLES}
    for name in _TABLES:
        rows = families[name]
        if not rows:
            continue
        row_hash = hashes[name]
        if row_hash is None:
            raise RuntimeError("Activation family hash is absent")
        token = (f"activation:{run_id}:{sha256(delivery_id.encode()).hexdigest()}:"
                 f"{name}:{row_hash}")
        columns = ",".join(column for column, _ in contracts[name].columns)
        body = "\n".join(canonical_json(row) for row in rows)
        sql = (f"INSERT INTO arte.{name} ({columns}) SETTINGS "
               "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
               f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n{body}")
        dispatch.execute(client, run_id=run_id, delivery_id=delivery_id,
                         table=name, row_hash=row_hash, sql=sql)
        stored = _stored(client, name, identity)
        if (len(stored) != len(rows) or
                sorted(row["content_hash"] for row in stored) !=
                sorted(row["content_hash"] for row in rows)):
            raise RuntimeError("Activation typed family readback differs")
        dispatch.seal_readback(run_id=run_id, delivery_id=delivery_id,
                               table=name, row_hash=row_hash)
    _verify_rows(client, identity, require_commit=True)
    dispatch.compact(run_id=run_id, delivery_id=delivery_id,
                     parent_hash=parent["content_hash"])
    return parent["content_hash"]
