"""Inactive durable Keeper allocation for never-reused typed live run IDs.

The allocator namespace must be provisioned once with a never-reused UUID by
operations. An absent head is an error, never a reason to reset the sequence.
Keeper stores identity and hashes only; normalized ClickHouse run context is
the data authority. No live launcher calls this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_writer import _literal, _rows, load_typed_run_context
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _committed, _identity, _path

_ZERO = "0" * 64
_HEAD = _path("live_run_allocator", "v2")


def _uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError("noncanonical")
    except (TypeError, ValueError) as exc:
        raise ValueError("Live run allocator needs a canonical UUID") from exc
    return value


def _receipt_path(request_id: str) -> str:
    return _path("live_run_allocation", _uuid(request_id))


@dataclass(frozen=True)
class LiveRunAllocatorHead:
    namespace_id: str
    last_sequence: int

    def wire(self) -> bytes:
        _uuid(self.namespace_id)
        if type(self.last_sequence) is not int or self.last_sequence < 0:
            raise ValueError("Live run allocator sequence is invalid")
        return f"1\n{self.namespace_id}\n{self.last_sequence}".encode()


@dataclass(frozen=True)
class LiveRunAllocation:
    namespace_id: str
    sequence: int
    run_id: str
    request_id: str
    owner_id: str
    status: str
    context_hash: str = _ZERO

    def wire(self) -> bytes:
        _uuid(self.namespace_id)
        _uuid(self.request_id)
        _identity(self.owner_id, "owner")
        if (type(self.sequence) is not int or self.sequence < 1
                or self.run_id != f"live:v2:{self.namespace_id}:{self.sequence:020d}"
                or self.status not in {"allocated", "gates_bound", "context_bound"}
                or re.fullmatch(r"[0-9a-f]{64}", self.context_hash) is None
                or (self.status == "context_bound") != (self.context_hash != _ZERO)):
            raise ValueError("Live run allocation is invalid")
        return ("1\n" + "\n".join(map(str, (
            self.namespace_id, self.sequence, self.run_id, self.request_id,
            self.owner_id, self.status, self.context_hash)))).encode()


class LiveRunAllocator:
    def __init__(self, keeper: Any) -> None:
        self.keeper = keeper

    def _head(self) -> tuple[LiveRunAllocatorHead, int]:
        try:
            value, stat = self.keeper.get(_HEAD)
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                raise KeeperUnavailable("Live run allocator head is absent") from exc
            raise KeeperUnavailable("Cannot read live run allocator head") from exc
        try:
            parts = value.decode("ascii").split("\n")
            if len(parts) != 3 or parts[0] != "1":
                raise ValueError("version")
            head = LiveRunAllocatorHead(parts[1], int(parts[2]))
            if head.wire() != value:
                raise ValueError("canonical")
            return head, stat.version
        except (UnicodeError, ValueError, TypeError) as exc:
            raise KeeperUnavailable("Live run allocator head is corrupt") from exc

    def load(self, request_id: str) -> tuple[LiveRunAllocation, int] | None:
        try:
            value, stat = self.keeper.get(_receipt_path(request_id))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Live run allocation cannot be read") from exc
        try:
            parts = value.decode("ascii").split("\n")
            if len(parts) != 8 or parts[0] != "1":
                raise ValueError("version")
            receipt = LiveRunAllocation(parts[1], int(parts[2]), parts[3],
                                        parts[4], parts[5], parts[6], parts[7])
            if receipt.request_id != request_id or receipt.wire() != value:
                raise ValueError("identity")
            return receipt, stat.version
        except (UnicodeError, ValueError, TypeError) as exc:
            raise KeeperUnavailable("Live run allocation is corrupt") from exc

    def allocate(self, *, request_id: str, owner_id: str) -> LiveRunAllocation:
        _uuid(request_id)
        _identity(owner_id, "owner")
        if self.load(request_id) is not None:
            raise KeeperUnavailable("Live run request ID was already allocated")
        for _ in range(8):
            head, version = self._head()
            sequence = head.last_sequence + 1
            receipt = LiveRunAllocation(head.namespace_id, sequence,
                f"live:v2:{head.namespace_id}:{sequence:020d}",
                request_id, owner_id, "allocated")
            txn = self.keeper.transaction()
            txn.check(_HEAD, version=version)
            txn.set_data(_HEAD,
                LiveRunAllocatorHead(head.namespace_id, sequence).wire(),
                version=version)
            txn.create(_receipt_path(request_id), receipt.wire(), ephemeral=False)
            try:
                committed = _committed(txn.commit())
            except Exception as exc:
                raise KeeperUnavailable("Live run allocation outcome is ambiguous") from exc
            if committed:
                if self.load(request_id) != (receipt, 0):
                    raise KeeperUnavailable("Live run allocation readback differs")
                return receipt
            if self.load(request_id) is not None:
                raise KeeperUnavailable("Live run request was concurrently allocated")
        raise KeeperUnavailable("Live run allocator CAS contended")

    def assert_status(self, receipt: LiveRunAllocation, status: str) -> int:
        stored = self.load(receipt.request_id)
        head, _ = self._head()
        if (stored is None or stored[0] != receipt or receipt.status != status
                or head.namespace_id != receipt.namespace_id
                or head.last_sequence < receipt.sequence):
            raise KeeperUnavailable("Live run allocation status or namespace differs")
        return stored[1]

    def add_gate_binding(self, txn: Any, receipt: LiveRunAllocation) -> None:
        version = self.assert_status(receipt, "allocated")
        txn.check(_receipt_path(receipt.request_id), version=version)
        txn.set_data(_receipt_path(receipt.request_id),
                     replace(receipt, status="gates_bound").wire(), version=version)

    def bind_context(self, client: Any, core_dispatch: Any,
                     receipt: LiveRunAllocation) -> LiveRunAllocation:
        current = replace(receipt, status="gates_bound")
        version = self.assert_status(current, "gates_bound")
        context = load_typed_run_context(client, receipt.run_id)
        if context.get("mode") != "live" or context.get("run_id") != receipt.run_id:
            raise KeeperUnavailable("Allocated run lacks normalized live context")
        rows = _rows(client,
            "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
            "FROM arte.trading_run_context_commit_v1 "
            f"WHERE run_id={_literal(receipt.run_id)} FORMAT JSONEachRow")
        if len(rows) != 1:
            raise KeeperUnavailable("Allocated run context fence is missing or duplicate")
        fence_hash = sha256(canonical_json(rows[0]).encode()).hexdigest()
        core_dispatch.assert_run_context_receipt(
            run_id=receipt.run_id, fence_hash=fence_hash)
        bound = replace(current, status="context_bound", context_hash=fence_hash)
        txn = self.keeper.transaction()
        txn.check(_receipt_path(receipt.request_id), version=version)
        txn.set_data(_receipt_path(receipt.request_id), bound.wire(), version=version)
        try:
            if not _committed(txn.commit()):
                raise KeeperUnavailable("Live run context binding CAS contended")
        except KeeperUnavailable:
            raise
        except Exception as exc:
            raise KeeperUnavailable("Live run context binding is ambiguous") from exc
        self.assert_status(bound, "context_bound")
        return bound

    def verify_context_bound(self, client: Any, core_dispatch: Any,
                             receipt: LiveRunAllocation) -> dict[str, Any]:
        self.assert_status(receipt, "context_bound")
        context = load_typed_run_context(client, receipt.run_id)
        if context.get("mode") != "live":
            raise KeeperUnavailable("Allocated run context is not live")
        rows = _rows(client,
            "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
            "FROM arte.trading_run_context_commit_v1 "
            f"WHERE run_id={_literal(receipt.run_id)} FORMAT JSONEachRow")
        if (len(rows) != 1 or
                sha256(canonical_json(rows[0]).encode()).hexdigest() != receipt.context_hash):
            raise KeeperUnavailable("Allocated run context changed after binding")
        core_dispatch.assert_run_context_receipt(
            run_id=receipt.run_id, fence_hash=receipt.context_hash)
        return context
