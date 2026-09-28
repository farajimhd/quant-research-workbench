"""Keeper-registered transport for typed live completion receipts.

An HTTP INSERT is registered before it is sent. A lost response leaves the
session pending; an exact readback and Keeper completion proof are both needed
before its cold fence may advance. This module never creates ClickHouse tables.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import re
from typing import Any, Mapping

from src.backend.live_signal_completion_keeper import completion_resource
from src.backend.live_signal_work_completion import COMPLETION
from src.backend.strategy_one_live_signal_schema import strategy_one_signal_table
from src.backend.signal_stream_typed_readback import canonical_row
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _committed, _path,
)


_ZERO = "0" * 64


def completion_insert_run_id(session_key: str, *, strategy_one: bool = False) -> str:
    if (type(session_key) is not str
            or date.fromisoformat(session_key).isoformat() != session_key):
        raise ValueError("completion INSERT session is invalid")
    if type(strategy_one) is not bool:
        raise ValueError("completion INSERT mode is invalid")
    prefix = "strategy-one-signal-completion" if strategy_one else "signal-completion"
    return f"{prefix}:{session_key}"


def _gate_path(run_id: str) -> str:
    return _path("completion_insert_gate", run_id)


def _receipt_path(run_id: str, resource: str) -> str:
    return _path("completion_insert_receipt", run_id, resource)


def completion_insert_proof(resource: str, row_hash: str) -> str:
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None
           for value in (resource, row_hash)):
        raise ValueError("completion INSERT proof identity is invalid")
    return sha256(f"{resource}\x00{row_hash}".encode()).hexdigest()


@dataclass(frozen=True)
class _Gate:
    mode: str = "open"
    status: str = "empty"
    resource: str = _ZERO
    sql_hash: str = _ZERO
    row_hash: str = _ZERO
    count: int = 0
    proof_xor: str = _ZERO

    def wire(self) -> bytes:
        return "\n".join(("1", self.mode, self.status, self.resource,
                          self.sql_hash, self.row_hash, str(self.count),
                          self.proof_xor)).encode("ascii")


def _decode(wire: bytes) -> _Gate:
    try:
        parts = wire.decode("ascii").split("\n")
        gate = _Gate(parts[1], parts[2], parts[3], parts[4], parts[5],
                     int(parts[6]), parts[7])
        if (len(parts) != 8 or parts[0] != "1"
                or gate.mode not in {"open", "closed"}
                or gate.status not in {"empty", "pending", "ack"}
                or gate.count < 0 or gate.mode == "closed" and gate.status != "empty"
                or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (gate.resource, gate.sql_hash,
                                     gate.row_hash, gate.proof_xor))
                or (gate.status == "empty") != (
                    gate.resource == gate.sql_hash == gate.row_hash == _ZERO)
                or gate.wire() != wire):
            raise ValueError("invalid completion INSERT gate")
        return gate
    except (ValueError, IndexError, UnicodeError, TypeError) as exc:
        raise KeeperUnavailable("completion INSERT gate is corrupt") from exc


class CompletionInsertDispatch:
    """One serial writer per session; all methods are control-plane I/O."""

    def __init__(self, keeper: Any, *, strategy_one: bool = False) -> None:
        if type(strategy_one) is not bool:
            raise ValueError("completion INSERT mode is invalid")
        self.keeper = keeper
        self.strategy_one = strategy_one
        self.table = (strategy_one_signal_table(COMPLETION.name) if strategy_one
                      else COMPLETION.name)

    def _scope(self, run_id: str) -> None:
        prefix = ("strategy-one-signal-completion:" if self.strategy_one
                  else "signal-completion:")
        if not run_id.startswith(prefix):
            raise ValueError("completion run differs from table authority")
        completion_insert_run_id(run_id.removeprefix(prefix),
                                 strategy_one=self.strategy_one)

    def initialize_new_session(self, run_id: str, *, has_ch_rows: bool) -> None:
        self._scope(run_id)
        if type(has_ch_rows) is not bool or has_ch_rows:
            raise KeeperUnavailable("completion INSERT session has unregistered rows")
        self.keeper.ensure_path(_path("completion_insert_gate"))
        self.keeper.ensure_path(_path("completion_insert_receipt", run_id))
        try:
            self.keeper.create(_gate_path(run_id), _Gate().wire())
        except Exception as exc:
            raise KeeperUnavailable("completion INSERT session already exists") from exc

    def _read(self, run_id: str) -> tuple[_Gate, int]:
        self._scope(run_id)
        try:
            wire, stat = self.keeper.get(_gate_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("completion INSERT gate is absent") from exc
        return _decode(wire), stat.version

    def _cas(self, run_id: str, version: int, gate: _Gate) -> bool:
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), gate.wire(), version=version)
        return _committed(txn.commit())

    def execute(self, client: Any, *, run_id: str, resource: str,
                row_hash: str, sql: str, token: str) -> None:
        if (re.fullmatch(r"[0-9a-f]{64}", resource) is None
                or re.fullmatch(r"[0-9a-f]{64}", row_hash) is None
                or re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", token) is None
                or not sql.startswith(
                    f"INSERT INTO arte.{self.table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or f"insert_deduplication_token='{token}'" not in sql):
            raise ValueError("completion INSERT SQL contract differs")
        sql_hash = sha256(sql.encode()).hexdigest()
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode != "open" or gate.status != "empty":
                raise KeeperUnavailable("completion INSERT lacks a free gate")
            try:
                self.keeper.get(_receipt_path(run_id, resource))
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("completion receipt cannot be inspected") from exc
            else:
                raise KeeperUnavailable("completion INSERT resource already sealed")
            pending = replace(gate, status="pending", resource=resource,
                              sql_hash=sql_hash, row_hash=row_hash)
            if self._cas(run_id, version, pending):
                break
        else:
            raise KeeperUnavailable("completion INSERT registration CAS contended")
        # Never retry an ambiguous response: the server may still commit it.
        query_id = "arte_completion_" + sha256(
            f"{run_id}\x00{resource}\x00{token}".encode()).hexdigest()
        registered = getattr(client, "execute_registered_signal_insert", None)
        if self.strategy_one and callable(registered):
            registered(sql, query_id=query_id, kind="completion", dispatch=self,
                       run_id=run_id, sequence=0, table=self.table)
        else:
            client.execute(sql, query_id=query_id)
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate != pending:
                raise KeeperUnavailable("completion INSERT ACK lost its gate")
            if self._cas(run_id, version, replace(gate, status="ack")):
                return
        raise KeeperUnavailable("completion INSERT acknowledgement CAS contended")

    def seal_readback(self, *, run_id: str, row: Mapping[str, Any],
                      storage: Any, keeper: Any) -> str:
        normalized = canonical_row(COMPLETION, row)
        observed = storage.read_completion_rows(
            session_key=normalized["session_key"],
            source_batch_sequence=normalized["source_batch_sequence"],
            ordinal=normalized["ordinal"])
        if (len(observed) != 1
                or canonical_row(COMPLETION, observed[0]) != normalized):
            raise KeeperUnavailable("completion INSERT exact row readback differs")
        expected_hash = sha256(canonical_json({
            key: value for key, value in normalized.items()
            if key != "content_hash"
        }).encode()).hexdigest()
        if normalized["content_hash"] != expected_hash:
            raise KeeperUnavailable("completion INSERT row content hash differs")
        resource = completion_resource(
            normalized["session_key"], normalized["source_batch_sequence"],
            normalized["ordinal"], normalized["delivery_id"])
        digest = normalized["content_hash"]
        proof = completion_insert_proof(resource, digest)
        if not keeper.completion_proof_matches(
                resource, owner_id=normalized["keeper_owner_id"],
                epoch=normalized["keeper_epoch"], content_hash=digest):
            raise KeeperUnavailable("completion INSERT lacks attested readback")
        for _ in range(8):
            gate, version = self._read(run_id)
            if (gate.mode != "open" or gate.status != "ack"
                    or gate.resource != resource or gate.row_hash != digest):
                raise KeeperUnavailable("completion INSERT seal differs from registered row")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.create(_receipt_path(run_id, resource), proof.encode(), ephemeral=False)
            txn.set_data(_gate_path(run_id), replace(
                gate, status="empty", resource=_ZERO, sql_hash=_ZERO,
                row_hash=_ZERO, count=gate.count + 1,
                proof_xor=f"{int(gate.proof_xor, 16) ^ int(proof, 16):064x}"
            ).wire(), version=version)
            if _committed(txn.commit()):
                return proof
        raise KeeperUnavailable("completion INSERT seal CAS contended")

    def close_for_cold(self, run_id: str) -> None:
        for _ in range(8):
            gate, version = self._read(run_id)
            if gate.mode == "closed":
                return
            if gate.status != "empty":
                raise KeeperUnavailable("completion INSERT has unresolved operation")
            if self._cas(run_id, version, replace(gate, mode="closed")):
                return
        raise KeeperUnavailable("completion INSERT cold fence CAS contended")

    def assert_sealed_resource(self, run_id: str, *, resource: str,
                               row_hash: str) -> None:
        proof = completion_insert_proof(resource, row_hash)
        gate, _ = self._read(run_id)
        if gate.mode != "open" or gate.count < 1:
            raise KeeperUnavailable("completion INSERT lacks a sealed resource")
        try:
            wire, _ = self.keeper.get(_receipt_path(run_id, resource))
        except Exception as exc:
            raise KeeperUnavailable("completion INSERT resource receipt is absent") from exc
        if wire != proof.encode():
            raise KeeperUnavailable("completion INSERT resource receipt differs")

    def assert_cold_receipts(self, run_id: str,
                             expected: Mapping[str, str]) -> None:
        gate, _ = self._read(run_id)
        proofs = {resource: completion_insert_proof(resource, digest)
                  for resource, digest in expected.items()}
        combined = 0
        for proof in proofs.values():
            combined ^= int(proof, 16)
        if (gate.mode != "closed" or gate.status != "empty"
                or gate.count != len(proofs)
                or gate.proof_xor != f"{combined:064x}"):
            raise KeeperUnavailable("completion INSERT receipt inventory differs")
        for resource, proof in proofs.items():
            try:
                wire, _ = self.keeper.get(_receipt_path(run_id, resource))
            except Exception as exc:
                raise KeeperUnavailable("completion INSERT receipt is absent") from exc
            if wire != proof.encode():
                raise KeeperUnavailable("completion INSERT receipt differs")
        if self._read(run_id)[0] != gate:
            raise KeeperUnavailable("completion INSERT cold fence changed during audit")
