"""Inactive Keeper-fenced transport for typed arte INSERTs.

No recovery claim is valid for a run that ever used an unwrapped writer. A
lost HTTP response leaves a durable pending operation and prevents cold drain.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from time import sleep
from typing import Any
from uuid import UUID

from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _ROOT, _committed, _identity, _path,
)


_ZERO_BATCH = "00000000-0000-0000-0000-000000000000"
_ZERO_HASH = "0" * 64
_REGISTER_CAS_ATTEMPTS = 64
_ACK_CAS_ATTEMPTS = 64
_SNAPSHOT_TABLES = frozenset({
    "trading_portfolio_snapshot_v1", "trading_portfolio_disabled_strategy_v1",
    "trading_portfolio_command_v1", "trading_portfolio_request_v1",
    "trading_portfolio_request_reason_v1", "trading_portfolio_reservation_v1",
    "trading_portfolio_allocation_v1", "trading_portfolio_reconciliation_v1",
    "trading_portfolio_snapshot_commit_v1",
})
_POLICY_TABLES = frozenset({
    "trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2",
    "trading_portfolio_policy_security_type_v1",
    "trading_portfolio_policy_currency_v1",
    "trading_portfolio_policy_restricted_symbol_v1",
    "trading_portfolio_policy_execution_policy_v1",
    "trading_portfolio_policy_protection_profile_v1",
})
_MANAGER_TABLES = frozenset({
    "trading_strategy_one_protection_snapshot_v1",
    "trading_strategy_one_protection_state_v1",
    "trading_strategy_one_protection_resistance_v1",
    "trading_strategy_one_manager_snapshot_v2",
    "trading_strategy_one_manager_snapshot_v3",
    "trading_strategy_one_manager_first_held_v1",
    "trading_strategy_one_manager_source_v2",
    "trading_strategy_one_manager_pending_break_v2",
    "trading_strategy_one_manager_position_high_v2",
    "trading_strategy_one_manager_closed_position_v2",
})


def _manager_tables(confirmed_original_risk_policy=None):
    if confirmed_original_risk_policy is None:
        return _MANAGER_TABLES
    from .original_risk_diagnostic_profile import require_original_risk_diagnostic_policy
    from .original_risk_pending_snapshot import selected_snapshot_contracts
    require_original_risk_diagnostic_policy(confirmed_original_risk_policy)
    return _MANAGER_TABLES | frozenset(t.name for t in selected_snapshot_contracts())
_BROKER_MATCH_TABLES = frozenset({
    "trading_strategy_one_broker_match_snapshot_v5",
    "trading_strategy_one_broker_match_account_v5",
    "trading_strategy_one_broker_match_position_v5",
    "trading_strategy_one_broker_match_open_order_v5",
    "trading_strategy_one_broker_match_ticker_v5",
    "trading_strategy_one_broker_match_performance_mark_v5",
})
_EVIDENCE_TABLES = frozenset({
    "trading_strategy_one_evidence_snapshot_v1",
    "trading_strategy_one_evidence_resistance_v1",
    "trading_strategy_one_evidence_known_v1",
    "trading_strategy_one_evidence_activation_v1",
    "trading_strategy_one_evidence_activation_level_v1",
    "trading_strategy_one_evidence_30s_low_v1",
})
_CAMPAIGN_TABLES = frozenset({
    "trading_strategy_one_campaign_snapshot_v1",
    "trading_strategy_one_campaign_owner_v1",
})
_OMS_OBSERVATION_TABLES = frozenset({
    "trading_strategy_one_oms_observation_snapshot_v2",
    "trading_strategy_one_oms_observation_v2",
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


def _manager_head_path(run_id: str) -> str:
    from src.trading_runtime.strategy_one_management_snapshot import (
        ManagedManagerSnapshotHeadReader,
    )
    return ManagedManagerSnapshotHeadReader.path(run_id)


def _broker_match_head_path(run_id: str) -> str:
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        ManagedBrokerMatchHeadReader,
    )
    return ManagedBrokerMatchHeadReader.path(run_id)


def _evidence_head_path(run_id: str) -> str:
    from src.trading_runtime.strategy_one_evidence_snapshot import (
        ManagedEvidenceSnapshotHeadReader,
    )
    return ManagedEvidenceSnapshotHeadReader.path(run_id)


def _campaign_head_path(run_id: str) -> str:
    from src.trading_runtime.strategy_one_campaign_snapshot import (
        ManagedCampaignSnapshotHeadReader,
    )
    return ManagedCampaignSnapshotHeadReader.path(run_id)


def _oms_observation_head_path(run_id: str) -> str:
    from src.trading_runtime.strategy_one_oms_observation_snapshot import (
        ManagedOmsObservationHeadReader,
    )
    return ManagedOmsObservationHeadReader.path(run_id)


def _manager_token(run_id: str, sequence: int, digest: str,
                   table: str) -> str:
    return f"manager-state:{run_id}:{sequence}:{digest}:{table}"


def _broker_match_token(run_id: str, sequence: int, digest: str,
                        table: str) -> str:
    return f"broker-match:{run_id}:{sequence}:{digest}:{table}"


def _running_financial_token(run_id: str, sequence: int, digest: str,
                             table: str) -> str:
    return f"running-financial:{run_id}:{sequence}:{digest}:{table}"


def _evidence_token(run_id: str, sequence: int, digest: str,
                    table: str) -> str:
    return f"evidence-state:{run_id}:{sequence}:{digest}:{table}"


def _campaign_token(run_id: str, sequence: int, digest: str,
                    table: str) -> str:
    return f"campaign-state:{run_id}:{sequence}:{digest}:{table}"


def _oms_observation_token(run_id: str, sequence: int, digest: str,
                           table: str) -> str:
    return f"oms-observation:{run_id}:{sequence}:{digest}:{table}"


def _policy_gate_path(policy_hash: str) -> str:
    return _path("typed_dispatch_policy_gate", policy_hash)


def _policy_operation_path(policy_hash: str, query_id: str) -> str:
    return _path("typed_dispatch_policy_operation", policy_hash, query_id)


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


@dataclass(frozen=True)
class _PolicyGate:
    mode: str
    inflight: int
    registered: int
    fence_hash: str

    def wire(self) -> bytes:
        return (f"1\n{self.mode}\n{self.inflight}\n{self.registered}\n"
                f"{self.fence_hash}").encode()


def _decode_policy_gate(value: bytes) -> _PolicyGate:
    try:
        version, mode, inflight, registered, digest = value.decode("ascii").split("\n")
        gate = _PolicyGate(mode, int(inflight), int(registered), digest)
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("Portfolio policy dispatch gate is corrupt") from exc
    if (version != "1" or mode not in {"publishing", "committed"}
            or gate.inflight < 0 or gate.registered < gate.inflight
            or re.fullmatch(r"[0-9a-f]{64}", gate.fence_hash) is None
            or (mode == "publishing" and gate.fence_hash != _ZERO_HASH)
            or (mode == "committed" and
                (gate.inflight or gate.registered or gate.fence_hash == _ZERO_HASH))
            or gate.wire() != value):
        raise KeeperUnavailable("Portfolio policy dispatch gate is invalid")
    return gate


def _request_bytes(sql: str | bytes) -> bytes:
    """Keeper identity covers the complete request, including binary payload."""
    if isinstance(sql, bytes):
        return sql
    if isinstance(sql, str):
        return sql.encode('utf-8')
    raise TypeError('Typed INSERT request must be text or exact bytes')


def _request_header(sql: str | bytes) -> str:
    # Only the first line is SQL for a RowBinary request. Its opaque payload
    # must neither be searched for settings nor decoded as UTF-8.
    if isinstance(sql, bytes):
        header, separator, _ = sql.partition(b'\n')
        if not separator or not header.endswith(b' FORMAT RowBinary'):
            raise ValueError('Binary typed INSERT requires a RowBinary header')
        return header.decode('utf-8', errors='strict')
    if isinstance(sql, str):
        return sql.partition('\n')[0]
    raise TypeError('Typed INSERT request must be text or exact bytes')


def _operation_wire(run_id: str, table: str, query_id: str,
                    token: str, sql: str | bytes, batch_id: str,
                    sequence: int, status: str) -> bytes:
    if status not in {"pending", "acknowledged", "sealed"}:
        raise ValueError("Typed dispatch operation status is invalid")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("Typed dispatch requires a nonnegative parent sequence")
    return ("3\n" + "\n".join((run_id, table, query_id,
            sha256(token.encode()).hexdigest(), sha256(_request_bytes(sql)).hexdigest(),
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

    def operation_present(self, *, run_id: str, table: str, token: str) -> bool:
        """Inspect an unfinished operation identity; a compacted one is absent."""
        path = _operation_path(run_id, typed_insert_query_id(run_id, table, token))
        try:
            self.keeper.get(path)
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise KeeperUnavailable("Typed dispatch operation cannot be inspected") from exc
        return True

    def _read_policy_gate(self, policy_hash: str) -> tuple[_PolicyGate, int] | None:
        if re.fullmatch(r"[0-9a-f]{64}", policy_hash) is None:
            raise ValueError("Portfolio policy hash is invalid")
        try:
            value, stat = self.keeper.get(_policy_gate_path(policy_hash))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Portfolio policy gate cannot be read") from exc
        return _decode_policy_gate(value), stat.version

    def begin_policy_publication(self, *, policy_hash: str,
                                 has_ch_rows: bool) -> str:
        """Open one immutable policy identity; never adopt legacy CH rows."""
        if type(has_ch_rows) is not bool:
            raise ValueError("Portfolio policy source scan result is invalid")
        observed = self._read_policy_gate(policy_hash)
        if observed is not None:
            return observed[0].mode
        if has_ch_rows:
            raise KeeperUnavailable("Portfolio policy has unattested legacy rows")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_policy_gate")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_policy_operation")
        try:
            self.keeper.create(_policy_gate_path(policy_hash),
                               _PolicyGate("publishing", 0, 0, _ZERO_HASH).wire())
        except Exception as exc:
            if type(exc).__name__ != "NodeExistsError":
                raise KeeperUnavailable("Portfolio policy gate cannot initialize") from exc
        observed = self._read_policy_gate(policy_hash)
        if observed is None:
            raise KeeperUnavailable("Portfolio policy gate disappeared")
        return observed[0].mode

    def execute_policy_insert(self, client: Any, *, policy_hash: str,
                              table: str, token: str, sql: str) -> None:
        if (table not in _POLICY_TABLES
                or token != f"portfolio-policy:{policy_hash}:"
                    f"{'commit' if table == 'trading_portfolio_policy_commit_v2' else table}"
                or not sql.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql
                or f"insert_deduplication_token='{token}'" not in sql):
            raise ValueError("Portfolio policy INSERT identity is invalid")
        query_id = typed_insert_query_id(policy_hash, table, token)
        path = _policy_operation_path(policy_hash, query_id)
        pending = _operation_wire(policy_hash, table, query_id, token, sql,
                                  _ZERO_BATCH, 0, "pending")
        acknowledged = _operation_wire(policy_hash, table, query_id, token, sql,
                                       _ZERO_BATCH, 0, "acknowledged")
        for _ in range(8):
            observed = self._read_policy_gate(policy_hash)
            if observed is None or observed[0].mode != "publishing":
                raise KeeperUnavailable("Portfolio policy is not open for publication")
            gate, version = observed
            try:
                existing, _ = self.keeper.get(path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("Policy operation cannot be inspected") from exc
            else:
                if existing == acknowledged:
                    return
                raise KeeperUnavailable("Portfolio policy has ambiguous pending INSERT")
            if gate.registered >= self.max_operations:
                raise KeeperUnavailable("Portfolio policy dispatch operation cap reached")
            txn = self.keeper.transaction()
            txn.check(_policy_gate_path(policy_hash), version=version)
            txn.create(path, pending, ephemeral=False)
            txn.set_data(_policy_gate_path(policy_hash), _PolicyGate(
                "publishing", gate.inflight + 1, gate.registered + 1,
                _ZERO_HASH).wire(), version=version)
            if _committed(txn.commit()):
                break
        else:
            raise KeeperUnavailable("Portfolio policy dispatch CAS contended")
        registered = getattr(client, "execute_registered_insert", None)
        if registered is None:
            client.execute(sql, query_id=query_id)
        else:
            registered(sql, query_id=query_id)
        for _ in range(8):
            observed = self._read_policy_gate(policy_hash)
            if observed is None or observed[0].mode != "publishing":
                raise KeeperUnavailable("Portfolio policy gate changed before acknowledgement")
            gate, version = observed
            value, stat = self.keeper.get(path)
            if value != pending:
                raise KeeperUnavailable("Portfolio policy operation changed before acknowledgement")
            txn = self.keeper.transaction()
            txn.check(_policy_gate_path(policy_hash), version=version)
            txn.set_data(path, acknowledged, version=stat.version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Portfolio policy acknowledgement CAS contended")

    def seal_verified_policy_operation(self, *, policy_hash: str,
                                       table: str, token: str) -> None:
        """Invoke only after exact normalized policy fence readback."""
        query_id = typed_insert_query_id(policy_hash, table, token)
        path = _policy_operation_path(policy_hash, query_id)
        for _ in range(8):
            observed = self._read_policy_gate(policy_hash)
            if observed is None or observed[0].mode != "publishing":
                raise KeeperUnavailable("Portfolio policy has no open dispatch gate")
            gate, version = observed
            try:
                value, stat = self.keeper.get(path)
            except Exception as exc:
                raise KeeperUnavailable("Portfolio policy lacks dispatch operation") from exc
            parts = value.decode().split("\n")
            if (len(parts) != 9 or parts[:5] != ["3", policy_hash, table, query_id,
                    sha256(token.encode()).hexdigest()]
                    or parts[6:] not in ([_ZERO_BATCH, "0", "acknowledged"],
                                         [_ZERO_BATCH, "0", "sealed"])):
                raise KeeperUnavailable("Portfolio policy operation identity differs")
            if parts[8] == "sealed":
                return
            if gate.inflight < 1:
                raise KeeperUnavailable("Portfolio policy acknowledgement count is invalid")
            txn = self.keeper.transaction()
            txn.check(_policy_gate_path(policy_hash), version=version)
            txn.set_data(path, ("\n".join((*parts[:8], "sealed"))).encode(),
                         version=stat.version)
            txn.set_data(_policy_gate_path(policy_hash), _PolicyGate(
                "publishing", gate.inflight - 1, gate.registered,
                _ZERO_HASH).wire(), version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Portfolio policy operation seal CAS contended")

    def compact_verified_policy(self, *, policy_hash: str, fence_hash: str,
                                operations: tuple[tuple[str, str], ...]) -> None:
        """Atomically retire policy operations; retain one immutable receipt."""
        if (re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
                or not operations or len(set(operations)) != len(operations)):
            raise ValueError("Portfolio policy receipt identity is invalid")
        for _ in range(8):
            observed = self._read_policy_gate(policy_hash)
            if observed is None:
                raise KeeperUnavailable("Portfolio policy lacks dispatch gate")
            gate, version = observed
            if gate.mode == "committed":
                if gate.fence_hash != fence_hash:
                    raise KeeperUnavailable("Portfolio policy receipt conflicts with fence")
                return
            if gate.inflight or gate.registered != len(operations):
                raise KeeperUnavailable("Portfolio policy has unresolved dispatch operations")
            paths = []
            for table, token in operations:
                suffix = "commit" if table == "trading_portfolio_policy_commit_v2" else table
                if (table not in _POLICY_TABLES
                        or token != f"portfolio-policy:{policy_hash}:{suffix}"):
                    raise KeeperUnavailable("Portfolio policy operation inventory is invalid")
                query_id = typed_insert_query_id(policy_hash, table, token)
                path = _policy_operation_path(policy_hash, query_id)
                try:
                    value, stat = self.keeper.get(path)
                except Exception as exc:
                    raise KeeperUnavailable("Portfolio policy lacks sealed dispatch operation") from exc
                parts = value.decode().split("\n")
                if (len(parts) != 9 or parts[:5] != ["3", policy_hash, table,
                        query_id, sha256(token.encode()).hexdigest()]
                        or parts[6:] != [_ZERO_BATCH, "0", "sealed"]):
                    raise KeeperUnavailable("Portfolio policy operation is not sealed")
                paths.append((path, stat.version))
            txn = self.keeper.transaction()
            txn.check(_policy_gate_path(policy_hash), version=version)
            for path, op_version in paths:
                txn.delete(path, version=op_version)
            txn.set_data(_policy_gate_path(policy_hash), _PolicyGate(
                "committed", 0, 0, fence_hash).wire(), version=version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Portfolio policy receipt CAS contended")

    def assert_policy_receipt(self, *, policy_hash: str,
                              fence_hash: str) -> None:
        observed = self._read_policy_gate(policy_hash)
        if (observed is None or observed[0] != _PolicyGate(
                "committed", 0, 0, fence_hash)):
            raise KeeperUnavailable("Portfolio policy receipt differs from ClickHouse")

    def assert_policy_absent(self, *, policy_hash: str) -> None:
        if self._read_policy_gate(policy_hash) is not None:
            raise KeeperUnavailable("Portfolio policy Keeper fact lacks ClickHouse fence")

    def initialize_new_run(self, run_id: str) -> None:
        """Only a fresh-run bootstrap may call this; never repair a missing gate."""
        _identity(run_id, "run")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_gate")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_operation")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_context_receipt")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_terminal_receipt")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_snapshot_head")
        self.keeper.ensure_path(f"{_ROOT}/typed_dispatch_policy_gate")
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
                             token: str, sql: str | bytes,
                             batch_id: str | None = None,
                             batch_last_sequence: int | None = None,
                             terminal_account_id: str | None = None,
                             snapshot_account_id: str | None = None,
                             manager_snapshot_hash: str | None = None,
                             fixed_lot_manager_context: Any | None = None,
                             broker_snapshot_hash: str | None = None,
                             evidence_snapshot_hash: str | None = None,
                             campaign_snapshot_hash: str | None = None,
                             oms_observation_snapshot_hash: str | None = None,
                             running_financial_checkpoint_hash: str | None = None) -> None:
        header = _request_header(sql)
        if fixed_lot_manager_context is not None and manager_snapshot_hash is None:
            raise ValueError('Selected manager dispatch lacks selected parent hash')
        if (re.fullmatch(r"[a-z][a-z0-9_]*", table) is None
                or not header.startswith(f"INSERT INTO arte.{table} (")
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1"
                   not in header
                or "insert_deduplication_token=" not in header):
            raise ValueError("Typed dispatch requires the acknowledged arte INSERT contract")
        if (type(batch_last_sequence) is not int or batch_last_sequence < 0
                or not isinstance(batch_id, str)
                or (snapshot_account_id is None and manager_snapshot_hash is None
                    and broker_snapshot_hash is None
                    and evidence_snapshot_hash is None
                    and campaign_snapshot_hash is None
                    and running_financial_checkpoint_hash is None
                    and oms_observation_snapshot_hash is None and
                    (batch_last_sequence == 0) != (batch_id == _ZERO_BATCH))
                or (snapshot_account_id is not None and batch_id != _ZERO_BATCH)):
            raise KeeperUnavailable("Strict typed dispatch lacks batch sequence authority")
        if sum(value is not None for value in (
                terminal_account_id, snapshot_account_id,
                manager_snapshot_hash, broker_snapshot_hash,
                evidence_snapshot_hash, campaign_snapshot_hash,
                oms_observation_snapshot_hash, running_financial_checkpoint_hash)) > 1:
            raise ValueError("Typed INSERT has multiple parent families")
        scalar_hash = next((value for value in (
            manager_snapshot_hash, broker_snapshot_hash,
            evidence_snapshot_hash, campaign_snapshot_hash,
            oms_observation_snapshot_hash, running_financial_checkpoint_hash)
            if value is not None), None)
        if scalar_hash is not None:
            if fixed_lot_manager_context is not None:
                from .fixed_structural_lot_manager_snapshot import require_manager_publication
                selected=require_manager_publication(fixed_lot_manager_context,client=client)
                from .fixed_structural_lot_manager_schema import PARENT
                if ((run_id,batch_last_sequence,batch_id,manager_snapshot_hash)!=(
                        fixed_lot_manager_context.run_id,fixed_lot_manager_context.sequence,
                        fixed_lot_manager_context.batch_id,selected[PARENT.name][0]['content_hash'])):
                    raise ValueError('Selected manager dispatch differs from issued cursor')
            tables = (frozenset(selected) if fixed_lot_manager_context is not None
                      else _manager_tables(getattr(client,'confirmed_original_risk_policy',None))
                      if manager_snapshot_hash is not None
                      else _BROKER_MATCH_TABLES if broker_snapshot_hash is not None
                      else _EVIDENCE_TABLES if evidence_snapshot_hash is not None
                      else _CAMPAIGN_TABLES if campaign_snapshot_hash is not None
                      else _OMS_OBSERVATION_TABLES if oms_observation_snapshot_hash is not None
                      else frozenset({'trading_running_financial_checkpoint_v1',
                                      'trading_running_financial_checkpoint_account_v1'}))
            expected_token = (_manager_token if manager_snapshot_hash is not None
                              else _broker_match_token if broker_snapshot_hash is not None
                              else _evidence_token if evidence_snapshot_hash is not None
                              else _campaign_token if campaign_snapshot_hash is not None
                              else _oms_observation_token if oms_observation_snapshot_hash is not None
                              else _running_financial_token)
            if (table not in tables or batch_last_sequence < 1
                    or re.fullmatch(r"[0-9a-f]{64}", scalar_hash) is None
                    or token != expected_token(
                        run_id, batch_last_sequence, scalar_hash, table)):
                raise KeeperUnavailable("Scalar snapshot dispatch identity is invalid")
            try:
                if str(UUID(batch_id)) != batch_id or batch_id == _ZERO_BATCH:
                    raise ValueError("noncanonical UUID")
            except (TypeError, ValueError) as exc:
                raise KeeperUnavailable("Scalar snapshot batch ID is invalid") from exc
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
        for attempt in range(_REGISTER_CAS_ATTEMPTS):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open":
                raise KeeperUnavailable("Typed dispatch run is cold-fenced")
            if scalar_hash is not None:
                self._read_context_receipt(run_id)
                if (gate.active_batch_id != _ZERO_BATCH
                        or gate.compacted_through != batch_last_sequence
                        or gate.compacted_batch_id != batch_id):
                    raise KeeperUnavailable(
                        "Scalar snapshot differs from compacted running prefix")
            elif snapshot_account_id is not None:
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
            if (scalar_hash is None and snapshot_account_id is None
                    and terminal_account_id is None and batch_last_sequence
                    and batch_last_sequence <= gate.compacted_through):
                return  # Exact CH batch readback and watermark check still follow.
            if (scalar_hash is None and snapshot_account_id is None
                    and terminal_account_id is None and not batch_last_sequence
                    and gate.active_batch_id != _ZERO_BATCH):
                raise KeeperUnavailable("Run context cannot dispatch during a batch")
            if (scalar_hash is None and snapshot_account_id is None
                    and terminal_account_id is None and not batch_last_sequence):
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
            # Only a proven optimistic transaction conflict is retried. Separate
            # family lanes share this gate; immediate retries can repeatedly
            # collide before any lane has time to publish its registration.
            if attempt + 1 < _REGISTER_CAS_ATTEMPTS:
                sleep(min(0.001 * (attempt + 1), 0.01))
        else:
            raise KeeperUnavailable("Typed dispatch gate CAS contended")
        # Do not catch/clear transport errors: the server may still commit.
        registered = getattr(client, "execute_registered_insert", None)
        if registered is None:
            client.execute(sql, query_id=query_id)
        else:
            registered(sql, query_id=query_id)
        for attempt in range(_ACK_CAS_ATTEMPTS):
            gate, version = self._read_gate(run_id)
            if gate.mode != "open" or gate.inflight < 1:
                raise KeeperUnavailable("Typed dispatch gate changed before acknowledgement")
            stored, stat = self.keeper.get(path)
            if stored != pending:
                raise KeeperUnavailable("Typed dispatch operation changed before acknowledgement")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=version)
            # ACK changes only this operation. Checking the gate's version
            # fences a concurrent close; rewriting identical gate bytes would
            # needlessly invalidate other independent family ACKs.
            txn.set_data(path, completed, version=stat.version)
            if _committed(txn.commit()):
                return
            # Independent family lanes can register or acknowledge while this
            # lane checks the shared gate. Yield only on a proven optimistic
            # conflict; transport and ambiguous INSERT errors still fail closed.
            if attempt + 1 < _ACK_CAS_ATTEMPTS:
                sleep(min(0.001 * (attempt + 1), 0.01))
        raise KeeperUnavailable("Typed dispatch acknowledgement CAS contended")

    def seal_verified_operation(self, *, run_id: str, table: str, token: str,
                                sql: str | bytes | None = None,
                                required: bool = True,
                                batch_id: str | None = None,
                                batch_last_sequence: int | None = None,
                                terminal: bool = False,
                                snapshot: bool = False,
                                manager_snapshot: bool = False,
                                broker_snapshot: bool = False,
                                evidence_snapshot: bool = False,
                                campaign_snapshot: bool = False,
                                oms_observation_snapshot: bool = False,
                                running_financial_checkpoint: bool = False) -> None:
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
            if (not terminal and not snapshot and not manager_snapshot
                    and not broker_snapshot and not evidence_snapshot
                    and not campaign_snapshot and not oms_observation_snapshot
                    and not running_financial_checkpoint
                    and type(batch_last_sequence) is int and batch_last_sequence > 0
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
                    or (sql is not None and parts[5] != sha256(_request_bytes(sql)).hexdigest())):
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

    def compact_verified_manager_snapshot(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None, confirmed_original_risk_policy=None,
    ) -> None:
        """Select read-back-verified scalar rows at one compacted V4 cursor.

        The caller must verify every selected ClickHouse row before invoking
        this CAS. Until it succeeds, orphan rows have no recovery authority.
        """
        from src.trading_runtime.strategy_one_management_snapshot import (
            ManagerSnapshotHead,
        )
        _identity(run_id, "run")
        operation_tables={table for table,_ in operations}
        legacy_roots={'trading_strategy_one_manager_snapshot_v2','trading_strategy_one_manager_snapshot_v3'}
        if confirmed_original_risk_policy is None:
            root_invalid=(len(operation_tables & legacy_roots)!=1
                or ('trading_strategy_one_manager_first_held_v1' in operation_tables
                    and 'trading_strategy_one_manager_snapshot_v3' not in operation_tables))
        else:
            _manager_tables(confirmed_original_risk_policy)
            root_invalid=('trading_strategy_one_manager_snapshot_v4' not in operation_tables
                          or bool(operation_tables & legacy_roots))
        if (type(last_sequence) is not int or last_sequence < 1
                or re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None
                or not operations or len(set(operations)) != len(operations)
                or "trading_strategy_one_protection_snapshot_v1"
                not in {table for table, _ in operations}
                or root_invalid):
            raise ValueError("Manager snapshot operation inventory is invalid")
        try:
            if str(UUID(batch_id)) != batch_id or batch_id == _ZERO_BATCH:
                raise ValueError("zero or noncanonical batch")
        except (TypeError, ValueError) as exc:
            raise ValueError("Manager snapshot batch ID is invalid") from exc
        if previous is not None and (
                not isinstance(previous, ManagerSnapshotHead)
                or previous.run_id != run_id
                or not 0 < previous.checkpoint_sequence < last_sequence):
            raise ValueError("Manager snapshot previous head is invalid")
        self._read_context_receipt(run_id)
        path = _manager_head_path(run_id)
        wire = (f"1\n{run_id}\n{last_sequence}\n{batch_id}\n"
                f"{snapshot_hash}").encode("utf-8")
        self.keeper.ensure_path(path.rsplit("/", 1)[0])
        for _ in range(8):
            gate, gate_version = self._read_gate(run_id)
            if (gate.mode != "open" or gate.inflight
                    or gate.active_batch_id != _ZERO_BATCH
                    or gate.compacted_through != last_sequence
                    or gate.compacted_batch_id != batch_id):
                raise KeeperUnavailable(
                    "Manager snapshot lacks quiescent compacted V4 prefix")
            try:
                current_raw, current_stat = self.keeper.get(path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable("Manager snapshot head cannot be read") from exc
                current_raw, current_stat = None, None
            if current_raw == wire and not gate.registered:
                return
            if previous is None:
                if current_raw is not None:
                    raise KeeperUnavailable("Manager snapshot head already exists")
            elif (current_raw != (f"1\n{run_id}\n"
                    f"{previous.checkpoint_sequence}\n"
                    f"{previous.journal_batch_id}\n"
                    f"{previous.snapshot_hash}").encode("utf-8")
                    or current_stat.version != previous.keeper_version):
                raise KeeperUnavailable("Manager snapshot previous head changed")
            paths = []
            for table, token in operations:
                if (table not in _manager_tables(confirmed_original_risk_policy)
                        or token != _manager_token(
                            run_id, last_sequence, snapshot_hash, table)):
                    raise KeeperUnavailable("Manager snapshot operation identity differs")
                query_id = typed_insert_query_id(run_id, table, token)
                op_path = _operation_path(run_id, query_id)
                try:
                    value, stat = self.keeper.get(op_path)
                except Exception as exc:
                    raise KeeperUnavailable(
                        "Manager snapshot dispatch operation is missing") from exc
                parts = value.decode("utf-8").split("\n")
                if (len(parts) != 9
                        or parts[:5] != ["3", run_id, table, query_id,
                            sha256(token.encode()).hexdigest()]
                        or parts[6:] != [batch_id, str(last_sequence), "sealed"]):
                    raise KeeperUnavailable("Manager snapshot operation is not sealed")
                paths.append((op_path, stat.version))
            if gate.registered != len(paths):
                raise KeeperUnavailable(
                    "Manager snapshot has unresolved dispatch operations")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=gate_version)
            for op_path, op_version in paths:
                txn.delete(op_path, version=op_version)
            if current_raw is None:
                txn.create(path, wire)
            else:
                txn.set_data(path, wire, version=current_stat.version)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", 0, gate.epoch, 0, gate.compacted_through,
                gate.compacted_batch_id, gate.compacted_commit_hash,
                _ZERO_BATCH).wire(), version=gate_version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable("Manager snapshot head CAS contended")

    def compact_verified_fixed_lot_manager_snapshot(self,*,client,context,operations,previous):
        from .fixed_structural_lot_manager_snapshot import require_manager_publication,selected_manager_head_path
        from .fixed_structural_lot_manager_schema import PARENT
        from .strategy_one_management_snapshot import ManagerSnapshotHead
        rows=require_manager_publication(context,client=client)
        seal=rows[PARENT.name][0]
        expected=tuple((table,_manager_token(context.run_id,context.sequence,
            seal['content_hash'],table)) for table,values in rows.items() if values)
        if set(operations)!=set(expected) or len(operations)!=len(expected):
            raise ValueError('Selected manager compaction lacks complete issued inventory')
        self._compact_verified_checkpoint_family(run_id=context.run_id,batch_id=context.batch_id,
            last_sequence=context.sequence,snapshot_hash=seal['content_hash'],operations=operations,
            previous=previous,head_type=ManagerSnapshotHead,head_path=selected_manager_head_path(context.run_id),
            tables=frozenset(rows),root_table=PARENT.name,token_factory=_manager_token,
            label='Fixed structural lot manager snapshot')

    def _compact_verified_checkpoint_family(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None, head_type: type, head_path: str,
        tables: frozenset[str], root_table: str,
        token_factory: Any, label: str,
    ) -> None:
        """CAS a read-back-verified scalar family under the compacted V4 gate."""
        _identity(run_id, "run")
        if (type(last_sequence) is not int or last_sequence < 1
                or re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None
                or not operations or len(set(operations)) != len(operations)
                or root_table not in {table for table, _ in operations}):
            raise ValueError(f"{label} operation inventory is invalid")
        try:
            if str(UUID(batch_id)) != batch_id or batch_id == _ZERO_BATCH:
                raise ValueError("zero or noncanonical batch")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} batch ID is invalid") from exc
        if previous is not None and (
                not isinstance(previous, head_type)
                or previous.run_id != run_id
                or not 0 < previous.checkpoint_sequence < last_sequence):
            raise ValueError(f"{label} previous head is invalid")
        self._read_context_receipt(run_id)
        wire = (f"1\n{run_id}\n{last_sequence}\n{batch_id}\n"
                f"{snapshot_hash}").encode("utf-8")
        self.keeper.ensure_path(head_path.rsplit("/", 1)[0])
        for _ in range(8):
            gate, gate_version = self._read_gate(run_id)
            if (gate.mode != "open" or gate.inflight
                    or gate.active_batch_id != _ZERO_BATCH
                    or gate.compacted_through != last_sequence
                    or gate.compacted_batch_id != batch_id):
                raise KeeperUnavailable(
                    f"{label} lacks quiescent compacted V4 prefix")
            try:
                current_raw, current_stat = self.keeper.get(head_path)
            except Exception as exc:
                if type(exc).__name__ != "NoNodeError":
                    raise KeeperUnavailable(f"{label} head cannot be read") from exc
                current_raw, current_stat = None, None
            if current_raw == wire and not gate.registered:
                return
            if previous is None:
                if current_raw is not None:
                    raise KeeperUnavailable(f"{label} head already exists")
            elif (current_raw != (f"1\n{run_id}\n"
                    f"{previous.checkpoint_sequence}\n"
                    f"{previous.journal_batch_id}\n"
                    f"{previous.snapshot_hash}").encode("utf-8")
                    or current_stat.version != previous.keeper_version):
                raise KeeperUnavailable(f"{label} previous head changed")
            paths = []
            for table, token in operations:
                if (table not in tables
                        or token != token_factory(
                            run_id, last_sequence, snapshot_hash, table)):
                    raise KeeperUnavailable(f"{label} operation identity differs")
                query_id = typed_insert_query_id(run_id, table, token)
                op_path = _operation_path(run_id, query_id)
                try:
                    value, stat = self.keeper.get(op_path)
                except Exception as exc:
                    raise KeeperUnavailable(
                        f"{label} dispatch operation is missing") from exc
                parts = value.decode("utf-8").split("\n")
                if (len(parts) != 9
                        or parts[:5] != ["3", run_id, table, query_id,
                            sha256(token.encode()).hexdigest()]
                        or parts[6:] != [batch_id, str(last_sequence), "sealed"]):
                    raise KeeperUnavailable(f"{label} operation is not sealed")
                paths.append((op_path, stat.version))
            if gate.registered != len(paths):
                raise KeeperUnavailable(
                    f"{label} has unresolved dispatch operations")
            txn = self.keeper.transaction()
            txn.check(_gate_path(run_id), version=gate_version)
            for op_path, op_version in paths:
                txn.delete(op_path, version=op_version)
            if current_raw is None:
                txn.create(head_path, wire)
            else:
                txn.set_data(head_path, wire, version=current_stat.version)
            txn.set_data(_gate_path(run_id), _Gate(
                "open", 0, gate.epoch, 0, gate.compacted_through,
                gate.compacted_batch_id, gate.compacted_commit_hash,
                _ZERO_BATCH).wire(), version=gate_version)
            if _committed(txn.commit()):
                return
        raise KeeperUnavailable(f"{label} head CAS contended")

    def compact_verified_evidence_snapshot(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None,
    ) -> None:
        from src.trading_runtime.strategy_one_evidence_snapshot import (
            EvidenceSnapshotHead,
        )
        self._compact_verified_checkpoint_family(
            run_id=run_id, batch_id=batch_id, last_sequence=last_sequence,
            snapshot_hash=snapshot_hash, operations=operations,
            previous=previous, head_type=EvidenceSnapshotHead,
            head_path=_evidence_head_path(run_id), tables=_EVIDENCE_TABLES,
            root_table="trading_strategy_one_evidence_snapshot_v1",
            token_factory=_evidence_token, label="Evidence snapshot")

    def compact_verified_running_financial_checkpoint(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None,
    ) -> None:
        """Select only read-back-verified running root/account rows at V4 cursor."""
        from src.trading_runtime.running_financial_checkpoint_head import (
            RunningFinancialCheckpointHead, ManagedRunningFinancialCheckpointHeadReader,
        )
        from src.trading_runtime.arte_running_financial_checkpoint_schema import ROOT, TABLES
        # Validate exact current and prior scalar identities before any Keeper I/O.
        RunningFinancialCheckpointHead(run_id, last_sequence, batch_id, snapshot_hash, 0)
        if previous is not None:
            if type(previous) is not RunningFinancialCheckpointHead:
                raise ValueError("Running financial previous head is not exact typed receipt")
            previous.__post_init__()
        if (type(operations) is not tuple
                or any(type(op) is not tuple or len(op) != 2
                       or any(type(value) is not str for value in op) for op in operations)
                or {table for table, _ in operations} != {table.name for table in TABLES}):
            raise ValueError("Running financial checkpoint requires both exact table operations")
        self._compact_verified_checkpoint_family(
            run_id=run_id, batch_id=batch_id, last_sequence=last_sequence,
            snapshot_hash=snapshot_hash, operations=operations,
            previous=previous, head_type=RunningFinancialCheckpointHead,
            head_path=ManagedRunningFinancialCheckpointHeadReader.path(run_id),
            tables=frozenset(table.name for table in TABLES), root_table=ROOT.name,
            token_factory=_running_financial_token, label="Running financial checkpoint")

    def compact_verified_broker_match_snapshot(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None,
    ) -> None:
        """Select only exact read-back-verified broker scalar rows."""
        from src.trading_runtime.strategy_one_broker_match_snapshot import (
            BrokerMatchHead,
        )
        self._compact_verified_checkpoint_family(
            run_id=run_id, batch_id=batch_id, last_sequence=last_sequence,
            snapshot_hash=snapshot_hash, operations=operations,
            previous=previous, head_type=BrokerMatchHead,
            head_path=_broker_match_head_path(run_id), tables=_BROKER_MATCH_TABLES,
            root_table="trading_strategy_one_broker_match_snapshot_v5",
            token_factory=_broker_match_token, label="Broker match")

    def compact_verified_campaign_snapshot(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None,
    ) -> None:
        """Select only exact read-back-verified campaign owner rows."""
        from src.trading_runtime.strategy_one_campaign_snapshot import (
            CampaignSnapshotHead,
        )
        self._compact_verified_checkpoint_family(
            run_id=run_id, batch_id=batch_id, last_sequence=last_sequence,
            snapshot_hash=snapshot_hash, operations=operations,
            previous=previous, head_type=CampaignSnapshotHead,
            head_path=_campaign_head_path(run_id), tables=_CAMPAIGN_TABLES,
            root_table="trading_strategy_one_campaign_snapshot_v1",
            token_factory=_campaign_token, label="Campaign snapshot")

    def compact_verified_oms_observation_snapshot(
        self, *, run_id: str, batch_id: str, last_sequence: int,
        snapshot_hash: str, operations: tuple[tuple[str, str], ...],
        previous: Any | None,
    ) -> None:
        """Select only exact read-back-verified OMS-observed order rows."""
        from src.trading_runtime.strategy_one_oms_observation_snapshot import (
            OmsObservationHead,
        )
        self._compact_verified_checkpoint_family(
            run_id=run_id, batch_id=batch_id, last_sequence=last_sequence,
            snapshot_hash=snapshot_hash, operations=operations,
            previous=previous, head_type=OmsObservationHead,
            head_path=_oms_observation_head_path(run_id),
            tables=_OMS_OBSERVATION_TABLES,
            root_table="trading_strategy_one_oms_observation_snapshot_v2",
            token_factory=_oms_observation_token, label="OMS observation")

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
        barrier = ColdDispatchBarrier(self, run_id, closed)
        barrier._assert_gate(run_id)
        return barrier


@dataclass
class ColdDispatchBarrier:
    authority: TypedInsertDispatch
    run_id: str
    gate: _Gate
    prefix_verified: bool = False
    context_verified: bool = False

    def __post_init__(self) -> None:
        if (self.gate.mode != "closed" or self.gate.inflight
                or self.gate.registered
                or self.gate.active_batch_id != _ZERO_BATCH):
            raise KeeperUnavailable("Typed dispatch cold barrier is not quiescent")

    @property
    def epoch(self) -> int:
        return self.gate.epoch

    def _assert_gate(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise KeeperUnavailable("Typed dispatch barrier run differs")
        gate, _ = self.authority._read_gate(run_id)
        if gate != self.gate:
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
                                journal_profile: str, fixed_lot_resume=None) -> Any:
        """Scan the selected CH prefix once and bind its terminal commit to Keeper."""
        from src.trading_runtime.arte_journal_writer import (
            _COMMIT_COLUMNS, _literal, _rows, load_committed_prefix,
        )
        from src.trading_runtime.journal_contract import canonical_json

        if journal_profile not in {"v1", "backtest_v2", "backtest_v4", "live_v4"}:
            raise ValueError("Cold dispatch needs an explicit typed journal profile")
        self.prefix_verified = False
        if journal_profile in {"backtest_v4", "live_v4"}:
            from src.trading_runtime.arte_journal_commit_v4 import (
                load_verified_v4_prefix,
            )
            from src.trading_runtime.arte_journal_writer import _CONTRACTS

            self._assert_gate(self.run_id)
            for other_table in ("trading_commit_v1", "trading_commit_v2"):
                mixed = _rows(client,
                    f"SELECT batch_id FROM arte.{other_table} "
                    f"WHERE run_id={_literal(self.run_id)} LIMIT 1 FORMAT JSONEachRow")
                if mixed:
                    raise KeeperUnavailable("Cold dispatch cannot mix V4 and older commit fences")
            if fixed_lot_resume is not None:
                if journal_profile != 'backtest_v4':
                    raise ValueError('Selected resume cannot authorize another journal profile')
                from src.backend.backtest_fixed_structural_lot_resume import require_fixed_structural_lot_resume
                prefix = require_fixed_structural_lot_resume(fixed_lot_resume,self.run_id).prefix(client,self.run_id)
            else:
                prefix = load_verified_v4_prefix(client, self.run_id)
            gate, _ = self.authority._read_gate(self.run_id)
            if gate.compacted_through == 0:
                if prefix is not None:
                    raise KeeperUnavailable("ClickHouse V4 prefix lacks dispatch compaction")
            else:
                if (prefix is None or prefix.last_sequence != gate.compacted_through
                        or prefix.last_batch_id != gate.compacted_batch_id):
                    raise KeeperUnavailable("ClickHouse V4 prefix differs from dispatch watermark")
                columns = ",".join(name for name, _ in
                                   _CONTRACTS["trading_commit_v4"].columns)
                rows = _rows(client,
                    f"SELECT {columns} FROM arte.trading_commit_v4 "
                    f"WHERE run_id={_literal(self.run_id)} "
                    f"AND batch_id=toUUID({_literal(gate.compacted_batch_id)}) "
                    "FORMAT JSONEachRow")
                if (len(rows) != 1 or
                        sha256(canonical_json(rows[0]).encode()).hexdigest()
                        != gate.compacted_commit_hash):
                    raise KeeperUnavailable("ClickHouse V4 commit differs from dispatch hash")
            self._assert_gate(self.run_id)
            self.prefix_verified = True
            return prefix
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
        if gate != self.gate:
            raise KeeperUnavailable("Typed dispatch cold barrier was lost")
        txn = self.authority.keeper.transaction()
        txn.check(_gate_path(self.run_id), version=version)
        txn.set_data(_gate_path(self.run_id),
                     _Gate("open", 0, self.epoch, self.gate.registered,
                           self.gate.compacted_through, self.gate.compacted_batch_id,
                           self.gate.compacted_commit_hash,
                           self.gate.active_batch_id).wire(),
                     version=version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("Typed dispatch cold barrier release lost CAS race")
