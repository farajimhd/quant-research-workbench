"""Inactive Keeper-fenced transport for typed arte INSERTs.

No recovery claim is valid for a run that ever used an unwrapped writer. A
lost HTTP response leaves a durable pending operation and prevents cold drain.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any
from uuid import UUID

from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _ROOT, _committed, _identity, _path,
)


_ZERO_BATCH = "00000000-0000-0000-0000-000000000000"
_ZERO_HASH = "0" * 64
_SNAPSHOT_TABLES = frozenset({
    "trading_portfolio_snapshot_v1", "trading_portfolio_disabled_strategy_v1",
    "trading_portfolio_command_v1", "trading_portfolio_request_v1",
    "trading_portfolio_request_reason_v1", "trading_portfolio_reservation_v1",
    "trading_portfolio_allocation_v1", "trading_portfolio_reconciliation_v1",
    "trading_portfolio_snapshot_commit_v1",
})


def _gate_path(run_id: str) -> str:
    return _path("typed_dispatch_gate", run_id)


def _operation_path(run_id: str, query_id: str) -> str:
    return _path("typed_dispatch_operation", run_id, query_id)


def _context_receipt_path(run_id: str) -> str:
    return _path("typed_dispatch_context_receipt", run_id)


def _terminal_receipt_path(run_id: str, account_id: str) -> str:
    return _path("typed_dispatch_terminal_receipt", run_id, account_id)


def _snapshot_head_path(run_id: str, account_id: str) -> str:
    return _path("typed_dispatch_snapshot_head", run_id, account_id)


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
    compacted_through: int
    compacted_batch_id: str
    compacted_commit_hash: str
    active_batch_id: str

    def wire(self) -> bytes:
        return (f"4\n{self.mode}\n{self.inflight}\n{self.epoch}\n{self.registered}\n"
                f"{self.compacted_through}\n{self.compacted_batch_id}\n"
                f"{self.compacted_commit_hash}\n{self.active_batch_id}").encode()


def _decode_gate(value: bytes) -> _Gate:
    try:
        (version, mode, raw_count, raw_epoch, raw_registered, raw_sequence,
         batch_id, commit_hash, active_id) = value.decode().split("\n")
        gate = _Gate(mode, int(raw_count), int(raw_epoch), int(raw_registered),
                     int(raw_sequence), str(UUID(batch_id)), commit_hash,
                     str(UUID(active_id)))
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("Typed dispatch gate is corrupt") from exc
    if (version != "4" or mode not in {"open", "closed"} or gate.inflight < 0
            or gate.epoch < 1 or gate.registered < gate.inflight
            or gate.compacted_through < 0
            or re.fullmatch(r"[0-9a-f]{64}", gate.compacted_commit_hash) is None
            or (gate.compacted_through == 0) != (
                gate.compacted_batch_id == _ZERO_BATCH and
                gate.compacted_commit_hash == _ZERO_HASH)
            or gate.wire() != value):
        raise KeeperUnavailable("Typed dispatch gate is invalid")
    return gate


@dataclass(frozen=True)
class _SnapshotHead:
    committed_revision: int
    fence_hash: str
    active_revision: int

    def wire(self) -> bytes:
        return (f"1\n{self.committed_revision}\n{self.fence_hash}\n"
                f"{self.active_revision}").encode()


def _decode_snapshot_head(value: bytes) -> _SnapshotHead:
    try:
        version, committed, digest, active = value.decode("ascii").split("\n")
        head = _SnapshotHead(int(committed), digest, int(active))
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("Portfolio snapshot head is corrupt") from exc
    if (version != "1" or head.committed_revision < 0
            or head.active_revision < 0
            or head.active_revision and head.active_revision <= head.committed_revision
            or re.fullmatch(r"[0-9a-f]{64}", head.fence_hash) is None
            or (head.committed_revision == 0) != (head.fence_hash == _ZERO_HASH)
            or head.wire() != value):
        raise KeeperUnavailable("Portfolio snapshot head is invalid")
    return head


def _operation_wire(run_id: str, table: str, query_id: str,
                    token: str, sql: str, batch_id: str,
                    sequence: int, status: str) -> bytes:
    if status not in {"pending", "acknowledged", "sealed"}:
        raise ValueError("Typed dispatch operation status is invalid")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("Typed dispatch requires a nonnegative parent sequence")
    return ("3\n" + "\n".join((run_id, table, query_id,
            sha256(token.encode()).hexdigest(), sha256(sql.encode()).hexdigest(),
            batch_id, str(sequence), status))).encode()


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
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_context_receipt")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_terminal_receipt")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_snapshot_head")
        try:
            self.keeper.create(_gate_path(run_id), _Gate(
                "open", 0, 1, 0, 0, _ZERO_BATCH, _ZERO_HASH,
                _ZERO_BATCH).wire())
        except Exception as exc:
            raise KeeperUnavailable("Typed dispatch gate already exists or cannot initialize") from exc

    def assert_next_batch(self, *, run_id: str, batch_id: str,
                          prior_batch_id: str, first_sequence: int,
                          last_sequence: int) -> None:
        try:
            if (str(UUID(batch_id)) != batch_id
                    or str(UUID(prior_batch_id)) != prior_batch_id
                    or type(first_sequence) is not int or first_sequence < 1
                    or type(last_sequence) is not int
                    or last_sequence < first_sequence):
                raise ValueError("batch identity is not canonical")
        except (TypeError, ValueError) as exc:
            raise ValueError("Typed batch reservation identity is invalid") from exc
        self._read_context_receipt(run_id)
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Typed dispatch run is cold-fenced")
            if gate.active_batch_id == _ZERO_BATCH and (gate.inflight or gate.registered):
                raise KeeperUnavailable("Typed batch cannot start with unresolved run-context INSERTs")
            if last_sequence <= gate.compacted_through:
                if (last_sequence == gate.compacted_through
                        and batch_id == gate.compacted_batch_id):
                    return
                raise KeeperUnavailable("Typed batch retry precedes compacted watermark")
            if (first_sequence != gate.compacted_through + 1
                    or prior_batch_id != gate.compacted_batch_id):
                raise KeeperUnavailable("Typed batch does not extend compacted prefix")
            if gate.active_batch_id == batch_id:
                return
            if gate.active_batch_id != _ZERO_BATCH:
                raise KeeperUnavailable("Competing typed batch owns the run prefix")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.set_data(_gate_path(run_id), _Gate(
                gate.mode, gate.inflight, gate.epoch, gate.registered,
                gate.compacted_through, gate.compacted_batch_id,
                gate.compacted_commit_hash, batch_id).wire(), version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed batch reservation CAS contended")

    def execute_typed_insert(self, client: Any, *, run_id: str, table: str,
                             token: str, sql: str,
                             batch_id: str | None = None,
                             batch_last_sequence: int | None = None,
                             terminal_account_id: str | None = None,
                             snapshot_account_id: str | None = None) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9_]*", table) is None
                or not sql.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or "insert_deduplication_token=" not in sql):
            raise ValueError("Typed dispatch requires the acknowledged arte INSERT contract")
        if (type(batch_last_sequence) is not int or batch_last_sequence < 0
                or not isinstance(batch_id, str)
                or (snapshot_account_id is None and
                    (batch_last_sequence == 0) != (batch_id == _ZERO_BATCH))
                or (snapshot_account_id is not None and batch_id != _ZERO_BATCH)):
            raise KeeperUnavailable("Strict typed dispatch lacks batch sequence authority")
        if terminal_account_id is not None and snapshot_account_id is not None:
            raise ValueError("Typed INSERT has multiple parent families")
        if snapshot_account_id is not None:
            _identity(snapshot_account_id, "account")
            if batch_last_sequence < 1 or table not in _SNAPSHOT_TABLES:
                raise KeeperUnavailable("Portfolio snapshot dispatch table is invalid")
            suffix = "commit" if table == "trading_portfolio_snapshot_commit_v1" else table
            if token != (f"portfolio-state:{run_id}:{snapshot_account_id}:"
                         f"{batch_last_sequence}:{suffix}"):
                raise KeeperUnavailable("Portfolio snapshot dispatch token differs from account revision")
        if terminal_account_id is not None:
            _identity(terminal_account_id, "account")
            if table != "trading_backtest_snapshot_anchor_v1" or batch_last_sequence < 1:
                raise KeeperUnavailable("Terminal dispatch table or sequence is invalid")
        query_id = typed_insert_query_id(run_id, table, token)
        path = _operation_path(run_id, query_id)
        pending = _operation_wire(run_id, table, query_id, token, sql, batch_id,
                                  batch_last_sequence, "pending")
        completed = _operation_wire(run_id, table, query_id, token, sql, batch_id,
                                    batch_last_sequence, "acknowledged")
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Typed dispatch run is cold-fenced")
            if snapshot_account_id is not None:
                self._read_context_receipt(run_id)
                head = self._read_snapshot_head(run_id, snapshot_account_id)
                if (head is None or head[0].active_revision != batch_last_sequence
                        or gate.active_batch_id != _ZERO_BATCH
                        or gate.inflight < 1 or gate.registered < 1):
                    raise KeeperUnavailable("Portfolio snapshot lacks active account reservation")
            elif terminal_account_id is not None:
                self._read_context_receipt(run_id)
                if (gate.inflight or gate.active_batch_id != _ZERO_BATCH
                        or gate.compacted_through != batch_last_sequence
                        or gate.compacted_batch_id != batch_id):
                    raise KeeperUnavailable("Terminal anchor differs from compacted run prefix")
                try:
                    self.keeper.get(_terminal_receipt_path(run_id, terminal_account_id))
                except Exception as exc:
                    if type(exc).__name__ != "NoNodeError":
                        raise KeeperUnavailable("Terminal receipt cannot be inspected") from exc
                else:
                    raise KeeperUnavailable("Terminal account receipt already sealed")
            elif batch_last_sequence and gate.active_batch_id != batch_id:
                raise KeeperUnavailable("Competing typed batch owns the run prefix")
            if snapshot_account_id is None and terminal_account_id is None and batch_last_sequence and batch_last_sequence <= gate.compacted_through:
                return  # Exact CH batch readback and watermark check still follow.
            if snapshot_account_id is None and terminal_account_id is None and not batch_last_sequence and gate.active_batch_id != _ZERO_BATCH:
                raise KeeperUnavailable("Run context cannot dispatch during a batch")
            if snapshot_account_id is None and terminal_account_id is None and not batch_last_sequence:
                try:
                    self.keeper.get(_context_receipt_path(run_id))
                except Exception as exc:
                    if type(exc).__name__ != "NoNodeError":
                        raise KeeperUnavailable("Run-context receipt cannot be inspected") from exc
                else:
                    raise KeeperUnavailable("Run-context receipt already sealed")
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
                               gate.registered + 1, gate.compacted_through,
                               gate.compacted_batch_id, gate.compacted_commit_hash,
                               gate.active_batch_id).wire(),
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
                               gate.registered, gate.compacted_through,
                               gate.compacted_batch_id, gate.compacted_commit_hash,
                               gate.active_batch_id).wire(),
                         version=version)
            txn.set_data(path, completed, version=stat.version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed dispatch acknowledgement CAS contended")

    def seal_verified_operation(self, *, run_id: str, table: str, token: str,
                                sql: str | None = None,
                                required: bool = True,
                                batch_id: str | None = None,
                                batch_last_sequence: int | None = None,
                                terminal: bool = False,
                                snapshot: bool = False) -> None:
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
            if (not terminal and not snapshot and type(batch_last_sequence) is int and batch_last_sequence > 0
                    and batch_last_sequence <= gate.compacted_through):
                return
            try:
                stored, stat = self.keeper.get(path)
            except Exception as exc:
                if type(exc).__name__ == "NoNodeError" and not required:
                    return
                raise KeeperUnavailable("Typed dispatch parent lacks operation identity") from exc
            parts = stored.decode("utf-8").split("\n")
            if (len(parts) != 9 or parts[:5] != ["3", run_id, table, query_id,
                    sha256(token.encode()).hexdigest()]
                    or re.fullmatch(r"[0-9a-f]{64}", parts[5]) is None
                    or parts[6] != batch_id
                    or type(batch_last_sequence) is not int
                    or parts[7] != str(batch_last_sequence)
                    or (sql is not None and parts[5] != sha256(sql.encode()).hexdigest())):
                raise KeeperUnavailable("Typed dispatch operation differs from parent identity")
            if parts[8] == "sealed":
                return
            if parts[8] != "acknowledged" or gate.inflight < 1:
                raise KeeperUnavailable("Typed dispatch operation lacks acknowledged parent")
            sealed = ("\n".join((*parts[:8], "sealed"))).encode()
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.set_data(_gate_path(run_id),
                         _Gate("open", gate.inflight - 1, gate.epoch,
                               gate.registered, gate.compacted_through,
                               gate.compacted_batch_id, gate.compacted_commit_hash,
                               gate.active_batch_id).wire(),
                         version=version)
            txn.set_data(path, sealed, version=stat.version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed dispatch parent seal CAS contended")

    def compact_verified_run_context(self, *, run_id: str, fence_hash: str,
                                     operations: tuple[tuple[str, str], ...]) -> None:
        """Retire exact acknowledged bootstrap INSERTs after context-fence readback.

        A fixed receipt survives operation GC. A pending lost-response operation
        is deliberately not recoverable here: its late server completion is
        unbounded and requires transport-level quiescence.
        """
        if (re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
                or not operations or len(set(operations)) != len(operations)):
            raise ValueError("Run-context receipt identity is invalid")
        receipt_path = _context_receipt_path(run_id)
        receipt = ("1\n" + fence_hash).encode()
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open" or gate.inflight or gate.active_batch_id != _ZERO_BATCH:
                raise KeeperUnavailable("Run context has unresolved INSERTs or active batch")
            try:
                existing, _ = self.keeper.get(receipt_path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("Run-context receipt cannot be inspected") from exc
            else:
                if existing != receipt:
                    raise KeeperUnavailable("Run-context receipt conflicts with fence")
                if gate.registered:
                    raise KeeperUnavailable("Run-context receipt has orphan operations")
                return
            paths = []
            for table, token in operations:
                query_id = typed_insert_query_id(run_id, table, token)
                path = _operation_path(run_id, query_id)
                try:
                    value, stat = self.keeper.get(path)
                except Exception as exc:
                    raise KeeperUnavailable("Run-context operation lacks durable dispatch identity") from exc
                parts = value.decode().split("\n")
                if (len(parts) != 9 or parts[:5] != ["3", run_id, table, query_id,
                        sha256(token.encode()).hexdigest()]
                        or parts[6:] != [_ZERO_BATCH, "0", "sealed"]):
                    raise KeeperUnavailable("Run-context operation is not sealed")
                paths.append((path, stat.version))
            if gate.registered != len(paths):
                raise KeeperUnavailable("Run-context operation inventory is incomplete")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            for path, op_version in paths:
                txn.delete(path, version=op_version)
            txn.create(receipt_path, receipt, ephemeral=False)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", 0, gate.epoch, 0, gate.compacted_through,
                gate.compacted_batch_id, gate.compacted_commit_hash,
                _ZERO_BATCH).wire(), version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Run-context receipt CAS contended")

    def assert_run_context_receipt(self, *, run_id: str, fence_hash: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None:
            raise ValueError("Run-context fence hash is invalid")
        try:
            value, _ = self.keeper.get(_context_receipt_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Run-context Keeper receipt is missing") from exc
        if value != ("1\n" + fence_hash).encode():
            raise KeeperUnavailable("Run-context Keeper receipt conflicts with ClickHouse")

    def _read_context_receipt(self, run_id: str) -> str:
        try:
            value, _ = self.keeper.get(_context_receipt_path(run_id))
        except Exception as exc:
            raise KeeperUnavailable("Typed batch lacks run-context Keeper receipt") from exc
        try:
            version, digest = value.decode("ascii").split("\n")
        except (UnicodeError, ValueError) as exc:
            raise KeeperUnavailable("Run-context Keeper receipt is invalid") from exc
        if (version != "1" or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or value != ("1\n" + digest).encode()):
            raise KeeperUnavailable("Run-context Keeper receipt is invalid")
        return digest

    def _read_snapshot_head(self, run_id: str, account_id: str) -> tuple[_SnapshotHead, int] | None:
        try:
            value, stat = self.keeper.get(_snapshot_head_path(run_id, account_id))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Portfolio snapshot head cannot be read") from exc
        return _decode_snapshot_head(value), stat.version

    def reserve_snapshot_revision(self, *, run_id: str, account_id: str,
                                  revision: int,
                                  latest_ch_revision: int | None) -> str:
        """Reserve a strictly newer account revision before any snapshot INSERT."""
        _identity(account_id, "account")
        if (type(revision) is not int or revision < 1
                or (latest_ch_revision is not None and
                    (type(latest_ch_revision) is not int or latest_ch_revision < 1))):
            raise ValueError("Portfolio snapshot revision is invalid")
        self._read_context_receipt(run_id)
        path = _snapshot_head_path(run_id, account_id)
        for _ in range(8):
            gate, gate_version = self._read_gate(run_id)
            if gate.mode != "open" or gate.active_batch_id != _ZERO_BATCH:
                raise KeeperUnavailable("Portfolio snapshot run is cold-fenced or batch-active")
            observed = self._read_snapshot_head(run_id, account_id)
            if observed is None:
                if latest_ch_revision is not None:
                    raise KeeperUnavailable("Portfolio snapshot has unattested legacy commit")
                proposed = _SnapshotHead(0, _ZERO_HASH, revision)
            else:
                head, head_version = observed
                if head.active_revision:
                    if (head.active_revision != revision
                            or latest_ch_revision not in (
                                head.committed_revision or None, revision)
                            or gate.inflight < 1 or gate.registered < 1):
                        raise KeeperUnavailable("Competing portfolio snapshot revision is active")
                    return "active"
                if latest_ch_revision != (head.committed_revision or None):
                    raise KeeperUnavailable("ClickHouse snapshot head differs from Keeper")
                if revision == head.committed_revision:
                    return "committed"
                if revision < head.committed_revision:
                    raise KeeperUnavailable("Portfolio snapshot revision is stale")
                proposed = _SnapshotHead(head.committed_revision,
                                         head.fence_hash, revision)
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=gate_version)
            if observed is None:
                txn.create(path, proposed.wire(), ephemeral=False)
            else:
                txn.set_data(path, proposed.wire(), version=head_version)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", gate.inflight + 1, gate.epoch, gate.registered + 1,
                gate.compacted_through, gate.compacted_batch_id,
                gate.compacted_commit_hash, gate.active_batch_id).wire(),
                version=gate_version)
            if _committed(txn.commit()):
                return "active"
        raise KeeperUnavailable("Portfolio snapshot reservation CAS contended")

    def compact_verified_snapshot(self, *, run_id: str, account_id: str,
                                  revision: int, fence_hash: str,
                                  operations: tuple[tuple[str, str], ...]) -> None:
        """Advance one account head and GC its sealed operations atomically."""
        _identity(account_id, "account")
        if (type(revision) is not int or revision < 1
                or re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
                or not operations or len(set(operations)) != len(operations)):
            raise ValueError("Portfolio snapshot compaction identity is invalid")
        path = _snapshot_head_path(run_id, account_id)
        for _ in range(8):
            gate, gate_version = self._read_gate(run_id)
            observed = self._read_snapshot_head(run_id, account_id)
            if observed is None:
                raise KeeperUnavailable("Portfolio snapshot lacks reserved account head")
            head, head_version = observed
            if gate.mode != "open" or gate.active_batch_id != _ZERO_BATCH:
                raise KeeperUnavailable("Portfolio snapshot cannot compact during batch or cold fence")
            if head.active_revision == 0:
                if head.committed_revision == revision and head.fence_hash == fence_hash:
                    return
                raise KeeperUnavailable("Portfolio snapshot committed head conflicts")
            if head.active_revision != revision or gate.inflight < 1:
                raise KeeperUnavailable("Portfolio snapshot active head conflicts")
            paths = []
            for table, token in operations:
                suffix = "commit" if table == "trading_portfolio_snapshot_commit_v1" else table
                if (table not in _SNAPSHOT_TABLES
                        or token != f"portfolio-state:{run_id}:{account_id}:{revision}:{suffix}"):
                    raise KeeperUnavailable("Portfolio snapshot operation inventory differs from account revision")
                query_id = typed_insert_query_id(run_id, table, token)
                op_path = _operation_path(run_id, query_id)
                try:
                    value, stat = self.keeper.get(op_path)
                except Exception as exc:
                    raise KeeperUnavailable("Portfolio snapshot lacks dispatch operation") from exc
                parts = value.decode().split("\n")
                if (len(parts) != 9 or parts[:5] != ["3", run_id, table, query_id,
                        sha256(token.encode()).hexdigest()]
                        or parts[6:] != [_ZERO_BATCH, str(revision), "sealed"]):
                    raise KeeperUnavailable("Portfolio snapshot operation is not sealed")
                paths.append((op_path, stat.version))
            if gate.registered < len(paths) + 1:
                raise KeeperUnavailable("Portfolio snapshot operation count is corrupt")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=gate_version)
            for op_path, op_version in paths:
                txn.delete(op_path, version=op_version)
            txn.set_data(path, _SnapshotHead(revision, fence_hash, 0).wire(),
                         version=head_version)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", gate.inflight - 1, gate.epoch,
                gate.registered - len(paths) - 1, gate.compacted_through,
                gate.compacted_batch_id, gate.compacted_commit_hash,
                _ZERO_BATCH).wire(), version=gate_version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Portfolio snapshot compaction CAS contended")

    def assert_snapshot_head(self, *, run_id: str, account_id: str,
                             revision: int, fence_hash: str) -> None:
        observed = self._read_snapshot_head(run_id, account_id)
        if observed is None or observed[0] != _SnapshotHead(revision, fence_hash, 0):
            raise KeeperUnavailable("Portfolio snapshot head differs from ClickHouse")

    def compact_verified_terminal_anchor(self, *, run_id: str, account_id: str,
                                         batch_id: str, last_sequence: int,
                                         anchor_hash: str, snapshot_hash: str,
                                         token: str) -> None:
        """Retire one exact terminal anchor INSERT into a fixed account receipt."""
        _identity(account_id, "account")
        if (type(last_sequence) is not int or last_sequence < 1
                or re.fullmatch(r"[0-9a-f]{64}", anchor_hash) is None
                or re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None):
            raise ValueError("Terminal receipt identity is invalid")
        try:
            if str(UUID(batch_id)) != batch_id:
                raise ValueError("noncanonical UUID")
        except (TypeError, ValueError) as exc:
            raise ValueError("Terminal receipt batch ID is invalid") from exc
        self._read_context_receipt(run_id)
        receipt_path = _terminal_receipt_path(run_id, account_id)
        receipt = (f"1\n{batch_id}\n{last_sequence}\n{anchor_hash}\n{snapshot_hash}").encode()
        table = "trading_backtest_snapshot_anchor_v1"
        query_id = typed_insert_query_id(run_id, table, token)
        path = _operation_path(run_id, query_id)
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if (gate.mode != "open" or gate.inflight or gate.active_batch_id != _ZERO_BATCH
                    or gate.compacted_batch_id != batch_id
                    or gate.compacted_through != last_sequence):
                raise KeeperUnavailable("Terminal receipt lacks quiescent compacted prefix")
            try:
                existing, _ = self.keeper.get(receipt_path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("Terminal receipt cannot be inspected") from exc
            else:
                if existing != receipt:
                    raise KeeperUnavailable("Terminal account receipt conflicts")
                return
            try:
                value, stat = self.keeper.get(path)
            except Exception as exc:
                raise KeeperUnavailable("Terminal anchor lacks durable dispatch identity") from exc
            parts = value.decode().split("\n")
            if (len(parts) != 9 or parts[:5] != ["3", run_id, table, query_id,
                    sha256(token.encode()).hexdigest()]
                    or parts[6:] != [batch_id, str(last_sequence), "sealed"]
                    or gate.registered < 1):
                raise KeeperUnavailable("Terminal anchor operation is not sealed")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            txn.delete(path, version=stat.version)
            txn.create(receipt_path, receipt, ephemeral=False)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", 0, gate.epoch, gate.registered - 1,
                gate.compacted_through, gate.compacted_batch_id,
                gate.compacted_commit_hash, _ZERO_BATCH).wire(), version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Terminal receipt CAS contended")

    def assert_terminal_anchor_receipt(self, *, run_id: str, account_id: str,
                                       batch_id: str, last_sequence: int,
                                       anchor_hash: str, snapshot_hash: str) -> None:
        _identity(account_id, "account")
        expected = (f"1\n{batch_id}\n{last_sequence}\n{anchor_hash}\n{snapshot_hash}").encode()
        try:
            value, _ = self.keeper.get(_terminal_receipt_path(run_id, account_id))
        except Exception as exc:
            raise KeeperUnavailable("Terminal account receipt is missing") from exc
        if value != expected:
            raise KeeperUnavailable("Terminal account receipt conflicts with ClickHouse")

    def compact_verified_batch(self, *, run_id: str, batch_id: str,
                               prior_batch_id: str, first_sequence: int,
                               last_sequence: int, commit_hash: str,
                               operations: tuple[tuple[str, str], ...]) -> None:
        """Atomically retire sealed operation IDs after exact CH commit readback.

        The caller must supply the just-read typed commit's identity/hash.
        The Keeper watermark enforces a contiguous batch chain; later retries
        at/below it never re-dispatch and must still pass CH exact readback.
        """
        if (not operations or len(set(operations)) != len(operations)
                or type(first_sequence) is not int or type(last_sequence) is not int
                or first_sequence < 1 or last_sequence < first_sequence
                or re.fullmatch(r"[0-9a-f]{64}", commit_hash) is None):
            raise ValueError("Typed batch compaction identity is invalid")
        try:
            if str(UUID(batch_id)) != batch_id or str(UUID(prior_batch_id)) != prior_batch_id:
                raise ValueError("noncanonical batch UUID")
        except (TypeError, ValueError) as exc:
            raise ValueError("Typed batch compaction batch UUID is invalid") from exc
        for _ in range(8):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open" or gate.inflight:
                raise KeeperUnavailable("Typed batch compaction has unresolved INSERTs")
            if last_sequence <= gate.compacted_through:
                if (last_sequence == gate.compacted_through
                        and batch_id == gate.compacted_batch_id
                        and commit_hash == gate.compacted_commit_hash):
                    return
                raise KeeperUnavailable("Typed batch retry precedes compacted watermark")
            if (first_sequence != gate.compacted_through + 1
                    or prior_batch_id != gate.compacted_batch_id):
                raise KeeperUnavailable("Typed batch does not extend compacted prefix")
            if gate.active_batch_id != batch_id:
                raise KeeperUnavailable("Competing typed batch owns the run prefix")
            paths = []
            for table, token in operations:
                path = _operation_path(run_id, typed_insert_query_id(run_id, table, token))
                try:
                    value, stat = self.keeper.get(path)
                except Exception as exc:
                    raise KeeperUnavailable("Typed batch lacks sealed dispatch operation") from exc
                parts = value.decode("utf-8").split("\n")
                if (len(parts) != 9 or parts[:5] != ["3", run_id, table,
                        typed_insert_query_id(run_id, table, token),
                        sha256(token.encode()).hexdigest()]
                        or parts[6:] != [batch_id, str(last_sequence), "sealed"]):
                    raise KeeperUnavailable("Typed batch operation differs from sealed prefix")
                paths.append((path, stat.version))
            if gate.registered < len(paths):
                raise KeeperUnavailable("Typed dispatch operation count is corrupt")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            for path, op_version in paths:
                txn.delete(path, version=op_version)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", 0, gate.epoch, gate.registered - len(paths),
                last_sequence, batch_id, commit_hash, _ZERO_BATCH).wire(),
                version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Typed batch compaction CAS contended")

    def acquire_cold_barrier(self, run_id: str) -> "ColdDispatchBarrier":
        gate, version = self._read_gate(run_id)
        if (gate.mode != "open" or gate.inflight or gate.registered
                or gate.active_batch_id != _ZERO_BATCH):
            raise KeeperUnavailable("Typed dispatch has pending or ambiguous INSERTs")
        closed = _Gate("closed", 0, gate.epoch + 1, gate.registered,
                       gate.compacted_through, gate.compacted_batch_id,
                       gate.compacted_commit_hash, gate.active_batch_id)
        txn = self.keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.set_data(_gate_path(run_id), closed.wire(), version=version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("Typed dispatch cold barrier lost CAS race")
        barrier = ColdDispatchBarrier(self, run_id, closed.epoch)
        barrier._assert_gate(run_id)
        return barrier


@dataclass
class ColdDispatchBarrier:
    authority: TypedInsertDispatch
    run_id: str
    epoch: int
    prefix_verified: bool = False
    context_verified: bool = False

    def _assert_gate(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise KeeperUnavailable("Typed dispatch barrier run differs")
        gate, _ = self.authority._read_gate(run_id)
        if gate != _Gate("closed", 0, self.epoch, gate.registered,
                         gate.compacted_through, gate.compacted_batch_id,
                         gate.compacted_commit_hash, gate.active_batch_id):
            raise KeeperUnavailable("Typed dispatch cold barrier was lost")

    def assert_fenced(self, run_id: str) -> None:
        if not self.prefix_verified:
            raise KeeperUnavailable("Typed dispatch prefix is not cold-verified")
        if not self.context_verified:
            raise KeeperUnavailable("Typed dispatch run context is not cold-verified")
        self._assert_gate(run_id)

    def verify_run_context_receipt(self, client: Any) -> dict[str, Any]:
        """Cold-read exact normalized context and its durable Keeper receipt."""
        from src.trading_runtime.arte_journal_writer import (
            _literal, _rows, load_typed_run_context,
        )
        from src.trading_runtime.journal_contract import canonical_json

        self._assert_gate(self.run_id)
        self.context_verified = False
        context = load_typed_run_context(client, self.run_id)
        columns = "run_id,run_month,run_hash,config_hash,account_count,account_hash"
        rows = _rows(client,
            f"SELECT {columns} FROM arte.trading_run_context_commit_v1 "
            f"WHERE run_id={_literal(self.run_id)} FORMAT JSONEachRow")
        if len(rows) != 1:
            raise KeeperUnavailable("Cold run-context fence is absent or duplicated")
        fence_hash = sha256(canonical_json(rows[0]).encode()).hexdigest()
        self.authority.assert_run_context_receipt(
            run_id=self.run_id, fence_hash=fence_hash)
        self._assert_gate(self.run_id)
        self.context_verified = True
        return context

    def verify_terminal_anchor_receipt(self, client: Any, prefix: Any, *,
                                       account_id: str) -> dict[str, Any]:
        """Return terminal state only after CH anchor and Keeper receipt agree."""
        from src.trading_runtime.arte_backtest_snapshot_anchor import (
            _stored, load_terminal_backtest_snapshot,
        )
        from src.trading_runtime.journal_contract import canonical_json

        self.assert_fenced(self.run_id)
        if prefix.run_id != self.run_id:
            raise KeeperUnavailable("Terminal anchor prefix differs from cold run")
        snapshot = load_terminal_backtest_snapshot(
            client, prefix, account_id=account_id)
        anchors = _stored(client, self.run_id, account_id)
        if len(anchors) != 1:
            raise KeeperUnavailable("Terminal anchor is absent or duplicated")
        anchor_hash = sha256(canonical_json(anchors[0]).encode()).hexdigest()
        self.authority.assert_terminal_anchor_receipt(
            run_id=self.run_id, account_id=account_id,
            batch_id=prefix.last_batch_id, last_sequence=prefix.last_sequence,
            anchor_hash=anchor_hash, snapshot_hash=snapshot["state_hash"])
        self.assert_fenced(self.run_id)
        return snapshot

    def verify_portfolio_snapshot_head(self, client: Any, *,
                                       account_id: str) -> dict[str, Any]:
        """Cold-read latest account snapshot and match its exact Keeper head."""
        from src.trading_runtime.arte_portfolio_snapshot import (
            _SNAPSHOT_COMMIT, _stored_rows, load_latest_portfolio_snapshot,
        )
        from src.trading_runtime.journal_contract import canonical_json

        self.assert_fenced(self.run_id)
        snapshot = load_latest_portfolio_snapshot(
            client, run_id=self.run_id, account_id=account_id)
        if snapshot is None:
            raise KeeperUnavailable("Cold portfolio account has no committed snapshot")
        revision = snapshot["state_revision"]
        rows = _stored_rows(client, _SNAPSHOT_COMMIT, self.run_id,
                            account_id, revision)
        if len(rows) != 1:
            raise KeeperUnavailable("Cold portfolio snapshot fence is absent or duplicated")
        stable = {key: value for key, value in rows[0].items()
                  if key != "committed_at"}
        digest = sha256(canonical_json(stable).encode()).hexdigest()
        self.authority.assert_snapshot_head(
            run_id=self.run_id, account_id=account_id,
            revision=revision, fence_hash=digest)
        self.assert_fenced(self.run_id)
        return snapshot

    def verify_committed_prefix(self, client: Any, *,
                                journal_profile: str) -> Any:
        """Scan the selected CH prefix once and bind its terminal commit to Keeper."""
        from src.trading_runtime.arte_journal_writer import (
            _COMMIT_COLUMNS, _literal, _rows, load_committed_prefix,
        )
        from src.trading_runtime.journal_contract import canonical_json

        if journal_profile not in {"v1", "backtest_v2"}:
            raise ValueError("Cold dispatch needs an explicit typed journal profile")
        self.prefix_verified = False
        commit_table = ("trading_commit_v2" if journal_profile == "backtest_v2"
                        else "trading_commit_v1")
        other_table = ("trading_commit_v1" if journal_profile == "backtest_v2"
                       else "trading_commit_v2")
        self._assert_gate(self.run_id)
        mixed = _rows(client,
            f"SELECT batch_id FROM arte.{other_table} "
            f"WHERE run_id={_literal(self.run_id)} LIMIT 1 FORMAT JSONEachRow")
        if mixed:
            raise KeeperUnavailable("Cold dispatch cannot mix V1 and V2 commit fences")
        prefix = load_committed_prefix(client, self.run_id,
                                       journal_profile=journal_profile)
        gate, _ = self.authority._read_gate(self.run_id)
        if gate.compacted_through == 0:
            if prefix is not None:
                raise KeeperUnavailable("ClickHouse prefix lacks dispatch compaction")
        else:
            if (prefix is None or prefix.last_sequence != gate.compacted_through
                    or prefix.last_batch_id != gate.compacted_batch_id):
                raise KeeperUnavailable("ClickHouse prefix differs from dispatch watermark")
            rows = _rows(client,
                f"SELECT {','.join(_COMMIT_COLUMNS)} FROM arte.{commit_table} "
                f"WHERE run_id={_literal(self.run_id)} "
                f"AND batch_id=toUUID({_literal(gate.compacted_batch_id)}) "
                "FORMAT JSONEachRow")
            if (len(rows) != 1 or
                    sha256(canonical_json(rows[0]).encode()).hexdigest()
                    != gate.compacted_commit_hash):
                raise KeeperUnavailable("ClickHouse commit differs from dispatch hash")
        self._assert_gate(self.run_id)
        self.prefix_verified = True
        return prefix

    def release(self) -> None:
        gate, version = self.authority._read_gate(self.run_id)
        if gate != _Gate("closed", 0, self.epoch, gate.registered,
                         gate.compacted_through, gate.compacted_batch_id,
                         gate.compacted_commit_hash, gate.active_batch_id):
            raise KeeperUnavailable("Typed dispatch cold barrier was lost")
        txn = self.authority.keeper.transaction()
        txn.check(_gate_path(self.run_id), version=version)
        txn.set_data(_gate_path(self.run_id),
                     _Gate("open", 0, self.epoch, gate.registered,
                           gate.compacted_through, gate.compacted_batch_id,
                           gate.compacted_commit_hash, gate.active_batch_id).wire(),
                     version=version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("Typed dispatch cold barrier release lost CAS race")
