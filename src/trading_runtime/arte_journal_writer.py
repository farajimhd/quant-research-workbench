"""Non-blocking submission of normalized ARTE journal rows.

This is a transport primitive, not the live/Backtest cutover. Producers must
first map every logical record to its typed family rows. The caller never waits
for ClickHouse in ``submit``; its future becomes durable only after the commit
row is verified. No local file is used.
"""
from __future__ import annotations

from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import math
import os
import re
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import perf_counter_ns
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import (
    POLICY_ALLOWED_TABLES, TABLES, V4_COMMIT_TABLES,
    VERSIONED_JOURNAL_V2_TABLES, fixed_backtest_v2_contracts,
    journal_permission_preflight, storage_preflight,
    versioned_journal_v2_contracts, versioned_journal_v2_preflight,
)
from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT
from src.backend.backtest_squeeze_episode_schema import (
    RESERVATION_REASON, SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE,
)
from src.backend.backtest_reconciliation_v3 import CHILD as RECONCILIATION_DIFFERENCE
from src.backend.backtest_portfolio_control_v3 import CONTROL as PORTFOLIO_CONTROL
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES,
    ENTRY_REPRICE_REJECTED,
    PROTECTED_EXIT_SATISFIED,
    PROTECTION_CHANGE_TABLES,
    PROTECTED_EXIT_SNAPSHOT,
    PORTFOLIO_ALLOCATION_FILL,
)
from src.trading_runtime.journal_contract import canonical_json

if TYPE_CHECKING:
    from src.trading_runtime.arte_portfolio_snapshot import (
        CapturedPortfolioSnapshot, PreparedPortfolioSnapshot,
    )


_CONTRACTS = {table.name: table for table in TABLES}
_CONTRACTS[ENTRY_EVIDENCE.name] = ENTRY_EVIDENCE
_CONTRACTS[ACKNOWLEDGEMENT.name] = ACKNOWLEDGEMENT
_CONTRACTS.update({table.name: table for table in V4_COMMIT_TABLES})
_CONTRACTS.update({table.name: table for table in VERSIONED_JOURNAL_V2_TABLES})
_CONTRACTS.update({table.name: table for table in (
    SQUEEZE_EPISODE, RESERVATION_REASON, RECONCILIATION_DIFFERENCE,
    PORTFOLIO_CONTROL,
    SQUEEZE_COMMIT_V3)})
_CONTRACTS.update({table.name: table for table in TRADE_PROPOSAL_TABLES})
_CONTRACTS.update({table.name: table for table in BROKER_OMS_TABLES})
_CONTRACTS.update({table.name: table for table in ENTRY_REPRICE_CAPACITY_TABLES})
_CONTRACTS[ENTRY_REPRICE_REJECTED.name] = ENTRY_REPRICE_REJECTED
_CONTRACTS[PROTECTED_EXIT_SATISFIED.name] = PROTECTED_EXIT_SATISFIED
_CONTRACTS.update({table.name: table for table in PROTECTION_CHANGE_TABLES})
_CONTRACTS[PROTECTED_EXIT_SNAPSHOT.name] = PROTECTED_EXIT_SNAPSHOT
_CONTRACTS[PORTFOLIO_ALLOCATION_FILL.name] = PORTFOLIO_ALLOCATION_FILL


def _without_text_prefix(value: str) -> str:
    """Ignore whitespace and UTF-8 BOMs when checking for opaque JSON text."""
    return re.sub(r"^[\s\ufeff]+", "", value)


_FAMILIES = (
    ("trading_event_v1", "events", "event_count", "event_hash"),
    ("trading_strategy_assignment_command_v1", "assignment_commands",
     "assignment_command_count", "assignment_command_hash"),
    ("trading_run_transition_v1", "run_transitions", "run_transition_count",
     "run_transition_hash"),
    ("trading_operational_fault_v1", "operational_faults", "operational_fault_count",
     "operational_fault_hash"),
    ("trading_account_risk_state_v1", "account_risk_states", "account_risk_state_count",
     "account_risk_state_hash"),
    ("trading_account_risk_reason_v1", "account_risk_reasons", "account_risk_reason_count",
     "account_risk_reason_hash"),
    ("trading_intent_decision_v1", "intent_decisions", "intent_decision_count",
     "intent_decision_hash"),
    ("trading_intent_decision_reason_v1", "intent_decision_reasons",
     "intent_decision_reason_count", "intent_decision_reason_hash"),
    ("trading_portfolio_decision_v1", "portfolio_decisions",
     "portfolio_decision_count", "portfolio_decision_hash"),
    ("trading_portfolio_decision_reason_v1", "portfolio_decision_reasons",
     "portfolio_decision_reason_count", "portfolio_decision_reason_hash"),
    ("trading_portfolio_reservation_event_v1", "portfolio_reservation_events",
     "portfolio_reservation_event_count", "portfolio_reservation_event_hash"),
    ("trading_portfolio_reconciliation_event_v1", "portfolio_reconciliation_events",
     "portfolio_reconciliation_event_count", "portfolio_reconciliation_event_hash"),
    ("trading_strategy_signal_v1", "signals", "signal_count", "signal_hash"),
    ("trading_strategy_signal_evidence_node_v1", "signal_evidence_nodes",
     "signal_evidence_node_count", "signal_evidence_node_hash"),
    ("trading_signal_source_v1", "signal_sources", "signal_source_count",
     "signal_source_hash"),
    ("trading_execution_v1", "executions", "execution_count", "execution_hash"),
    ("trading_commission_v1", "commissions", "commission_count", "commission_hash"),
    ("trading_order_command_v1", "order_commands", "order_command_count", "order_command_hash"),
    ("trading_order_command_context_v1", "order_contexts", "order_context_count",
     "order_context_hash"),
    ("trading_strategy_intent_use_v1", "intent_uses", "intent_use_count",
     "intent_use_hash"),
    ("trading_order_transition_v1", "order_transitions", "order_transition_count",
     "order_transition_hash"),
    ("trading_oms_group_state_v1", "oms_group_states", "oms_group_state_count",
     "oms_group_state_hash"),
    ("trading_oms_order_state_v1", "oms_order_states", "oms_order_state_count",
     "oms_order_state_hash"),
    ("trading_oms_broker_binding_v1", "oms_broker_bindings", "oms_broker_binding_count",
     "oms_broker_binding_hash"),
    ("trading_oms_warning_v1", "oms_warnings", "oms_warning_count", "oms_warning_hash"),
    ("trading_oms_cancel_oca_v1", "oms_cancel_ocas", "oms_cancel_oca_count",
     "oms_cancel_oca_hash"),
    ("trading_account_snapshot_v1", "account_snapshots", "account_snapshot_count",
     "account_snapshot_hash"),
    ("trading_position_snapshot_v1", "position_snapshots", "position_snapshot_count",
     "position_snapshot_hash"),
    ("trading_strategy_intent_v1", "intents", "intent_count", "intent_hash"),
    ("trading_intent_protection_slice_v1", "intent_slices", "intent_slice_count",
     "intent_slice_hash"),
    ("trading_backtest_cursor_v1", "backtest_cursors", "backtest_cursor_count",
     "backtest_cursor_hash"),
    ("trading_backtest_market_authority_v1", "backtest_market_authorities",
     "backtest_market_authority_count", "backtest_market_authority_hash"),
    ("trading_backtest_progress_v1", "backtest_progress",
     "backtest_progress_count", "backtest_progress_hash"),
    ("trading_prepared_v7_lease_v1", "prepared_v7_leases",
     "prepared_v7_lease_count", "prepared_v7_lease_hash"),
)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_EVENT_DETAILS = {
    # Legacy journal-only test marker; runtime lifecycle records use the
    # normalized (lifecycle, run) detail below.
    ("run_state", "lifecycle"): None,
    ("lifecycle", "run"): "trading_run_transition_v1",
    ("broker", "connection_state"): "trading_operational_fault_v1",
    ("risk", "risk_snapshot"): "trading_operational_fault_v1",
    ("risk", "continuous_risk_state"): "trading_account_risk_state_v1",
    ("strategy_decision", "signal"): "trading_strategy_signal_v1",
    ("strategy", "strategy_assignment_command"): "trading_strategy_assignment_command_v1",
    ("strategy", "strategy_intent"): "trading_strategy_intent_v1",
    ("checkpoint", "market_boundary"): "trading_backtest_cursor_v1",
    ("data_authority", "source_revision"): "trading_backtest_market_authority_v1",
    ("resource_lease", "prepared_v7_stream"): "trading_prepared_v7_lease_v1",
    ("strategy_decision", "intent_rejection"): "trading_intent_decision_v1",
    ("strategy_decision", "intent_deferral"): "trading_intent_decision_v1",
    ("portfolio_management", "portfolio_decision"): "trading_portfolio_decision_v1",
    ("portfolio_management", "portfolio_reservation"): "trading_portfolio_reservation_event_v1",
    ("portfolio_management", "portfolio_reconciliation"): "trading_portfolio_reconciliation_event_v1",
    ("execution", "fill"): "trading_execution_v1",
    ("execution", "commission"): "trading_commission_v1",
    ("order_management", "order_command"): "trading_order_command_v1",
    ("command", "order"): "trading_order_command_v1",
    ("order_management", "order_transition"): "trading_order_transition_v1",
    ("order_management", "order_group_state"): "trading_oms_group_state_v1",
    ("snapshot", "portfolio"): "trading_account_snapshot_v1",
    ("snapshot", "position"): "trading_position_snapshot_v1",
}
_COMMIT_COLUMNS = tuple(name for name, _ in _CONTRACTS["trading_commit_v1"].columns
                        if name not in ("run_month", "committed_at"))


class JournalQueueFull(RuntimeError):
    """The bounded persistence lane cannot accept another batch immediately."""


def journal_client_from_env() -> Any:
    """Open the dedicated typed-journal principal, never market credentials."""
    from research.mlops.clickhouse import ClickHouseHttpClient

    url = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "")
    if not url or not user or not password:
        raise ValueError("Typed journal requires dedicated ClickHouse URL, user, and password")
    market_users = {os.environ.get(key, "").strip() for key in (
        "BACKTEST_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_READ_USER",
        "REAL_LIVE_CLICKHOUSE_USER",
    )}
    if user in market_users:
        raise ValueError("Typed journal principal must differ from market-data readers")
    return ClickHouseHttpClient(
        url, user, password, timeout_seconds=60, persistent=True,
        default_query_params={"max_threads": 2, "max_execution_time": 60},
    )


def backtest_v4_journal_client_from_env(*, keeper_session=None) -> Any:
    """Open V4 with a caller-owned writable Keeper session and strict dispatch.

    The caller must keep that session alive until the writer has drained and
    closed. A credential alone is never a V4 publication authority.
    """
    from research.mlops.clickhouse import ClickHouseHttpClient
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_session import ManagedKeeperSession

    url = os.environ.get("BACKTEST_V4_RUNNER_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD", "")
    if not url or user != "backtest_v4_runner" or not password:
        raise ValueError("V4 Backtest requires its dedicated runner credential")
    if user in {os.environ.get(key, "").strip() for key in (
        "BACKTEST_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_READ_USER",
        "REAL_LIVE_CLICKHOUSE_USER", "TRADING_JOURNAL_CLICKHOUSE_USER",
    )}:
        raise ValueError("V4 Backtest runner must differ from market and live writers")
    if (not isinstance(keeper_session, ManagedKeeperSession)
            or not keeper_session.writable):
        raise RuntimeError("V4 Backtest runner needs a caller-owned writable Keeper session")
    client = ClickHouseHttpClient(
        url, user, password, timeout_seconds=60, persistent=True,
        default_query_params={"max_threads": 2, "max_execution_time": 60},
    )
    client.typed_insert_dispatch = TypedInsertDispatch(keeper_session.client)
    client.typed_insert_strict = True
    return client


def backtest_v4_context_client_from_env(*, keeper_session=None) -> Any:
    """Fence new run-context rows with Keeper using the journal-only principal.

    This control-plane client is separate from the V4 batch runner. It cannot
    write market products, and an unverified or read-only Keeper session never
    gains typed INSERT authority.
    """
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_session import ManagedKeeperSession

    if (not isinstance(keeper_session, ManagedKeeperSession)
            or not keeper_session.writable):
        raise RuntimeError("V4 run context needs a caller-owned writable Keeper session")
    client = journal_client_from_env()
    try:
        # The deployed journal principal owns the complete normalized V2
        # family, not only the older V1 subset checked by the default audit.
        from src.trading_runtime.arte_journal_schema import fixed_backtest_v2_preflight
        fixed_backtest_v2_preflight(client)
        client.typed_insert_dispatch = TypedInsertDispatch(keeper_session.client)
        client.typed_insert_strict = True
        return client
    except BaseException:
        client.close()
        raise


@dataclass(frozen=True, slots=True)
class TypedJournalBatch:
    run_id: str
    run_month: date
    attempt_id: str
    batch_id: str
    prior_batch_id: str
    first_sequence: int
    last_sequence: int
    source_cursor: str
    status: str
    events: tuple[Mapping[str, Any], ...]
    assignment_commands: tuple[Mapping[str, Any], ...] = ()
    run_transitions: tuple[Mapping[str, Any], ...] = ()
    operational_faults: tuple[Mapping[str, Any], ...] = ()
    account_risk_states: tuple[Mapping[str, Any], ...] = ()
    account_risk_reasons: tuple[Mapping[str, Any], ...] = ()
    intent_decisions: tuple[Mapping[str, Any], ...] = ()
    intent_decision_reasons: tuple[Mapping[str, Any], ...] = ()
    portfolio_decisions: tuple[Mapping[str, Any], ...] = ()
    portfolio_decision_reasons: tuple[Mapping[str, Any], ...] = ()
    portfolio_reservation_events: tuple[Mapping[str, Any], ...] = ()
    portfolio_reconciliation_events: tuple[Mapping[str, Any], ...] = ()
    signals: tuple[Mapping[str, Any], ...] = ()
    signal_evidence_nodes: tuple[Mapping[str, Any], ...] = ()
    signal_sources: tuple[Mapping[str, Any], ...] = ()
    executions: tuple[Mapping[str, Any], ...] = ()
    commissions: tuple[Mapping[str, Any], ...] = ()
    order_commands: tuple[Mapping[str, Any], ...] = ()
    order_contexts: tuple[Mapping[str, Any], ...] = ()
    intent_uses: tuple[Mapping[str, Any], ...] = ()
    order_transitions: tuple[Mapping[str, Any], ...] = ()
    oms_group_states: tuple[Mapping[str, Any], ...] = ()
    oms_order_states: tuple[Mapping[str, Any], ...] = ()
    oms_broker_bindings: tuple[Mapping[str, Any], ...] = ()
    oms_warnings: tuple[Mapping[str, Any], ...] = ()
    oms_cancel_ocas: tuple[Mapping[str, Any], ...] = ()
    account_snapshots: tuple[Mapping[str, Any], ...] = ()
    position_snapshots: tuple[Mapping[str, Any], ...] = ()
    intents: tuple[Mapping[str, Any], ...] = ()
    intent_slices: tuple[Mapping[str, Any], ...] = ()
    backtest_cursors: tuple[Mapping[str, Any], ...] = ()
    backtest_market_authorities: tuple[Mapping[str, Any], ...] = ()
    backtest_progress: tuple[Mapping[str, Any], ...] = ()
    prepared_v7_leases: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id or self.first_sequence < 1 or self.last_sequence < self.first_sequence:
            raise ValueError("Journal batch has invalid run or sequence identity")
        if (not isinstance(self.source_cursor, str) or not self.source_cursor
                or self.status not in {"running", "completed", "stopped", "failed"}):
            raise ValueError("Journal batch requires a source cursor and valid status")
        if _without_text_prefix(self.source_cursor).startswith(("{", "[")):
            raise ValueError("Journal source cursor requires normalized typed fields, not JSON")
        for value in (self.attempt_id, self.batch_id, self.prior_batch_id):
            UUID(value)
        # Copy only the small typed row envelopes at submission. Full schema
        # checks, JSON wire serialization, and hashing happen on the writer.
        for _, family, _, _ in _FAMILIES:
            snapshots = []
            for row in getattr(self, family):
                if any(isinstance(value, (Mapping, list, tuple, set, bytearray, memoryview))
                       for value in row.values()):
                    raise ValueError(f"{family} contains mutable or opaque journal data")
                snapshots.append(MappingProxyType(dict(row)))
            object.__setattr__(self, family, tuple(snapshots))

    def families(self) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
        return tuple((name, getattr(self, attribute)) for name, attribute, _, _ in _FAMILIES)


@dataclass(frozen=True, slots=True)
class V3SqueezeBatch:
    """One immutable closed-family supplement to an ordinary typed batch."""

    base: TypedJournalBatch
    episodes: tuple[Mapping[str, Any], ...]
    reservation_reasons: tuple[Mapping[str, Any], ...] = ()
    reconciliation_differences: tuple[Mapping[str, Any], ...] = ()
    portfolio_controls: tuple[Mapping[str, Any], ...] = ()
    policy_selections: tuple[Any, ...] = ()
    trade_proposal_rows: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    short_order_skips: tuple[Mapping[str, Any], ...] = ()
    broker_reply_policy_events: tuple[Mapping[str, Any], ...] = ()
    broker_reply_policy_messages: tuple[Mapping[str, Any], ...] = ()
    entry_reprice_deferred: tuple[Mapping[str, Any], ...] = ()
    entry_reprice_capacities: tuple[Mapping[str, Any], ...] = ()
    entry_reprice_capacity_reasons: tuple[Mapping[str, Any], ...] = ()
    entry_reprice_rejections: tuple[Mapping[str, Any], ...] = ()
    protected_exit_satisfied: tuple[Mapping[str, Any], ...] = ()
    protection_changes: tuple[Mapping[str, Any], ...] = ()
    protection_entry_orders: tuple[Mapping[str, Any], ...] = ()
    protected_exit_snapshots: tuple[Mapping[str, Any], ...] = ()
    portfolio_allocation_fills: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.base.status != "running":
            raise ValueError("V3 terminal Backtest requires the separate terminal fence")
        object.__setattr__(self, "episodes", tuple(
            MappingProxyType(dict(row)) for row in self.episodes))
        object.__setattr__(self, "reservation_reasons", tuple(
            MappingProxyType(dict(row)) for row in self.reservation_reasons))
        object.__setattr__(self, "reconciliation_differences", tuple(
            MappingProxyType(dict(row)) for row in self.reconciliation_differences))
        object.__setattr__(self, "portfolio_controls", tuple(
            MappingProxyType(dict(row)) for row in self.portfolio_controls))
        from src.backend.backtest_policy_selection_v3 import PolicySelection

        if any(type(selection) is not PolicySelection for selection in self.policy_selections):
            raise ValueError("V3 policy selections require typed catalog objects")
        object.__setattr__(self, "policy_selections", tuple(self.policy_selections))
        object.__setattr__(self, "trade_proposal_rows", tuple(
            (name, MappingProxyType(dict(row))) for name, row in self.trade_proposal_rows))
        for family in ("short_order_skips", "broker_reply_policy_events",
                       "broker_reply_policy_messages", "entry_reprice_deferred",
                       "entry_reprice_capacities", "entry_reprice_capacity_reasons",
                       "entry_reprice_rejections", "protected_exit_satisfied",
                       "protection_changes", "protection_entry_orders",
                       "protected_exit_snapshots", "portfolio_allocation_fills"):
            object.__setattr__(self, family, tuple(
                MappingProxyType(dict(row)) for row in getattr(self, family)))


@dataclass(frozen=True, slots=True)
class V4StrategyOneEntryBatch:
    """One typed intent batch and its nonredundant numbered evidence."""

    base: TypedJournalBatch
    entry_evidence: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.base, TypedJournalBatch)
                or self.base.status != "running"
                or not self.entry_evidence):
            raise ValueError("V4 Strategy 1 entry needs a running typed batch and evidence")
        object.__setattr__(self, "entry_evidence", tuple(
            MappingProxyType(dict(row)) for row in self.entry_evidence))


@dataclass(frozen=True, slots=True)
class V4BrokerAcknowledgementBatch:
    """One broker reply and its exact scalar detail on the V4 writer lane."""

    base: TypedJournalBatch
    acknowledgement: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (not isinstance(self.base, TypedJournalBatch)
                or self.base.status != "running"
                or len(self.base.events) != 1
                or not isinstance(self.acknowledgement, Mapping)):
            raise ValueError("V4 broker acknowledgement requires one running event")
        object.__setattr__(self, "acknowledgement",
                           MappingProxyType(dict(self.acknowledgement)))


@dataclass(frozen=True, slots=True)
class V4ProtectionChangeBatch:
    """One normalized protection revision and its numbered entry identities."""

    base: TypedJournalBatch
    change: Mapping[str, Any]
    entry_orders: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.base, TypedJournalBatch)
                or self.base.status != "running" or len(self.base.events) != 1
                or not isinstance(self.change, Mapping)):
            raise ValueError("V4 protection change requires one running event")
        object.__setattr__(self, "change", MappingProxyType(dict(self.change)))
        object.__setattr__(self, "entry_orders", tuple(
            MappingProxyType(dict(row)) for row in self.entry_orders))


def _sealed_families(
    batch: TypedJournalBatch, *, v3_episode_ids: tuple[str, ...] = (),
    v3_control_ids: tuple[str, ...] = (),
    v3_proposal_ids: tuple[str, ...] = (),
    v3_short_skip_ids: tuple[str, ...] = (),
    v3_reply_policy_ids: tuple[str, ...] = (),
    v3_reprice_deferred_ids: tuple[str, ...] = (),
    v3_reprice_capacity_ids: tuple[str, ...] = (),
    v3_reprice_rejected_ids: tuple[str, ...] = (),
    v3_protected_exit_satisfied_ids: tuple[str, ...] = (),
    v3_protection_change_ids: tuple[str, ...] = (),
    v3_protected_exit_snapshot_ids: tuple[str, ...] = (),
    v3_portfolio_allocation_fill_ids: tuple[str, ...] = (),
    v3_reconciliation: bool = False,
    v4_broker_ack_ids: tuple[str, ...] = (),
    v4_protection_ids: tuple[str, ...] = (),
) -> tuple[tuple[str, tuple[dict[str, Any], ...]], ...]:
    """Validate and hash the immutable snapshot on the persistence lane."""
    if len(batch.events) != batch.last_sequence - batch.first_sequence + 1:
        raise ValueError("Journal batch must cover a contiguous event sequence")
    sequences = [int(row["sequence"]) for row in batch.events]
    if sequences != list(range(batch.first_sequence, batch.last_sequence + 1)):
        raise ValueError("Journal event sequence is not contiguous")
    event_ids = {str(UUID(str(row["record_id"]))) for row in batch.events}
    if len(event_ids) != len(batch.events):
        raise ValueError("Journal batch repeated an event identity")
    events_by_id = {str(UUID(str(row["record_id"]))): row for row in batch.events}
    result: list[tuple[str, tuple[dict[str, Any], ...]]] = []
    for name, rows in batch.families():
        allowed = {column for column, _ in _CONTRACTS[name].columns}
        identities: set[str] = set()
        sealed: list[dict[str, Any]] = []
        for row in rows:
            if set(row) not in (allowed, allowed - {"content_hash"}):
                raise ValueError(f"{name} has missing or extra typed columns")
            if str(row["run_id"]) != batch.run_id or str(UUID(str(row["batch_id"]))) != batch.batch_id:
                raise ValueError(f"{name} mixed runs or batches")
            record_id = str(UUID(str(row["record_id"])))
            child_family = name in {
                "trading_signal_source_v1", "trading_intent_protection_slice_v1",
                "trading_order_command_context_v1", "trading_oms_order_state_v1",
                "trading_oms_broker_binding_v1", "trading_oms_warning_v1",
                "trading_oms_cancel_oca_v1", "trading_strategy_intent_use_v1",
                "trading_account_risk_reason_v1",
                "trading_intent_decision_reason_v1",
                "trading_portfolio_decision_reason_v1",
                "trading_backtest_progress_v1",
                "trading_strategy_signal_evidence_node_v1",
            }
            parent_id = (str(UUID(str(row["parent_record_id"])))
                         if child_family else record_id)
            if name != "trading_event_v1" and parent_id not in event_ids:
                raise ValueError(f"{name} has no parent journal event")
            if name != "trading_event_v1":
                parent = events_by_id[parent_id]
                if (str(row["event_month"]) != str(parent["event_month"])
                        or (not child_family
                            and str(row["account_id"]) != str(parent["account_id"]))):
                    raise ValueError(f"{name} differs from its parent event identity")
            if name == "trading_event_v1" and str(UUID(str(row["attempt_id"]))) != batch.attempt_id:
                raise ValueError("Journal event mixed attempts")
            if record_id in identities:
                raise ValueError(f"{name} has duplicate record identities")
            identities.add(record_id)
            content = {key: value for key, value in row.items() if key != "content_hash"}
            if any(isinstance(value, (Mapping, list, tuple, bytearray))
                   for value in content.values()):
                raise ValueError(f"{name} contains an opaque or mutable value")
            canonical = _canonical_typed_content(name, content)
            if (name == "trading_event_v1"
                    and canonical["event_month"] != canonical["event_time"][:7] + "-01"):
                raise ValueError("Journal event partition differs from its UTC event time")
            digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
            if "content_hash" in row and str(row["content_hash"]) != digest:
                raise ValueError(f"{name} has an incorrect content hash")
            sealed.append({**content, "content_hash": digest})
        result.append((name, tuple(sealed)))
    by_family = dict(result)
    account_snapshots = {
        (str(row["account_id"]), str(row["snapshot_id"]))
        for row in by_family["trading_account_snapshot_v1"]
    }
    if len(account_snapshots) != len(by_family["trading_account_snapshot_v1"]):
        raise ValueError("Journal batch repeated an account snapshot identity")
    for row in by_family["trading_position_snapshot_v1"]:
        if (str(row["account_id"]), str(row["snapshot_id"])) not in account_snapshots:
            raise ValueError("Position snapshot lacks its complete account snapshot")
    details_by_record: dict[str, str] = {}
    for name, rows in result:
        if name in {"trading_event_v1", "trading_signal_source_v1",
                    "trading_intent_protection_slice_v1",
                    "trading_order_command_context_v1", "trading_oms_order_state_v1",
                    "trading_oms_broker_binding_v1", "trading_oms_warning_v1",
                    "trading_oms_cancel_oca_v1", "trading_strategy_intent_use_v1",
                    "trading_account_risk_reason_v1",
                    "trading_intent_decision_reason_v1",
                    "trading_portfolio_decision_reason_v1",
                    "trading_backtest_progress_v1",
                    "trading_strategy_signal_evidence_node_v1"}:
            continue
        for row in rows:
            record_id = str(UUID(str(row["record_id"])))
            if record_id in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[record_id] = name
    expected_details = _EVENT_DETAILS
    if (v3_episode_ids or v3_control_ids or v3_proposal_ids
            or v3_short_skip_ids or v3_reply_policy_ids
            or v3_reprice_deferred_ids or v3_reprice_capacity_ids
            or v3_reprice_rejected_ids or v3_protected_exit_satisfied_ids
            or v3_protection_change_ids or v3_protected_exit_snapshot_ids
            or v3_portfolio_allocation_fill_ids):
        expected_details = {**_EVENT_DETAILS,
            ("market_discovery_signal", "signal_occurrence"):
                SQUEEZE_EPISODE.name,
            ("portfolio_management", "portfolio_control"):
                PORTFOLIO_CONTROL.name,
            ("trade_proposal", "trade_proposal_confirmed"):
                TRADE_PROPOSAL_TABLES[0].name,
            ("trade_proposal", "trade_proposal_result"):
                TRADE_PROPOSAL_TABLES[0].name,
            ("broker_policy", "short_order_skipped"):
                BROKER_OMS_TABLES[0].name,
            ("broker_policy", "order_reply_suppression"):
                BROKER_OMS_TABLES[1].name,
            ("broker_policy", "order_warning_decision"):
                BROKER_OMS_TABLES[1].name,
            ("order_management", "entry_reprice_deferred"):
                BROKER_OMS_TABLES[3].name,
            ("portfolio_management", "entry_reprice_capacity"):
                ENTRY_REPRICE_CAPACITY_TABLES[0].name,
            ("portfolio_management", "entry_reprice_rejected"):
                ENTRY_REPRICE_REJECTED.name,
            ("order_management", "protected_exit_already_satisfied"):
                PROTECTED_EXIT_SATISFIED.name,
            ("protection", "protection_change"):
                PROTECTION_CHANGE_TABLES[0].name,
            ("order_management", "protected_exit_snapshot_reconciled"):
                PROTECTED_EXIT_SNAPSHOT.name,
            ("portfolio_management", "portfolio_allocation"):
                PORTFOLIO_ALLOCATION_FILL.name}
        for record_id in v3_episode_ids:
            identity = str(UUID(str(record_id)))
            if identity in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[identity] = SQUEEZE_EPISODE.name
        for record_id in v3_control_ids:
            identity = str(UUID(str(record_id)))
            if identity in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[identity] = PORTFOLIO_CONTROL.name
        for record_id in v3_proposal_ids:
            identity = str(UUID(str(record_id)))
            if identity in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[identity] = TRADE_PROPOSAL_TABLES[0].name
        for record_ids, table in (
            (v3_short_skip_ids, BROKER_OMS_TABLES[0].name),
            (v3_reply_policy_ids, BROKER_OMS_TABLES[1].name),
            (v3_reprice_deferred_ids, BROKER_OMS_TABLES[3].name),
            (v3_reprice_capacity_ids, ENTRY_REPRICE_CAPACITY_TABLES[0].name),
            (v3_reprice_rejected_ids, ENTRY_REPRICE_REJECTED.name),
            (v3_protected_exit_satisfied_ids, PROTECTED_EXIT_SATISFIED.name),
            (v3_protection_change_ids, PROTECTION_CHANGE_TABLES[0].name),
            (v3_protected_exit_snapshot_ids, PROTECTED_EXIT_SNAPSHOT.name),
            (v3_portfolio_allocation_fill_ids, PORTFOLIO_ALLOCATION_FILL.name),
        ):
            for record_id in record_ids:
                identity = str(UUID(str(record_id)))
                if identity in details_by_record:
                    raise ValueError("Journal event has multiple typed detail families")
                details_by_record[identity] = table
    if v4_broker_ack_ids:
        expected_details = {**expected_details,
                            ("broker", "order_acknowledgement"): ACKNOWLEDGEMENT.name}
        for record_id in v4_broker_ack_ids:
            identity = str(UUID(str(record_id)))
            if identity in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[identity] = ACKNOWLEDGEMENT.name
    if v4_protection_ids:
        expected_details = {**expected_details,
                            ("protection", "protection_change"):
                                PROTECTION_CHANGE_TABLES[0].name}
        for record_id in v4_protection_ids:
            identity = str(UUID(str(record_id)))
            if identity in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[identity] = PROTECTION_CHANGE_TABLES[0].name
    for event in by_family["trading_event_v1"]:
        key = (str(event["category"]), str(event["entity_type"]))
        if key not in expected_details:
            raise ValueError(f"Journal event has no typed contract: {key}")
        record_id = str(UUID(str(event["record_id"])))
        if details_by_record.get(record_id) != expected_details[key]:
            raise ValueError("Journal event lacks its required typed detail")
    for authority in by_family["trading_backtest_market_authority_v1"]:
        parent = events_by_id[str(UUID(str(authority["record_id"]))) ]
        if (parent["category"] != "data_authority"
                or parent["entity_type"] != "source_revision"
                or parent["entity_id"] != "fixed_market_data"
                or parent["account_id"] or authority["account_id"]
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(authority["execution_plan_token"]))
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(authority["parent_market_plan_token"]))):
            raise ValueError("Fixed market authority differs from its journal event")
    for lease in by_family["trading_prepared_v7_lease_v1"]:
        parent = events_by_id[str(UUID(str(lease["record_id"]))) ]
        if (parent["category"] != "resource_lease"
                or parent["entity_type"] != "prepared_v7_stream"
                or parent["entity_id"] != lease["stream_id"]
                or parent["account_id"] or lease["account_id"]
                or not lease["stream_id"] or not 0 < int(lease["owner_pid"]) < 2**32
                or lease["phase"] not in {"acquiring", "acquired", "release_failed", "released"}
                or (lease["phase"] == "release_failed") != (lease["error_type"] is not None)
                or _datetime_wire(lease["source_event_time"], 9)
                != _datetime_wire(parent["event_time"], 9)):
            raise ValueError("Prepared V7 lease differs from its journal event")
    for reconciliation in by_family["trading_portfolio_reconciliation_event_v1"]:
        parent = events_by_id[str(UUID(str(reconciliation["record_id"]))) ]
        event_time = _datetime_wire(parent["event_time"], 9)
        source_time = _datetime_wire(reconciliation["source_event_time"], 9)
        if (parent["category"] != "portfolio_management"
                or parent["entity_type"] != "portfolio_reconciliation"
                or parent["entity_id"] != reconciliation["account_key"]
                or parent["account_id"] != reconciliation["account_id"]
                or not reconciliation["snapshot_id"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(reconciliation["difference_hash"]))
                or (source_time > event_time if v3_reconciliation
                    else source_time != event_time)):
            raise ValueError("Portfolio reconciliation differs from its journal event")
    progress_parents: set[str] = set()
    for progress in by_family["trading_backtest_progress_v1"]:
        parent_id = str(UUID(str(progress["parent_record_id"])))
        parent = events_by_id[parent_id]
        if (details_by_record.get(parent_id) != "trading_backtest_cursor_v1"
                or parent_id in progress_parents
                or _datetime_wire(progress["controller_time"], 9)
                != _datetime_wire(parent["event_time"], 9)
                or (progress["runtime_last_event_time"] is not None
                    and _datetime_wire(progress["runtime_last_event_time"], 9)
                    > _datetime_wire(progress["controller_time"], 9))):
            raise ValueError("Backtest progress differs from its boundary event")
        progress_parents.add(parent_id)
    for transition in by_family["trading_run_transition_v1"]:
        parent = events_by_id[str(UUID(str(transition["record_id"])))]
        if (parent["category"] != "lifecycle" or parent["entity_type"] != "run"
                or parent["entity_id"] != batch.run_id
                or parent["account_id"]
                or transition["account_id"]
                or transition["status"] not in {"running", "completed", "stopped", "failed"}
                or _datetime_wire(transition["source_event_time"], 9)
                != _datetime_wire(parent["event_time"], 9)
                or (transition["status"] == "running") != (transition["processed_events"] is None)):
            raise ValueError("Run transition differs from its lifecycle event")
    for fault in by_family["trading_operational_fault_v1"]:
        parent = events_by_id[str(UUID(str(fault["record_id"])))]
        expected_status = {
            ("broker", "connection_state"): "disconnected",
            ("risk", "risk_snapshot"): "stale",
        }.get((parent["category"], parent["entity_type"]))
        if (expected_status is None or parent["entity_id"] != batch.run_id
                or parent["account_id"] or fault["account_id"]
                or fault["status"] != expected_status
                or fault["entries_frozen"] != 1
                or _datetime_wire(fault["source_event_time"], 9)
                != _datetime_wire(parent["event_time"], 9)):
            raise ValueError("Operational fault differs from its broker or risk event")
    risk_states = {
        str(UUID(str(row["record_id"]))): row
        for row in by_family["trading_account_risk_state_v1"]
    }
    reasons_by_parent: dict[str, list[dict[str, Any]]] = {}
    for reason in by_family["trading_account_risk_reason_v1"]:
        parent_id = str(UUID(str(reason["parent_record_id"])))
        if parent_id not in risk_states or not str(reason["reason"]):
            raise ValueError("Account risk reason lacks its typed state")
        reasons_by_parent.setdefault(parent_id, []).append(reason)
    for risk_id, state in risk_states.items():
        parent = events_by_id[risk_id]
        reasons = reasons_by_parent.get(risk_id, [])
        if (parent["category"] != "risk" or parent["entity_type"] != "continuous_risk_state"
                or parent["entity_id"] != state["account_id"]
                or not state["account_id"] or not state["account_key"]
                or state["state"] not in {"normal", "entries_paused", "reduce_only",
                                       "emergency_exit", "reconciling", "fully_blocked"}
                or state["enforced"] not in {0, 1}
                or (state["enforced"] == 0 and state["state"] != "normal")
                or _datetime_wire(state["source_event_time"], 9)
                != _datetime_wire(parent["event_time"], 9)
                or len(reasons) != int(state["reason_count"])
                or sorted(int(row["ordinal"]) for row in reasons) != list(range(len(reasons)))
                or len({str(row["reason"]) for row in reasons}) != len(reasons)):
            raise ValueError("Account risk state or reasons differ from its event")
    decisions = {
        str(UUID(str(row["record_id"]))): row
        for row in by_family["trading_intent_decision_v1"]
    }
    decision_reasons: dict[str, list[dict[str, Any]]] = {}
    for reason in by_family["trading_intent_decision_reason_v1"]:
        parent_id = str(UUID(str(reason["parent_record_id"])))
        if (parent_id not in decisions or not str(reason["reason"])
                or str(reason["account_id"]) != str(decisions[parent_id]["account_id"])):
            raise ValueError("Intent decision reason lacks its typed decision")
        decision_reasons.setdefault(parent_id, []).append(reason)
    for decision_id, decision in decisions.items():
        parent = events_by_id[decision_id]
        reasons = decision_reasons.get(decision_id, [])
        if (parent["category"] != "strategy_decision"
                or parent["entity_type"] != decision["decision_kind"]
                or decision["decision_kind"] not in {"intent_rejection", "intent_deferral"}
                or str(parent["account_id"]) != str(decision["account_id"])
                or not str(decision["account_id"]) or not str(decision["intent_id"])
                or not str(decision["ticker"]) or not str(decision["reason_code"])
                or decision["action"] != "wait"
                or _datetime_wire(decision["source_event_time"], 9)
                != _datetime_wire(parent["event_time"], 9)
                or len(reasons) != int(decision["reason_count"])
                or sorted(int(row["ordinal"]) for row in reasons)
                != list(range(len(reasons)))
                or len({str(row["reason"]) for row in reasons}) != len(reasons)):
            raise ValueError("Intent decision or reasons differ from its event")
    portfolio_decisions = {
        str(UUID(str(row["record_id"]))): row
        for row in by_family["trading_portfolio_decision_v1"]
    }
    portfolio_reasons: dict[str, list[dict[str, Any]]] = {}
    for reason in by_family["trading_portfolio_decision_reason_v1"]:
        parent_id = str(UUID(str(reason["parent_record_id"])))
        if (parent_id not in portfolio_decisions
                or reason["account_id"] != portfolio_decisions[parent_id]["account_id"]
                or not reason["reason"]):
            raise ValueError("Portfolio decision reason lacks its typed decision")
        portfolio_reasons.setdefault(parent_id, []).append(reason)
    for record_id, decision in portfolio_decisions.items():
        parent = events_by_id[record_id]
        reasons = portfolio_reasons.get(record_id, [])
        if (parent["category"] != "portfolio_management"
                or parent["entity_type"] != "portfolio_decision"
                or parent["entity_id"] != decision["decision_id"]
                or parent["account_id"] != decision["account_id"]
                or _datetime_wire(parent["event_time"], 6)
                < _datetime_wire(decision["decided_at"], 6)
                or len(reasons) != int(decision["reason_count"])
                or sorted(int(row["ordinal"]) for row in reasons)
                != list(range(len(reasons)))):
            raise ValueError("Portfolio decision differs from its event or reasons")
    for reservation in by_family["trading_portfolio_reservation_event_v1"]:
        parent = events_by_id[str(UUID(str(reservation["record_id"])))]
        if (parent["category"] != "portfolio_management"
                or parent["entity_type"] != "portfolio_reservation"
                or parent["entity_id"] != reservation["reservation_id"]
                or parent["account_id"] != reservation["account_id"]):
            raise ValueError("Portfolio reservation differs from its event")
    sources_by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in by_family["trading_signal_source_v1"]:
        parent_id = str(UUID(str(row["parent_record_id"])))
        if details_by_record.get(parent_id) != "trading_strategy_signal_v1":
            raise ValueError("Signal source lacks a typed strategy signal")
        if not str(row["source_signal_id"]):
            raise ValueError("Signal source identity is empty")
        sources_by_parent.setdefault(parent_id, []).append(row)
    for signal in by_family["trading_strategy_signal_v1"]:
        signal_id = str(UUID(str(signal["record_id"])))
        source_rows = sources_by_parent.get(signal_id, [])
        if (len(source_rows) != int(signal["source_signal_count"])
                or sorted(int(row["source_ordinal"]) for row in source_rows)
                != list(range(len(source_rows)))):
            raise ValueError("Signal sources do not match the typed signal count")
    evidence_by_parent: dict[str, list[dict[str, Any]]] = {}
    for node in by_family["trading_strategy_signal_evidence_node_v1"]:
        parent_id = str(UUID(str(node["parent_record_id"])))
        if details_by_record.get(parent_id) != "trading_strategy_signal_v1":
            raise ValueError("Signal evidence node lacks its typed signal")
        evidence_by_parent.setdefault(parent_id, []).append(node)
    from src.trading_runtime.arte_journal_projection import recover_signal_evidence_nodes
    for signal in by_family["trading_strategy_signal_v1"]:
        signal_id = str(UUID(str(signal["record_id"])))
        if len(evidence_by_parent.get(signal_id, [])) != int(signal["evidence_node_count"]):
            raise ValueError("Signal evidence nodes do not match the typed signal count")
        if evidence_by_parent.get(signal_id):
            recover_signal_evidence_nodes(evidence_by_parent[signal_id],
                                          parent_record_id=signal_id)
    slices_by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in by_family["trading_intent_protection_slice_v1"]:
        parent_id = str(UUID(str(row["parent_record_id"])))
        if details_by_record.get(parent_id) != "trading_strategy_intent_v1":
            raise ValueError("Protection slice parent lacks a typed strategy intent")
        parent = events_by_id[parent_id]
        if str(row["account_id"]) != str(parent["account_id"]):
            raise ValueError("Protection slice account differs from its intent")
        slices_by_parent.setdefault(parent_id, []).append(row)
    for intent in by_family["trading_strategy_intent_v1"]:
        intent_id = str(UUID(str(intent["record_id"])))
        child_rows = slices_by_parent.get(intent_id, [])
        if (len(child_rows) != int(intent["protection_slice_count"])
                or sorted(int(row["ordinal"]) for row in child_rows)
                != list(range(len(child_rows)))
                or len({str(row["slice_id"]) for row in child_rows}) != len(child_rows)):
            raise ValueError("Protection slices do not match the typed intent count")
    context_parents: set[str] = set()
    for row in by_family["trading_order_command_context_v1"]:
        parent_id = str(UUID(str(row["parent_record_id"])))
        parent = events_by_id[parent_id]
        if (details_by_record.get(parent_id) != "trading_order_command_v1"
                or parent_id in context_parents
                or str(row["account_id"]) != str(parent["account_id"])
                or not str(row["strategy_intent_id"])
                or not str(row["order_group_id"])
                or not str(row["policy_version"])):
            raise ValueError("Order command context lacks a unique typed command parent")
        context_parents.add(parent_id)
    intent_use_parents: set[str] = set()
    for row in by_family["trading_strategy_intent_use_v1"]:
        parent_id = str(UUID(str(row["parent_record_id"])))
        parent = events_by_id[parent_id]
        if (details_by_record.get(parent_id) not in {
                "trading_order_command_v1", "trading_oms_group_state_v1"}
                or parent_id in intent_use_parents
                or str(row["account_id"]) != str(parent["account_id"])):
            raise ValueError("Intent revision use lacks a unique strategy consumer")
        intent_use_parents.add(parent_id)
    oms_orders: dict[str, list[dict[str, Any]]] = {}
    oms_bindings: dict[str, list[dict[str, Any]]] = {}
    oms_warnings: dict[str, list[dict[str, Any]]] = {}
    oms_cancel_ocas: dict[str, list[dict[str, Any]]] = {}
    for name, target in (("trading_oms_order_state_v1", oms_orders),
                         ("trading_oms_broker_binding_v1", oms_bindings),
                         ("trading_oms_warning_v1", oms_warnings),
                         ("trading_oms_cancel_oca_v1", oms_cancel_ocas)):
        for row in by_family[name]:
            parent_id = str(UUID(str(row["parent_record_id"])))
            parent = events_by_id[parent_id]
            if (details_by_record.get(parent_id) != "trading_oms_group_state_v1"
                    or str(row["account_id"]) != str(parent["account_id"])):
                raise ValueError("OMS component lacks its typed group-state parent")
            target.setdefault(parent_id, []).append(row)
    for group in by_family["trading_oms_group_state_v1"]:
        parent_id = str(UUID(str(group["record_id"])))
        event = events_by_id[parent_id]
        orders = oms_orders.get(parent_id, [])
        bindings = oms_bindings.get(parent_id, [])
        warnings = oms_warnings.get(parent_id, [])
        cancel_ocas = oms_cancel_ocas.get(parent_id, [])
        if (str(group["group_id"]) != str(event["entity_id"])
                or len(orders) != int(group["order_count"])
                or len(bindings) != int(group["broker_binding_count"])
                or len(warnings) != int(group["warning_count"])
                or len(cancel_ocas) != int(group["cancel_oca_count"])
                or sorted(int(row["ordinal"]) for row in orders) != list(range(len(orders)))
                or sorted(int(row["ordinal"]) for row in bindings) != list(range(len(bindings)))
                or sorted(int(row["ordinal"]) for row in warnings) != list(range(len(warnings)))
                or sorted(int(row["ordinal"]) for row in cancel_ocas) != list(range(len(cancel_ocas)))
                or len({str(row["broker_order_id"]) for row in bindings}) != len(bindings)):
            raise ValueError("OMS components differ from their group-state counts or identity")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CommittedPrefix:
    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class V2CommittedPrefix:
    """Verified V2 chain; intentionally not a V1 page-reader credential."""
    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


def _can_coalesce(left: TypedJournalBatch, right: TypedJournalBatch,
                  max_events: int) -> bool:
    return (
        left.status == "running"
        and left.run_id == right.run_id
        and left.run_month == right.run_month
        and left.attempt_id == right.attempt_id
        and right.prior_batch_id == left.batch_id
        and right.first_sequence == left.last_sequence + 1
        and right.last_sequence - left.first_sequence + 1 <= max_events
    )


def _coalesce_unpublished(batches: tuple[TypedJournalBatch, ...]) -> TypedJournalBatch:
    """Rekey contiguous unpublished microbatches under the final batch ID."""
    if not batches:
        raise ValueError("Cannot coalesce an empty journal batch")
    for prior, current in zip(batches, batches[1:]):
        if not _can_coalesce(prior, current, 2**64 - 1):
            raise ValueError("Journal microbatches are not contiguous")
    if len(batches) == 1:
        return batches[0]
    last = batches[-1]
    families: dict[str, tuple[dict[str, Any], ...]] = {}
    for _, attribute, _, _ in _FAMILIES:
        families[attribute] = tuple(
            {**{key: value for key, value in row.items() if key != "content_hash"},
             "batch_id": last.batch_id}
            for batch in batches for row in getattr(batch, attribute)
        )
    rekeyed_intents = {
        str(UUID(str(row["record_id"]))): typed_row("trading_strategy_intent_v1", row)["content_hash"]
        for row in families["intents"]
    }
    families["intent_uses"] = tuple(
        {**row, "intent_content_hash": rekeyed_intents.get(
            str(UUID(str(row["intent_record_id"]))), row["intent_content_hash"])}
        for row in families["intent_uses"]
    )
    return TypedJournalBatch(
        batches[0].run_id, batches[0].run_month, batches[0].attempt_id,
        last.batch_id, batches[0].prior_batch_id,
        batches[0].first_sequence, last.last_sequence,
        last.source_cursor, last.status,
        **families,
    )


def typed_row(name: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Hash the persisted typed representation, not source-side spellings."""
    row = dict(values)
    if "content_hash" in row:
        raise ValueError("Caller cannot provide a content hash")
    row["content_hash"] = sha256(
        canonical_json(_canonical_typed_content(name, row)).encode("utf-8")
    ).hexdigest()
    return row


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_ISO_INSTANT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def _datetime_wire(value: Any, scale: int, *, stored_utc: bool = False) -> str:
    """Render a timezone-aware instant in ClickHouse's lossless UTC format."""
    source = value.isoformat() if isinstance(value, datetime) else str(value)
    if stored_utc:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}(?:\d{3})?", source):
            raise ValueError("Stored journal timestamp is not a UTC DateTime64 value")
        source += "+00:00"
    match = _ISO_INSTANT.fullmatch(source)
    if match is None:
        raise ValueError("Journal timestamps must be timezone-aware ISO instants")
    fraction = (match.group(3) or "").ljust(9, "0")
    if scale == 6 and fraction[6:] != "000":
        raise ValueError("Submicrosecond timestamp cannot fit DateTime64(6)")
    zone = "+00:00" if match.group(4) == "Z" else match.group(4)
    parsed = datetime.fromisoformat(
        f"{match.group(1)}T{match.group(2)}.{fraction[:6]}{zone}"
    ).astimezone(timezone.utc)
    result = parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
    return result + fraction[6:] if scale == 9 else result


def _canonical_typed_content(
    name: str, row: Mapping[str, Any], *, stored_utc: bool = False,
) -> dict[str, Any]:
    """Canonicalize every persisted field for reproducible row-hash recovery."""
    if name not in _CONTRACTS:
        raise ValueError("Unknown typed journal family")
    expected = {column for column, _ in _CONTRACTS[name].columns} - {"content_hash"}
    if set(row) != expected:
        raise ValueError(f"{name} has missing or extra typed columns")
    canonical: dict[str, Any] = {}
    for column, kind in _CONTRACTS[name].columns:
        if column == "content_hash":
            continue
        value = row[column]
        nullable = kind.startswith("Nullable(")
        if value is None:
            if not nullable:
                raise ValueError(f"{name}.{column} cannot be null")
            canonical[column] = None
            continue
        base = kind[9:-1] if nullable else kind
        if base.startswith("DateTime64(9"):
            canonical[column] = _datetime_wire(value, 9, stored_utc=stored_utc)
        elif base.startswith("DateTime64(6"):
            canonical[column] = _datetime_wire(value, 6, stored_utc=stored_utc)
        elif base.startswith("Decimal("):
            decimal_type = re.fullmatch(r"Decimal\((\d+),\s*(\d+)\)", base)
            if decimal_type is None:
                raise ValueError(f"Unsupported typed decimal {name}.{column}: {base}")
            precision, scale = map(int, decimal_type.groups())
            try:
                with localcontext() as context:
                    context.prec = 50
                    number = Decimal(str(value))
                    quantized = number.quantize(Decimal(1).scaleb(-scale))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{name}.{column} is not a valid decimal") from exc
            if not number.is_finite() or number != quantized:
                raise ValueError(f"{name}.{column} loses decimal precision")
            if quantized.copy_abs() >= Decimal(10) ** (precision - scale):
                raise ValueError(f"{name}.{column} exceeds decimal width")
            canonical[column] = format(quantized, f".{scale}f")
        elif base.startswith("UInt"):
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError(f"{name}.{column} is not an unsigned integer")
            number = int(value)
            if number >= 1 << int(base[4:]):
                raise ValueError(f"{name}.{column} exceeds its unsigned width")
            canonical[column] = number
        elif base == "Int64":
            if type(value) is not int or not -(2**63) <= value < 2**63:
                raise ValueError(f"{name}.{column} is not an Int64")
            canonical[column] = value
        elif base == "Int32":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError(f"{name}.{column} is not an Int32")
            canonical[column] = value
        elif base == "Bool":
            if type(value) is bool:
                canonical[column] = value
            elif stored_utc and type(value) is int and value in (0, 1):
                canonical[column] = bool(value)
            else:
                raise ValueError(f"{name}.{column} is not a Bool")
        elif base == "Float64":
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name}.{column} is not a finite Float64")
            canonical[column] = float(value)
        elif base == "UUID":
            canonical[column] = str(UUID(str(value)))
        elif base == "Date":
            canonical[column] = date.fromisoformat(str(value)).isoformat()
        elif base in {"String", "LowCardinality(String)", "FixedString(64)"}:
            if not isinstance(value, str):
                raise ValueError(f"{name}.{column} is not a string")
            # A String column is not an escape hatch for an unmodelled JSON
            # object or array. JSONEachRow below is only the wire format.
            candidate = _without_text_prefix(value)
            if candidate.startswith(("{", "[")):
                try:
                    decoded = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass
                else:
                    if isinstance(decoded, (dict, list)):
                        raise ValueError(
                            f"{name}.{column} requires normalized typed rows, not JSON text"
                        )
            canonical[column] = value
        else:
            raise ValueError(f"Unsupported typed journal field {name}.{column}: {kind}")
    return canonical


def _wire_row(name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    result = _canonical_typed_content(name, content)
    if "content_hash" in row:
        result["content_hash"] = str(row["content_hash"])
    return result


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def _identity(rows: tuple[Mapping[str, Any], ...] | list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((str(UUID(str(row["record_id"]))), str(row["content_hash"])) for row in rows)


def _family_identities(
    client: Any, batch_id: str, *, journal_profile: str = "v1",
) -> dict[str, list[tuple[str, str]]]:
    """Read every typed family in one network request, including empty ones."""
    token = f"batch_id=toUUID({_literal(batch_id)})"
    selects = [
        "(SELECT groupArray((toString(record_id),toString(content_hash))) "
        f"FROM arte.{_profile_table(name, journal_profile)} WHERE {token}) AS {name}"
        for name, _, _, _ in _FAMILIES
    ]
    response = _rows(client, "SELECT " + ",".join(selects) + " FORMAT JSONEachRow")
    names = {name for name, _, _, _ in _FAMILIES}
    if len(response) != 1 or set(response[0]) != names:
        raise RuntimeError("Typed journal family readback is incomplete")
    return {
        name: sorted((str(UUID(str(record_id))), str(digest))
                     for record_id, digest in response[0][name])
        for name in names
    }


def _profile_table(name: str, journal_profile: str) -> str:
    if journal_profile == "v1":
        return name
    if journal_profile == "backtest_v2":
        return {"trading_strategy_signal_v1": "trading_strategy_signal_v2",
                "trading_commit_v1": "trading_commit_v2"}.get(name, name)
    if journal_profile == "backtest_v3":
        return {"trading_strategy_signal_v1": "trading_strategy_signal_v2",
                "trading_commit_v1": "trading_commit_v3"}.get(name, name)
    if journal_profile == "backtest_v4":
        return {"trading_strategy_signal_v1": "trading_strategy_signal_v2",
                "trading_commit_v1": "trading_commit_v4"}.get(name, name)
    raise ValueError("Unknown typed journal profile")


_ZERO_DISPATCH_BATCH = "00000000-0000-0000-0000-000000000000"


def _insert(
    client: Any, name: str, rows: tuple[Mapping[str, Any], ...], token: str,
    *, journal_profile: str = "v1", dispatch_sequence: int | None = None,
    dispatch_batch_id: str | None = None, dispatch_run_context: bool = False,
    dispatch_terminal_account_id: str | None = None,
    dispatch_snapshot_account_id: str | None = None,
    dispatch_policy_hash: str | None = None,
    dispatch_sync_account_id: str | None = None,
    dispatch_sync_revision: int | None = None,
) -> str | None:
    contract_name = (_profile_table(name, journal_profile)
                     if journal_profile == "backtest_v3" and name == "trading_commit_v1"
                     else name)
    if contract_name not in _CONTRACTS:
        raise ValueError("Journal writer cannot insert outside typed journal tables")
    if not rows:
        return None
    columns = tuple(column for column, _ in _CONTRACTS[contract_name].columns)
    body = "\n".join(canonical_json(_wire_row(contract_name, row)) for row in rows)
    sql = (
        f"INSERT INTO arte.{_profile_table(name, journal_profile)} ({','.join(columns)}) "
        f"SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
        f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n{body}"
    )
    dispatch = getattr(client, "typed_insert_dispatch", None)
    sync_dispatch = getattr(client, "typed_sync_insert_dispatch", None)
    if dispatch_sync_account_id is not None:
        if (sync_dispatch is None or len(rows) != 1
                or name not in {"trading_portfolio_sync_snapshot_marker_v1",
                                "trading_portfolio_sync_fence_v1"}
                or journal_profile != "v1" or
                rows[0].get("account_id") != dispatch_sync_account_id or
                rows[0].get("state_revision") != dispatch_sync_revision or
                any(value is not None for value in (
                    dispatch_sequence, dispatch_batch_id, dispatch_terminal_account_id,
                    dispatch_snapshot_account_id, dispatch_policy_hash)) or
                dispatch_run_context):
            raise RuntimeError("Portfolio sync INSERT lacks strict dispatch identity")
        sync_dispatch.execute(client, run_id=rows[0]["run_id"],
            account_id=dispatch_sync_account_id, revision=dispatch_sync_revision,
            table=name, token=token, sql=sql,
            row_hash=_wire_row(contract_name, rows[0])["content_hash"])
        return sql
    if name in {"trading_portfolio_sync_snapshot_marker_v1",
                "trading_portfolio_sync_fence_v1"} and getattr(client, "typed_insert_strict", False):
        raise RuntimeError("Strict portfolio sync INSERT lacks dispatch identity")
    if getattr(client, "typed_insert_strict", False) and dispatch is None:
        raise RuntimeError("Strict typed journal INSERT lacks durable dispatch authority")
    if dispatch_policy_hash is not None and dispatch is None:
        raise RuntimeError("Policy INSERT lacks durable dispatch authority")
    if dispatch is not None:
        if dispatch_policy_hash is not None:
            policy_tables = {"trading_portfolio_policy_v1",
                             "trading_portfolio_policy_commit_v2"}
            policy_tables.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
            if (name not in policy_tables or journal_profile != "v1"
                    or not re.fullmatch(r"[0-9a-f]{64}", dispatch_policy_hash)
                    or any(row.get("policy_hash") != dispatch_policy_hash for row in rows)
                    or any(value is not None for value in (
                        dispatch_sequence, dispatch_batch_id,
                        dispatch_terminal_account_id, dispatch_snapshot_account_id))
                    or dispatch_run_context):
                raise ValueError("Policy dispatch identity differs from typed rows")
            dispatch.execute_policy_insert(
                client, policy_hash=dispatch_policy_hash,
                table=name, token=token, sql=sql)
            return sql
        if dispatch_terminal_account_id is not None and (
                name != "trading_backtest_snapshot_anchor_v1"
                or any(row.get("account_id") != dispatch_terminal_account_id for row in rows)):
            raise ValueError("Terminal dispatch account differs from typed anchor rows")
        if dispatch_snapshot_account_id is not None and any(
                row.get("account_id") != dispatch_snapshot_account_id
                or row.get("state_revision") != dispatch_sequence for row in rows):
            raise ValueError("Snapshot dispatch identity differs from typed rows")
        run_ids = {row.get("run_id") for row in rows}
        if len(run_ids) != 1 or not isinstance(next(iter(run_ids)), str) or not next(iter(run_ids)):
            raise RuntimeError("Durable typed INSERT lacks one run identity")
        dispatch.execute_typed_insert(
            client, run_id=next(iter(run_ids)),
            table=_profile_table(name, journal_profile), token=token, sql=sql,
            batch_id=(_ZERO_DISPATCH_BATCH if dispatch_run_context or dispatch_snapshot_account_id is not None else dispatch_batch_id),
            batch_last_sequence=(0 if dispatch_run_context else dispatch_sequence),
            terminal_account_id=dispatch_terminal_account_id,
            snapshot_account_id=dispatch_snapshot_account_id)
    else:
        client.execute(sql)
    return sql


def publish_typed_run(client: Any, run: Mapping[str, Any]) -> str:
    """Publish the parent identity; runtime context still needs its own fence."""
    expected_columns = {column for column, _ in _CONTRACTS["trading_run_v1"].columns}
    if set(run) != expected_columns:
        raise ValueError("Typed run has missing or extra columns")
    run_id = str(run["run_id"])
    interval = run["evaluation_interval_ms"]
    if (not run_id or run["mode"] not in {"live", "paper", "replay", "backtest", "integration_test"}
            or (interval is not None and (not isinstance(interval, int)
                                          or interval < 100 or interval % 100))):
        raise ValueError("Typed run identity or evaluation interval is invalid")
    for field in ("configuration_hash", "code_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(run[field])):
            raise ValueError(f"Typed run {field} must be a SHA-256 digest")
    wire = _wire_row("trading_run_v1", run)
    if wire["run_month"] != wire["started_at"][:7] + "-01":
        raise ValueError("Typed run partition differs from its UTC start time")
    columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    query = (f"SELECT {columns} FROM arte.trading_run_v1 "
             f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    existing = _rows(client, query)
    if existing and (len(existing) != 1 or existing[0] != wire):
        raise RuntimeError("Typed run identity conflicts with existing publication")
    if not existing:
        _insert(client, "trading_run_v1", (run,), f"run:{run_id}",
                dispatch_run_context=True)
        if _rows(client, query) != [wire]:
            raise RuntimeError("Typed run identity was not durably published")
    return run_id


_RUN_CONFIG_FIELDS = frozenset({
    "strategy_id", "strategy_revision", "anchor_date", "run_plan_id",
    "safety_supervisor_enabled", "checkpoint_interval_events",
    "write_progress_checkpoints",
})


def publish_typed_run_context(
    client: Any, *, run_id: str, config: Mapping[str, Any],
    account_ids: tuple[str, ...],
) -> None:
    """Publish the normalized RunConfig and account membership, fence last."""
    if set(config) != _RUN_CONFIG_FIELDS:
        raise ValueError("Runtime configuration has missing or extra typed fields")
    if (not str(config["strategy_id"]).strip()
            or int(config["strategy_revision"]) < 0
            or int(config["checkpoint_interval_events"]) < 1
            or any(config[key] not in (0, 1, False, True) for key in (
                "safety_supervisor_enabled", "write_progress_checkpoints"))):
        raise ValueError("Runtime configuration contains invalid values")
    if (not account_ids or len(account_ids) > 65535
            or any(not account.strip() for account in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Runtime account membership is invalid")
    parent_columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    parent = _rows(client,
        f"SELECT {parent_columns} FROM arte.trading_run_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(parent) != 1:
        raise RuntimeError("Typed run parent is missing or duplicated")
    month = str(parent[0]["run_month"])
    run_hash = sha256(canonical_json(_canonical_typed_content(
        "trading_run_v1", parent[0], stored_utc=True)).encode("utf-8")).hexdigest()
    context = typed_row("trading_runtime_config_v1", {
        "run_id": run_id, "run_month": month,
        **{key: (int(value) if key in {"safety_supervisor_enabled",
                                        "write_progress_checkpoints"} else value)
           for key, value in config.items()},
    })
    members = tuple(typed_row("trading_run_account_v1", {
        "run_id": run_id, "run_month": month, "ordinal": ordinal,
        "account_id": account,
    }) for ordinal, account in enumerate(account_ids))
    account_hash = sha256(canonical_json([
        (row["ordinal"], row["content_hash"]) for row in members
    ]).encode("utf-8")).hexdigest()
    fenced = _rows(client,
        "SELECT run_id FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(fenced) > 1:
        raise RuntimeError("Typed run context has duplicated commit fences")
    for name, expected_rows in (("trading_runtime_config_v1", (context,)),
                                ("trading_run_account_v1", members)):
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        query = (f"SELECT {columns} FROM arte.{name} "
                 f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
        expected = [_wire_row(name, row) for row in expected_rows]
        actual = _rows(client, query)
        if actual and sorted(actual, key=canonical_json) != sorted(expected, key=canonical_json):
            raise RuntimeError(f"Typed run {name} conflicts with existing publication")
        if not actual:
            if fenced:
                raise RuntimeError("Committed run context has missing typed rows")
            _insert(client, name, expected_rows, f"run-context:{run_id}:{name}",
                    dispatch_run_context=True)
            actual = _rows(client, query)
        if sorted(actual, key=canonical_json) != sorted(expected, key=canonical_json):
            raise RuntimeError(f"Typed run {name} did not become durable")
    fence = {
        "run_id": run_id, "run_month": month,
        "run_hash": run_hash,
        "config_hash": context["content_hash"],
        "account_count": len(members), "account_hash": account_hash,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    fence_query = (
        "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
        "FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow"
    )
    stable = {key: value for key, value in fence.items() if key != "committed_at"}
    existing = _rows(client, fence_query)
    if existing and (len(existing) != 1 or existing[0] != stable):
        raise RuntimeError("Typed run context fence conflicts with existing publication")
    if not existing:
        _insert(client, "trading_run_context_commit_v1", (fence,),
                f"run-context:{run_id}:commit", dispatch_run_context=True)
        if _rows(client, fence_query) != [stable]:
            raise RuntimeError("Typed run context fence did not become durable")
    dispatch = getattr(client, "typed_insert_dispatch", None)
    if dispatch is not None:
        operations = (("trading_run_v1", f"run:{run_id}"),
                      ("trading_runtime_config_v1",
                       f"run-context:{run_id}:trading_runtime_config_v1"),
                      ("trading_run_account_v1",
                       f"run-context:{run_id}:trading_run_account_v1"),
                      ("trading_run_context_commit_v1", f"run-context:{run_id}:commit"))
        for table, token in operations:
            dispatch.seal_verified_operation(
                run_id=run_id, table=table, token=token, required=False,
                batch_id=_ZERO_DISPATCH_BATCH, batch_last_sequence=0)
        dispatch.compact_verified_run_context(
            run_id=run_id,
            fence_hash=sha256(canonical_json(stable).encode()).hexdigest(),
            operations=operations)


def load_typed_run_context(client: Any, run_id: str) -> dict[str, Any]:
    """Recover only a fully fenced and hash-verified runtime configuration."""
    parent_columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    parents = _rows(client, f"SELECT {parent_columns} FROM arte.trading_run_v1 "
                    f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    config_columns = ",".join(column for column, _ in
                              _CONTRACTS["trading_runtime_config_v1"].columns)
    account_columns = ",".join(column for column, _ in
                               _CONTRACTS["trading_run_account_v1"].columns)
    config_rows = _rows(client, f"SELECT {config_columns} "
                        "FROM arte.trading_runtime_config_v1 "
                        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    accounts = _rows(client, f"SELECT {account_columns} "
                     "FROM arte.trading_run_account_v1 "
                     f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    fences = _rows(client,
        "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
        "FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(parents) != 1 or len(config_rows) != 1 or len(fences) != 1 or not accounts:
        raise RuntimeError("Typed run context has no complete publication fence")
    config = config_rows[0]
    month = str(config["run_month"])
    run_hash = sha256(canonical_json(_canonical_typed_content(
        "trading_run_v1", parents[0], stored_utc=True)).encode("utf-8")).hexdigest()
    if (str(config["run_id"]) != run_id
            or str(parents[0]["run_id"]) != run_id
            or str(parents[0]["run_month"]) != month
            or str(fences[0]["run_id"]) != run_id
            or str(fences[0]["run_month"]) != month):
        raise RuntimeError("Typed run context identity differs from its fence")
    def verified_hash(name: str, row: Mapping[str, Any]) -> str:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(name, content))
                        .encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError(f"Typed run {name} row content differs from its hash")
        return digest
    config_hash = verified_hash("trading_runtime_config_v1", config)
    accounts.sort(key=lambda row: int(row["ordinal"]))
    if (len(accounts) > 65535
            or [int(row["ordinal"]) for row in accounts] != list(range(len(accounts)))
            or len({str(row["account_id"]) for row in accounts}) != len(accounts)
            or any(not str(row["account_id"]).strip() for row in accounts)
            or any(str(row["run_id"]) != run_id or str(row["run_month"]) != month
                   for row in accounts)):
        raise RuntimeError("Typed run account membership is not contiguous")
    if (not str(config["strategy_id"]).strip()
            or int(config["checkpoint_interval_events"]) < 1
            or int(config["safety_supervisor_enabled"]) not in (0, 1)
            or int(config["write_progress_checkpoints"]) not in (0, 1)):
        raise RuntimeError("Typed run configuration is invalid")
    account_hash = sha256(canonical_json([
        (int(row["ordinal"]), verified_hash("trading_run_account_v1", row))
        for row in accounts
    ]).encode("utf-8")).hexdigest()
    if (str(fences[0]["run_hash"]) != run_hash
            or str(fences[0]["config_hash"]) != config_hash
            or int(fences[0]["account_count"]) != len(accounts)
            or str(fences[0]["account_hash"]) != account_hash):
        raise RuntimeError("Typed run context differs from its committed fence")
    return {key: config[key] for key in _RUN_CONFIG_FIELDS} | {
        "run_id": run_id, "run_month": month,
        "mode": str(parents[0]["mode"]),
        "evaluation_interval_ms": parents[0]["evaluation_interval_ms"],
        "session_date": parents[0]["session_date"],
        "configuration_hash": str(parents[0]["configuration_hash"]),
        "code_hash": str(parents[0]["code_hash"]),
        "market_plan_token": str(parents[0]["market_plan_token"]),
        "started_at": str(parents[0]["started_at"]),
        "account_ids": tuple(str(row["account_id"]) for row in accounts),
    }


def _verify_run_identity(client: Any, run_id: str) -> dict[str, Any]:
    rows = _rows(client,
        "SELECT run_id FROM arte.trading_run_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if rows != [{"run_id": run_id}]:
        raise RuntimeError("Typed journal run identity is missing or duplicated")
    context = load_typed_run_context(client, run_id)
    from src.trading_runtime.arte_admission_fence import verify_no_incomplete_admissions
    verify_no_incomplete_admissions(client, run_id)
    if context.get("mode") in {"live", "paper"}:
        from src.trading_runtime.arte_portfolio_sync import verify_no_incomplete_portfolio_syncs
        verify_no_incomplete_portfolio_syncs(client, run_id)
    return context


def _verify_commission_links(
    client: Any, batch: TypedJournalBatch,
    families: tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
    *, journal_profile: str = "v1",
) -> None:
    by_family = dict(families)
    same_batch = {
        (str(row["account_id"]), str(row["execution_id"]))
        for row in by_family["trading_execution_v1"]
    }
    for fee in by_family["trading_commission_v1"]:
        account_id, execution_id = str(fee["account_id"]), str(fee["execution_id"])
        if (account_id, execution_id) in same_batch:
            continue
        candidates = _rows(client,
            "SELECT batch_id,record_id FROM arte.trading_execution_v1 "
            f"WHERE run_id={_literal(batch.run_id)} "
            f"AND account_id={_literal(account_id)} "
            f"AND execution_id={_literal(execution_id)} FORMAT JSONEachRow")
        committed = 0
        for candidate in candidates:
            source_batch = str(UUID(str(candidate["batch_id"])))
            fences = _rows(client,
                f"SELECT batch_id FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
                f"WHERE run_id={_literal(batch.run_id)} "
                f"AND batch_id=toUUID({_literal(source_batch)}) FORMAT JSONEachRow")
            if len(fences) > 1:
                raise RuntimeError("Commission execution has duplicated commit fences")
            committed += len(fences)
        if committed != 1:
            raise RuntimeError("Commission revision requires one committed execution")


def _verify_order_context_links(
    client: Any, batch: TypedJournalBatch,
    families: tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
    *, journal_profile: str = "v1",
) -> None:
    by_family = dict(families)
    exact_parents = {str(UUID(str(row["parent_record_id"])))
                     for row in by_family["trading_strategy_intent_use_v1"]}
    same_batch = {
        (str(row["account_id"]), str(row["intent_id"]))
        for row in by_family["trading_strategy_intent_v1"]
    }
    required = ({
        (str(row["account_id"]), str(row["strategy_intent_id"]))
        for row in by_family["trading_order_command_context_v1"]
        if str(UUID(str(row["parent_record_id"]))) not in exact_parents
    } | {
        (str(row["account_id"]), str(row["strategy_intent_id"]))
        for row in by_family["trading_oms_group_state_v1"]
        if str(UUID(str(row["record_id"]))) not in exact_parents
    }) - same_batch
    if not required:
        return
    intent_ids = ",".join(_literal(value) for value in sorted({value for _, value in required}))
    candidates = _rows(client,
        "SELECT account_id,intent_id,batch_id FROM arte.trading_strategy_intent_v1 "
        f"WHERE run_id={_literal(batch.run_id)} AND intent_id IN ({intent_ids}) "
        "FORMAT JSONEachRow")
    relevant = [row for row in candidates
                if (str(row["account_id"]), str(row["intent_id"])) in required]
    batch_ids = {str(UUID(str(row["batch_id"]))) for row in relevant}
    committed: set[str] = set()
    if batch_ids:
        ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(batch_ids))
        fences = _rows(client,
            f"SELECT batch_id FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
            f"WHERE run_id={_literal(batch.run_id)} AND batch_id IN ({ids}) "
            "FORMAT JSONEachRow")
        committed = {str(UUID(str(row["batch_id"]))) for row in fences}
        if len(fences) != len(committed):
            raise RuntimeError("Strategy intent has duplicated commit fences")
    for key in required:
        matching = [row for row in relevant
                    if (str(row["account_id"]), str(row["intent_id"])) == key
                    and str(UUID(str(row["batch_id"]))) in committed]
        if len(matching) != 1:
            raise RuntimeError("Order command requires one committed strategy intent")


def _verify_exact_intent_uses(
    client: Any, batch: TypedJournalBatch,
    families: tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
    *, journal_profile: str = "v1",
) -> None:
    by_family = dict(families)
    uses = by_family["trading_strategy_intent_use_v1"]
    if not uses:
        return
    events = {str(UUID(str(row["record_id"]))): row for row in by_family["trading_event_v1"]}
    intents = {str(UUID(str(row["record_id"]))): row
               for row in by_family["trading_strategy_intent_v1"]}
    contexts = {str(UUID(str(row["parent_record_id"]))): row
                for row in by_family["trading_order_command_context_v1"]}
    groups = {str(UUID(str(row["record_id"]))): row
              for row in by_family["trading_oms_group_state_v1"]}
    needed = {str(UUID(str(row["intent_record_id"]))) for row in uses} - set(intents)
    if needed:
        ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(needed))
        fence = (
            f"AND batch_id IN (SELECT batch_id FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
            f"WHERE run_id={_literal(batch.run_id)} "
            f"AND last_sequence<={batch.first_sequence - 1}) "
        )
        prior_intents = _rows(client,
            "SELECT record_id,batch_id,account_id,intent_id,content_hash "
            "FROM arte.trading_strategy_intent_v1 "
            f"WHERE run_id={_literal(batch.run_id)} AND record_id IN ({ids}) "
            f"{fence}FORMAT JSONEachRow")
        prior_events = _rows(client,
            "SELECT record_id,batch_id,account_id,sequence,category,entity_type "
            "FROM arte.trading_event_v1 "
            f"WHERE run_id={_literal(batch.run_id)} AND record_id IN ({ids}) "
            f"{fence}FORMAT JSONEachRow")
        if (len(prior_intents) != len(needed) or len(prior_events) != len(needed)
                or len({str(UUID(str(row["record_id"]))) for row in prior_intents}) != len(needed)
                or len({str(UUID(str(row["record_id"]))) for row in prior_events}) != len(needed)):
            raise RuntimeError("Exact intent revision lacks one earlier committed record")
        intents.update({str(UUID(str(row["record_id"]))): row for row in prior_intents})
        events.update({str(UUID(str(row["record_id"]))): row for row in prior_events})
    for use in uses:
        parent_id = str(UUID(str(use["parent_record_id"])))
        intent_id = str(UUID(str(use["intent_record_id"])))
        parent = events[parent_id]
        source = events[intent_id]
        detail = intents[intent_id]
        consumer = groups.get(parent_id) or contexts.get(parent_id)
        if (consumer is None or int(source["sequence"]) >= int(parent["sequence"])
                or source["category"] != "strategy"
                or source["entity_type"] != "strategy_intent"
                or str(UUID(str(source["batch_id"]))) != str(UUID(str(detail["batch_id"])))
                or str(source["account_id"]) != str(use["account_id"])
                or str(detail["account_id"]) != str(use["account_id"])
                or str(detail["intent_id"]) != str(consumer["strategy_intent_id"])
                or str(detail["content_hash"]) != str(use["intent_content_hash"])):
            raise RuntimeError("Intent revision use differs from its committed source or consumer")


_V2_AUTHORITY_SEAL = object()


class _V2WriterAuthority:
    """Private proof that this exact client/run passed constructor preflight."""

    def __init__(self, seal: object, client: Any, run_id: str) -> None:
        if seal is not _V2_AUTHORITY_SEAL:
            raise RuntimeError("V2 writer authority cannot be forged")
        self.client = client
        self.run_id = run_id


def _v3_preflight(client: Any) -> None:
    """Exact opt-in layout and grants; never included in active Live startup."""
    from src.backend.backtest_fixed_v3_preflight import running_v3_preflight

    running_v3_preflight(client)


def v4_storage_contracts() -> tuple[Any, ...]:
    """One exact, deduplicated V4 catalog for every principal's storage audit."""
    installed = fixed_backtest_v2_contracts()
    contracts = (*installed, *V4_COMMIT_TABLES, ENTRY_EVIDENCE,
                 ACKNOWLEDGEMENT, *PROTECTION_CHANGE_TABLES)
    by_name = {}
    for contract in contracts:
        previous = by_name.setdefault(contract.name, contract)
        if previous != contract:
            raise ValueError(f"V4 table has conflicting contracts: {contract.name}")
    return tuple(by_name.values())


def _v4_preflight(client: Any) -> None:
    """Opt-in normalized fence; leave the live V1 startup contract unchanged."""
    installed = fixed_backtest_v2_contracts()
    # A storage_preflight scans active parts as well as schema. Audit the
    # union once: repeating that catalog scan for each family can dominate
    # Backtest startup on a workstation with large market-part catalogs.
    storage_preflight(client, tables=v4_storage_contracts())
    writable = frozenset(
        _v4_family_table(table) for table, _, _, _ in _FAMILIES
    ) | frozenset(table.name for table in V4_COMMIT_TABLES) | {
        ENTRY_EVIDENCE.name, ACKNOWLEDGEMENT.name,
        *(table.name for table in PROTECTION_CHANGE_TABLES)}
    readonly = frozenset(table.name for table in installed) - writable
    journal_permission_preflight(
        client, journal_tables=writable, read_only_tables=readonly)


def _v4_family_table(name: str) -> str:
    """Write current signal shape to V2; occupied legacy V1 is read-only."""
    return ("trading_strategy_signal_v2" if name == "trading_strategy_signal_v1"
            else name)


def _optional_v3_commit_exists(client: Any) -> bool:
    """Check V3 only if installed; older V2 deployments need no V3 DDL."""
    installed = _rows(client,
        "SELECT name FROM system.tables WHERE database='arte' "
        "AND name='trading_commit_v3' FORMAT JSONEachRow")
    if installed not in ([], [{"name": "trading_commit_v3"}]):
        raise RuntimeError("V3 commit table inventory is ambiguous")
    return bool(installed)


def publish_typed_batch(
    client: Any, batch: TypedJournalBatch, *, journal_profile: str = "v1",
) -> str:
    """Public direct publisher; V2 always runs full read-only preflight."""
    return _publish_typed_batch(client, batch, journal_profile=journal_profile)


def publish_typed_squeeze_batch_v3(
    client: Any, unit: V3SqueezeBatch,
) -> str:
    """Direct opt-in V3 publisher; runs full preflight for each public call."""
    if not isinstance(unit, V3SqueezeBatch):
        raise TypeError("V3 publication requires a closed squeeze batch")
    return _publish_typed_batch(
        client, unit.base, journal_profile="backtest_v3",
        squeeze_episodes=unit.episodes,
        reservation_reasons=unit.reservation_reasons,
        reconciliation_differences=unit.reconciliation_differences,
        portfolio_controls=unit.portfolio_controls,
        policy_selections=unit.policy_selections,
        trade_proposal_rows=unit.trade_proposal_rows,
        short_order_skips=unit.short_order_skips,
        broker_reply_policy_events=unit.broker_reply_policy_events,
        broker_reply_policy_messages=unit.broker_reply_policy_messages,
        entry_reprice_deferred=unit.entry_reprice_deferred,
        entry_reprice_capacities=unit.entry_reprice_capacities,
        entry_reprice_capacity_reasons=unit.entry_reprice_capacity_reasons,
        entry_reprice_rejections=unit.entry_reprice_rejections,
        protected_exit_satisfied=unit.protected_exit_satisfied,
        protection_changes=unit.protection_changes,
        protection_entry_orders=unit.protection_entry_orders,
        protected_exit_snapshots=unit.protected_exit_snapshots,
        portfolio_allocation_fills=unit.portfolio_allocation_fills)


def _publish_typed_batch(
    client: Any, batch: TypedJournalBatch, *, journal_profile: str,
    authority: _V2WriterAuthority | None = None,
    squeeze_episodes: tuple[Mapping[str, Any], ...] | None = None,
    reservation_reasons: tuple[Mapping[str, Any], ...] | None = None,
    reconciliation_differences: tuple[Mapping[str, Any], ...] | None = None,
    portfolio_controls: tuple[Mapping[str, Any], ...] | None = None,
    policy_selections: tuple[Any, ...] | None = None,
    trade_proposal_rows: tuple[tuple[str, Mapping[str, Any]], ...] | None = None,
    short_order_skips: tuple[Mapping[str, Any], ...] | None = None,
    broker_reply_policy_events: tuple[Mapping[str, Any], ...] | None = None,
    broker_reply_policy_messages: tuple[Mapping[str, Any], ...] | None = None,
    entry_reprice_deferred: tuple[Mapping[str, Any], ...] | None = None,
    entry_reprice_capacities: tuple[Mapping[str, Any], ...] | None = None,
    entry_reprice_capacity_reasons: tuple[Mapping[str, Any], ...] | None = None,
    entry_reprice_rejections: tuple[Mapping[str, Any], ...] | None = None,
    protected_exit_satisfied: tuple[Mapping[str, Any], ...] | None = None,
    protection_changes: tuple[Mapping[str, Any], ...] | None = None,
    protection_entry_orders: tuple[Mapping[str, Any], ...] | None = None,
    protected_exit_snapshots: tuple[Mapping[str, Any], ...] | None = None,
    portfolio_allocation_fills: tuple[Mapping[str, Any], ...] | None = None,
) -> str:
    """Publish and verify one typed batch, with the commit row written last."""
    if batch.signal_evidence_nodes:
        raise ValueError("Generic signal evidence nodes are retired for new writes")
    if journal_profile not in {"v1", "backtest_v2", "backtest_v3"}:
        raise ValueError("Unknown typed journal profile")
    if journal_profile != "backtest_v3" and (
            squeeze_episodes is not None or reservation_reasons is not None
            or reconciliation_differences is not None
            or portfolio_controls is not None or policy_selections is not None
            or trade_proposal_rows is not None
            or short_order_skips is not None
            or broker_reply_policy_events is not None
            or broker_reply_policy_messages is not None
            or entry_reprice_deferred is not None
            or entry_reprice_capacities is not None
            or entry_reprice_capacity_reasons is not None
            or entry_reprice_rejections is not None
            or protected_exit_satisfied is not None
            or protection_changes is not None
            or protection_entry_orders is not None
            or protected_exit_snapshots is not None
            or portfolio_allocation_fills is not None):
        raise ValueError("V3 child families require a V3-only commit")
    if journal_profile == "backtest_v3" and (
            squeeze_episodes is None or reservation_reasons is None
            or reconciliation_differences is None
            or portfolio_controls is None or policy_selections is None
            or trade_proposal_rows is None
            or short_order_skips is None
            or broker_reply_policy_events is None
            or broker_reply_policy_messages is None
            or entry_reprice_deferred is None
            or entry_reprice_capacities is None
            or entry_reprice_capacity_reasons is None
            or entry_reprice_rejections is None
            or protected_exit_satisfied is None
            or protection_changes is None
            or protection_entry_orders is None
            or protected_exit_snapshots is None
            or portfolio_allocation_fills is None):
        raise ValueError("V3 commit requires explicit closed child families")
    if journal_profile in {"backtest_v2", "backtest_v3"} and batch.status != "running":
        raise ValueError("Terminal Backtest requires separate anchored V2 publication")
    if journal_profile in {"backtest_v2", "backtest_v3"}:
        if authority is None:
            (versioned_journal_v2_preflight if journal_profile == "backtest_v2"
             else _v3_preflight)(client)
            context = _verify_run_identity(client, batch.run_id)
            if context.get("mode") != "backtest":
                raise RuntimeError("V2 journal profile requires a verified Backtest run")
        elif not isinstance(authority, _V2WriterAuthority) or (
            authority.client is not client or authority.run_id != batch.run_id
        ):
            raise RuntimeError("V2 writer authority differs from its client or run")
        legacy = _rows(client,
            "SELECT batch_id FROM arte.trading_commit_v1 "
            f"WHERE run_id={_literal(batch.run_id)} LIMIT 1 FORMAT JSONEachRow")
        if legacy:
            raise RuntimeError("Versioned journal cannot mix with legacy V1 commits")
        other = "trading_commit_v3" if journal_profile == "backtest_v2" else "trading_commit_v2"
        if (journal_profile == "backtest_v3" or _optional_v3_commit_exists(client)) and _rows(
                client, f"SELECT batch_id FROM arte.{other} "
                f"WHERE run_id={_literal(batch.run_id)} LIMIT 1 FORMAT JSONEachRow"):
            raise RuntimeError("Backtest cannot mix V2 and V3 commit fences")
    elif authority is not None:
        raise ValueError("V2 writer authority cannot be used with V1")
    families = _sealed_families(
        batch, v3_episode_ids=tuple(str(row["record_id"])
            for row in squeeze_episodes or ()) if journal_profile == "backtest_v3" else (),
        v3_control_ids=tuple(str(row["record_id"])
            for row in portfolio_controls or ()) if journal_profile == "backtest_v3" else (),
        v3_proposal_ids=tuple(str(row["record_id"]) for name, row in trade_proposal_rows or ()
            if name == TRADE_PROPOSAL_TABLES[0].name) if journal_profile == "backtest_v3" else (),
        v3_short_skip_ids=tuple(str(row["record_id"]) for row in short_order_skips or ())
            if journal_profile == "backtest_v3" else (),
        v3_reply_policy_ids=tuple(str(row["record_id"]) for row in broker_reply_policy_events or ())
            if journal_profile == "backtest_v3" else (),
        v3_reprice_deferred_ids=tuple(str(row["record_id"]) for row in entry_reprice_deferred or ())
            if journal_profile == "backtest_v3" else (),
        v3_reprice_capacity_ids=tuple(str(row["record_id"]) for row in entry_reprice_capacities or ())
            if journal_profile == "backtest_v3" else (),
        v3_reprice_rejected_ids=tuple(str(row["record_id"]) for row in entry_reprice_rejections or ())
            if journal_profile == "backtest_v3" else (),
        v3_protected_exit_satisfied_ids=tuple(str(row["record_id"]) for row in protected_exit_satisfied or ())
            if journal_profile == "backtest_v3" else (),
        v3_protection_change_ids=tuple(str(row["record_id"]) for row in protection_changes or ())
            if journal_profile == "backtest_v3" else (),
        v3_protected_exit_snapshot_ids=tuple(str(row["record_id"]) for row in protected_exit_snapshots or ())
            if journal_profile == "backtest_v3" else (),
        v3_portfolio_allocation_fill_ids=tuple(str(row["record_id"]) for row in portfolio_allocation_fills or ())
            if journal_profile == "backtest_v3" else (),
        v3_reconciliation=journal_profile == "backtest_v3")
    if journal_profile == "backtest_v3":
        from src.backend.backtest_squeeze_episode_v3 import seal_squeeze_family_v3
        # Validate the closed child and its exact parent before any INSERT.
        v3_rows = tuple(dict(row) for row in squeeze_episodes or ())
        reason_rows = tuple(dict(row) for row in reservation_reasons or ())
        difference_rows = tuple(dict(row) for row in reconciliation_differences or ())
        control_rows = tuple(dict(row) for row in portfolio_controls or ())
        proposal_rows = tuple((name, dict(row)) for name, row in trade_proposal_rows or ())
        broker_oms_rows = tuple(tuple(dict(row) for row in family) for family in (
            short_order_skips or (), broker_reply_policy_events or (),
            broker_reply_policy_messages or (), entry_reprice_deferred or ()))
        capacity_rows = tuple(tuple(dict(row) for row in family) for family in (
            entry_reprice_capacities or (), entry_reprice_capacity_reasons or ()))
        rejected_rows = tuple(dict(row) for row in entry_reprice_rejections or ())
        satisfied_rows = tuple(dict(row) for row in protected_exit_satisfied or ())
        protection_rows = tuple(dict(row) for row in protection_changes or ())
        protection_entry_rows = tuple(dict(row) for row in protection_entry_orders or ())
        snapshot_rows = tuple(dict(row) for row in protected_exit_snapshots or ())
        allocation_rows = tuple(dict(row) for row in portfolio_allocation_fills or ())
        selected_hashes = {row["policy_hash"] for row in control_rows
                           if row["control_event"] == "portfolio_policy_selected"}
        supplied = {selection.policy_hash: selection.policy
                    for selection in policy_selections or ()}
        if selected_hashes != set(supplied):
            raise ValueError("V3 policy selection lacks exact catalog publication input")
        reservation_parents = dict(families)[
            "trading_portfolio_reservation_event_v1"]
        reconciliation_parents = dict(families)[
            "trading_portfolio_reconciliation_event_v1"]
        seal_squeeze_family_v3(
            {**{name: "" for name, _ in SQUEEZE_COMMIT_V3.columns if name not in {
                "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
                "portfolio_reservation_reason_count",
                "portfolio_reservation_reason_hash",
                "portfolio_reconciliation_difference_count",
                "portfolio_reconciliation_difference_hash",
                "portfolio_control_count", "portfolio_control_hash",
                "trade_proposal_child_count", "trade_proposal_child_hash",
                "broker_short_order_skip_count", "broker_short_order_skip_hash",
                "broker_reply_policy_event_count", "broker_reply_policy_event_hash",
                "broker_reply_policy_message_count", "broker_reply_policy_message_hash",
                "entry_reprice_deferred_count", "entry_reprice_deferred_hash",
                "entry_reprice_capacity_count", "entry_reprice_capacity_hash",
                "entry_reprice_capacity_reason_count", "entry_reprice_capacity_reason_hash",
                "entry_reprice_rejected_count", "entry_reprice_rejected_hash",
                "protected_exit_satisfied_count", "protected_exit_satisfied_hash",
                "protection_change_count", "protection_change_hash",
                "protected_exit_snapshot_count", "protected_exit_snapshot_hash",
                "portfolio_allocation_fill_count", "portfolio_allocation_fill_hash"}},
             "run_id": batch.run_id, "batch_id": batch.batch_id},
            v3_rows, families[0][1], reservation_reasons=reason_rows,
            parent_reservations=reservation_parents,
            reconciliation_differences=difference_rows,
            parent_reconciliations=reconciliation_parents,
            portfolio_controls=control_rows,
            trade_proposal_rows=proposal_rows,
            short_order_skips=broker_oms_rows[0],
            broker_reply_policy_events=broker_oms_rows[1],
            broker_reply_policy_messages=broker_oms_rows[2],
            entry_reprice_deferred=broker_oms_rows[3],
            entry_reprice_capacities=capacity_rows[0],
            entry_reprice_capacity_reasons=capacity_rows[1],
            entry_reprice_rejections=rejected_rows,
            protected_exit_satisfied=satisfied_rows,
            protection_changes=protection_rows,
            protection_entry_orders=protection_entry_rows,
            protected_exit_snapshots=snapshot_rows,
            portfolio_allocation_fills=allocation_rows)
    else:
        v3_rows = ()
        reason_rows = ()
        reservation_parents = ()
        difference_rows = ()
        reconciliation_parents = ()
        control_rows = ()
        proposal_rows = ()
        broker_oms_rows = ((), (), (), ())
        capacity_rows = ((), ())
        rejected_rows = ()
        protection_rows = ()
        protection_entry_rows = ()
    _verify_commission_links(client, batch, families, journal_profile=journal_profile)
    _verify_exact_intent_uses(client, batch, families, journal_profile=journal_profile)
    _verify_order_context_links(client, batch, families, journal_profile=journal_profile)
    commit_columns = (tuple(name for name, _ in SQUEEZE_COMMIT_V3.columns
                            if name not in {"run_month", "committed_at"})
                      if journal_profile == "backtest_v3" else _COMMIT_COLUMNS)
    existing = _rows(client,
        f"SELECT {','.join(commit_columns)} "
        f"FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
        f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
    if not existing:
        prior = _rows(client,
            f"SELECT batch_id,last_sequence,status FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
            f"WHERE run_id={_literal(batch.run_id)} "
            "ORDER BY last_sequence DESC LIMIT 1 FORMAT JSONEachRow")
        if prior and prior[0]["status"] != "running":
            raise RuntimeError("Typed journal cannot extend a terminal run")
        expected_prior = ((str(UUID(str(prior[0]["batch_id"]))), int(prior[0]["last_sequence"]))
                          if prior else (_ZERO_UUID, 0))
        if expected_prior != (batch.prior_batch_id, batch.first_sequence - 1):
            raise RuntimeError("Typed journal batch does not extend the committed prefix")
    dispatch = getattr(client, "typed_insert_dispatch", None)
    if dispatch is not None:
        dispatch.assert_next_batch(
            run_id=batch.run_id, batch_id=batch.batch_id,
            prior_batch_id=batch.prior_batch_id,
            first_sequence=batch.first_sequence,
            last_sequence=batch.last_sequence)
    if journal_profile == "backtest_v3" and selected_hashes:
        if dispatch is None:
            raise RuntimeError("V3 selected policy requires durable catalog dispatch")
        from src.trading_runtime.arte_portfolio_policy import (
            load_attested_portfolio_policy, publish_portfolio_policy,
        )
        for policy_hash in sorted(selected_hashes):
            policy = supplied[policy_hash]
            if publish_portfolio_policy(client, policy) != policy_hash:
                raise RuntimeError("V3 selected policy catalog hash differs")
            if load_attested_portfolio_policy(client, dispatch, policy_hash) != policy:
                raise RuntimeError("V3 selected policy lacks attested catalog fence")
    hashes: dict[str, str] = {}
    actual = _family_identities(client, batch.batch_id, journal_profile=journal_profile)
    inserted = False
    for name, rows in families:
        expected_ids = _identity(rows)
        hashes[name] = sha256(canonical_json(expected_ids).encode("utf-8")).hexdigest()
        if actual[name] and actual[name] != expected_ids:
            raise RuntimeError(f"{name} has a conflicting or duplicated batch")
        if rows and not actual[name]:
            table = _profile_table(name, journal_profile)
            token = f"{batch.batch_id}:{table}"
            _insert(client, name, rows, token, journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            inserted = True
    if inserted:
        actual = _family_identities(client, batch.batch_id, journal_profile=journal_profile)
        for name, rows in families:
            if actual[name] != _identity(rows):
                raise RuntimeError(f"{name} did not become durable")
    if journal_profile == "backtest_v3":
        from src.backend.backtest_squeeze_episode_v3 import verify_squeeze_family_v3
        child_columns = ",".join(
            f"toString({name}) AS {name}" if kind.startswith("Decimal") else name
            for name, kind in SQUEEZE_EPISODE.columns)
        child_query = (
            f"SELECT {child_columns} FROM arte.{SQUEEZE_EPISODE.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_children = _rows(client, child_query)
        if not actual_children and v3_rows:
            _insert(client, SQUEEZE_EPISODE.name, v3_rows,
                    f"{batch.batch_id}:{SQUEEZE_EPISODE.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_children = _rows(client, child_query)
        reason_columns = ",".join(name for name, _ in RESERVATION_REASON.columns)
        reason_query = (
            f"SELECT {reason_columns} FROM arte.{RESERVATION_REASON.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_reasons = _rows(client, reason_query)
        if not actual_reasons and reason_rows:
            _insert(client, RESERVATION_REASON.name, reason_rows,
                    f"{batch.batch_id}:{RESERVATION_REASON.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_reasons = _rows(client, reason_query)
        difference_columns = ",".join(
            name for name, _ in RECONCILIATION_DIFFERENCE.columns)
        difference_query = (
            f"SELECT {difference_columns} FROM arte.{RECONCILIATION_DIFFERENCE.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_differences = _rows(client, difference_query)
        if not actual_differences and difference_rows:
            _insert(client, RECONCILIATION_DIFFERENCE.name, difference_rows,
                    f"{batch.batch_id}:{RECONCILIATION_DIFFERENCE.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_differences = _rows(client, difference_query)
        control_columns = ",".join(name for name, _ in PORTFOLIO_CONTROL.columns)
        control_query = (
            f"SELECT {control_columns} FROM arte.{PORTFOLIO_CONTROL.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_controls = _rows(client, control_query)
        if not actual_controls and control_rows:
            _insert(client, PORTFOLIO_CONTROL.name, control_rows,
                    f"{batch.batch_id}:{PORTFOLIO_CONTROL.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_controls = _rows(client, control_query)
        actual_proposals = []
        for proposal_table in TRADE_PROPOSAL_TABLES:
            expected_rows = tuple(row for name, row in proposal_rows
                                  if name == proposal_table.name)
            columns = ",".join(name for name, _ in proposal_table.columns)
            query = (f"SELECT {columns} FROM arte.{proposal_table.name} "
                     f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
            actual_rows = _rows(client, query)
            if not actual_rows and expected_rows:
                _insert(client, proposal_table.name, expected_rows,
                        f"{batch.batch_id}:{proposal_table.name}",
                        journal_profile=journal_profile,
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)
                actual_rows = _rows(client, query)
            actual_proposals.extend((proposal_table.name, row) for row in actual_rows)
        actual_broker_oms = []
        for contract, expected_rows in zip(BROKER_OMS_TABLES, broker_oms_rows):
            columns = ",".join(name for name, _ in contract.columns)
            query = (f"SELECT {columns} FROM arte.{contract.name} "
                     f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
            actual_rows = _rows(client, query)
            if not actual_rows and expected_rows:
                _insert(client, contract.name, expected_rows,
                        f"{batch.batch_id}:{contract.name}",
                        journal_profile=journal_profile,
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)
                actual_rows = _rows(client, query)
            actual_broker_oms.append(actual_rows)
        actual_capacity = []
        for contract, expected_rows in zip(ENTRY_REPRICE_CAPACITY_TABLES, capacity_rows):
            columns = ",".join(name for name, _ in contract.columns)
            query = (f"SELECT {columns} FROM arte.{contract.name} "
                     f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
            actual_rows = _rows(client, query)
            if not actual_rows and expected_rows:
                _insert(client, contract.name, expected_rows,
                        f"{batch.batch_id}:{contract.name}",
                        journal_profile=journal_profile,
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)
                actual_rows = _rows(client, query)
            actual_capacity.append(actual_rows)
        rejected_columns = ",".join(name for name, _ in ENTRY_REPRICE_REJECTED.columns)
        rejected_query = (
            f"SELECT {rejected_columns} FROM arte.{ENTRY_REPRICE_REJECTED.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_rejected = _rows(client, rejected_query)
        if not actual_rejected and rejected_rows:
            _insert(client, ENTRY_REPRICE_REJECTED.name, rejected_rows,
                    f"{batch.batch_id}:{ENTRY_REPRICE_REJECTED.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_rejected = _rows(client, rejected_query)
        satisfied_columns = ",".join(name for name, _ in PROTECTED_EXIT_SATISFIED.columns)
        satisfied_query = (
            f"SELECT {satisfied_columns} FROM arte.{PROTECTED_EXIT_SATISFIED.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_satisfied = _rows(client, satisfied_query)
        if not actual_satisfied and satisfied_rows:
            _insert(client, PROTECTED_EXIT_SATISFIED.name, satisfied_rows,
                    f"{batch.batch_id}:{PROTECTED_EXIT_SATISFIED.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_satisfied = _rows(client, satisfied_query)
        actual_protection = []
        for contract, expected_rows in zip(
                PROTECTION_CHANGE_TABLES, (protection_rows, protection_entry_rows)):
            columns = ",".join(
                f"toString({name}) AS {name}" if kind.startswith("Decimal") else name
                for name, kind in contract.columns)
            query = (f"SELECT {columns} FROM arte.{contract.name} "
                     f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
            actual_rows = _rows(client, query)
            if not actual_rows and expected_rows:
                _insert(client, contract.name, expected_rows,
                        f"{batch.batch_id}:{contract.name}",
                        journal_profile=journal_profile,
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)
                actual_rows = _rows(client, query)
            actual_protection.append(actual_rows)
        snapshot_columns = ",".join(
            f"toString({name}) AS {name}" if kind.startswith("Decimal") else name
            for name, kind in PROTECTED_EXIT_SNAPSHOT.columns)
        snapshot_query = (
            f"SELECT {snapshot_columns} FROM arte.{PROTECTED_EXIT_SNAPSHOT.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_snapshots = _rows(client, snapshot_query)
        if not actual_snapshots and snapshot_rows:
            _insert(client, PROTECTED_EXIT_SNAPSHOT.name, snapshot_rows,
                    f"{batch.batch_id}:{PROTECTED_EXIT_SNAPSHOT.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_snapshots = _rows(client, snapshot_query)
        allocation_columns = ",".join(
            f"toString({name}) AS {name}" if kind.startswith("Decimal") else name
            for name, kind in PORTFOLIO_ALLOCATION_FILL.columns)
        allocation_query = (
            f"SELECT {allocation_columns} FROM arte.{PORTFOLIO_ALLOCATION_FILL.name} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        actual_allocations = _rows(client, allocation_query)
        if not actual_allocations and allocation_rows:
            _insert(client, PORTFOLIO_ALLOCATION_FILL.name, allocation_rows,
                    f"{batch.batch_id}:{PORTFOLIO_ALLOCATION_FILL.name}",
                    journal_profile=journal_profile,
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
            actual_allocations = _rows(client, allocation_query)
        # The family verifier also rejects missing, duplicate and extra children.
        base_stub = {name: "" for name, _ in SQUEEZE_COMMIT_V3.columns
                     if name not in {"backtest_squeeze_episode_count",
                                     "backtest_squeeze_episode_hash",
                                     "portfolio_reservation_reason_count",
                                     "portfolio_reservation_reason_hash",
                                     "portfolio_reconciliation_difference_count",
                                     "portfolio_reconciliation_difference_hash",
                                     "portfolio_control_count",
                                     "portfolio_control_hash",
                                     "trade_proposal_child_count",
                                     "trade_proposal_child_hash",
                                     "broker_short_order_skip_count",
                                     "broker_short_order_skip_hash",
                                     "broker_reply_policy_event_count",
                                     "broker_reply_policy_event_hash",
                                     "broker_reply_policy_message_count",
                                     "broker_reply_policy_message_hash",
                                     "entry_reprice_deferred_count",
                                     "entry_reprice_deferred_hash",
                                     "entry_reprice_capacity_count",
                                     "entry_reprice_capacity_hash",
                                     "entry_reprice_capacity_reason_count",
                                     "entry_reprice_capacity_reason_hash",
                                     "entry_reprice_rejected_count",
                                     "entry_reprice_rejected_hash",
                                     "protected_exit_satisfied_count",
                                     "protected_exit_satisfied_hash",
                                     "protection_change_count",
                                     "protection_change_hash",
                                     "protected_exit_snapshot_count",
                                     "protected_exit_snapshot_hash",
                                     "portfolio_allocation_fill_count",
                                     "portfolio_allocation_fill_hash"}}
        base_stub.update(run_id=batch.run_id, batch_id=batch.batch_id)
        child_seal = seal_squeeze_family_v3(
            base_stub, v3_rows, families[0][1],
            reservation_reasons=reason_rows,
            parent_reservations=reservation_parents,
            reconciliation_differences=difference_rows,
            parent_reconciliations=reconciliation_parents,
            portfolio_controls=control_rows,
            trade_proposal_rows=proposal_rows,
            short_order_skips=broker_oms_rows[0],
            broker_reply_policy_events=broker_oms_rows[1],
            broker_reply_policy_messages=broker_oms_rows[2],
            entry_reprice_deferred=broker_oms_rows[3],
            entry_reprice_capacities=capacity_rows[0],
            entry_reprice_capacity_reasons=capacity_rows[1],
            entry_reprice_rejections=rejected_rows,
            protected_exit_satisfied=satisfied_rows,
            protection_changes=protection_rows,
            protection_entry_orders=protection_entry_rows,
            protected_exit_snapshots=snapshot_rows,
            portfolio_allocation_fills=allocation_rows)
        try:
            verified_children = verify_squeeze_family_v3(
                child_seal, actual_children, families[0][1], stored_utc=True,
                reservation_reasons=actual_reasons,
                parent_reservations=reservation_parents,
                reconciliation_differences=actual_differences,
                parent_reconciliations=reconciliation_parents,
                portfolio_controls=actual_controls,
                trade_proposal_rows=actual_proposals,
                short_order_skips=actual_broker_oms[0],
                broker_reply_policy_events=actual_broker_oms[1],
                broker_reply_policy_messages=actual_broker_oms[2],
                entry_reprice_deferred=actual_broker_oms[3],
                entry_reprice_capacities=actual_capacity[0],
                entry_reprice_capacity_reasons=actual_capacity[1],
                entry_reprice_rejections=actual_rejected,
                protected_exit_satisfied=actual_satisfied,
                protection_changes=actual_protection[0],
                protection_entry_orders=actual_protection[1],
                protected_exit_snapshots=actual_snapshots,
                portfolio_allocation_fills=actual_allocations)
        except ValueError as exc:
            raise RuntimeError("V3 child families differ from durable readback") from exc
        if sorted((r["record_id"], r["content_hash"]) for r in verified_children) != sorted(
            (r["record_id"], r["content_hash"]) for r in v3_rows):
            raise RuntimeError("V3 squeeze family differs from durable readback")
    commit = {
        "run_id": batch.run_id,
        "run_month": batch.run_month.isoformat(),
        "attempt_id": batch.attempt_id,
        "batch_id": batch.batch_id,
        "prior_batch_id": batch.prior_batch_id,
        "first_sequence": batch.first_sequence,
        "last_sequence": batch.last_sequence,
        "source_cursor": batch.source_cursor,
        "status": batch.status,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    for name, attribute, count_column, hash_column in _FAMILIES:
        commit[count_column] = len(getattr(batch, attribute))
        commit[hash_column] = hashes[name]
    if journal_profile == "backtest_v3":
        commit = seal_squeeze_family_v3(
            commit, v3_rows, families[0][1],
            reservation_reasons=reason_rows,
            parent_reservations=reservation_parents,
            reconciliation_differences=difference_rows,
            parent_reconciliations=reconciliation_parents,
            portfolio_controls=control_rows,
            trade_proposal_rows=proposal_rows,
            short_order_skips=broker_oms_rows[0],
            broker_reply_policy_events=broker_oms_rows[1],
            broker_reply_policy_messages=broker_oms_rows[2],
            entry_reprice_deferred=broker_oms_rows[3],
            entry_reprice_capacities=capacity_rows[0],
            entry_reprice_capacity_reasons=capacity_rows[1],
            entry_reprice_rejections=rejected_rows,
            protected_exit_satisfied=satisfied_rows,
            protection_changes=protection_rows,
            protection_entry_orders=protection_entry_rows,
            protected_exit_snapshots=snapshot_rows,
            portfolio_allocation_fills=allocation_rows)
    expected = {key: value for key, value in commit.items() if key not in ("run_month", "committed_at")}
    if existing and (len(existing) != 1 or existing[0] != expected):
        raise RuntimeError("Typed journal commit conflicts with an existing batch")
    if not existing:
        table = _profile_table("trading_commit_v1", journal_profile)
        token = f"{batch.batch_id}:{table}:commit"
        _insert(client, "trading_commit_v1", (commit,), token,
                journal_profile=journal_profile,
                dispatch_batch_id=batch.batch_id,
                dispatch_sequence=batch.last_sequence)
        verified = _rows(client,
            f"SELECT {','.join(commit_columns)} "
            f"FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        if verified != [expected]:
            raise RuntimeError("Typed journal commit was not durably published")
    if dispatch is not None:
        required = bool(getattr(client, "typed_insert_strict", False))
        for name, rows in families:
            if not rows:
                continue
            table = _profile_table(name, journal_profile)
            token = f"{batch.batch_id}:{table}"
            dispatch.seal_verified_operation(run_id=batch.run_id, table=table,
                                             token=token, required=required,
                                             batch_id=batch.batch_id,
                                             batch_last_sequence=batch.last_sequence)
        if journal_profile == "backtest_v3" and v3_rows:
            dispatch.seal_verified_operation(
                run_id=batch.run_id, table=SQUEEZE_EPISODE.name,
                token=f"{batch.batch_id}:{SQUEEZE_EPISODE.name}",
                required=required, batch_id=batch.batch_id,
                batch_last_sequence=batch.last_sequence)
        if journal_profile == "backtest_v3" and reason_rows:
            dispatch.seal_verified_operation(
                run_id=batch.run_id, table=RESERVATION_REASON.name,
                token=f"{batch.batch_id}:{RESERVATION_REASON.name}",
                required=required, batch_id=batch.batch_id,
                batch_last_sequence=batch.last_sequence)
        if journal_profile == "backtest_v3" and difference_rows:
            dispatch.seal_verified_operation(
                run_id=batch.run_id, table=RECONCILIATION_DIFFERENCE.name,
                token=f"{batch.batch_id}:{RECONCILIATION_DIFFERENCE.name}",
                required=required, batch_id=batch.batch_id,
                batch_last_sequence=batch.last_sequence)
        if journal_profile == "backtest_v3" and control_rows:
            dispatch.seal_verified_operation(
                run_id=batch.run_id, table=PORTFOLIO_CONTROL.name,
                token=f"{batch.batch_id}:{PORTFOLIO_CONTROL.name}",
                required=required, batch_id=batch.batch_id,
                batch_last_sequence=batch.last_sequence)
        if journal_profile == "backtest_v3":
            for proposal_table in TRADE_PROPOSAL_TABLES:
                if any(name == proposal_table.name for name, _ in proposal_rows):
                    dispatch.seal_verified_operation(
                        run_id=batch.run_id, table=proposal_table.name,
                        token=f"{batch.batch_id}:{proposal_table.name}",
                        required=required, batch_id=batch.batch_id,
                        batch_last_sequence=batch.last_sequence)
            for broker_table, broker_rows in zip(BROKER_OMS_TABLES, broker_oms_rows):
                if broker_rows:
                    dispatch.seal_verified_operation(
                        run_id=batch.run_id, table=broker_table.name,
                        token=f"{batch.batch_id}:{broker_table.name}",
                        required=required, batch_id=batch.batch_id,
                        batch_last_sequence=batch.last_sequence)
            for capacity_table, rows in zip(ENTRY_REPRICE_CAPACITY_TABLES, capacity_rows):
                if rows:
                    dispatch.seal_verified_operation(
                        run_id=batch.run_id, table=capacity_table.name,
                        token=f"{batch.batch_id}:{capacity_table.name}",
                        required=required, batch_id=batch.batch_id,
                        batch_last_sequence=batch.last_sequence)
            if rejected_rows:
                dispatch.seal_verified_operation(
                    run_id=batch.run_id, table=ENTRY_REPRICE_REJECTED.name,
                    token=f"{batch.batch_id}:{ENTRY_REPRICE_REJECTED.name}",
                    required=required, batch_id=batch.batch_id,
                    batch_last_sequence=batch.last_sequence)
            if satisfied_rows:
                dispatch.seal_verified_operation(
                    run_id=batch.run_id, table=PROTECTED_EXIT_SATISFIED.name,
                    token=f"{batch.batch_id}:{PROTECTED_EXIT_SATISFIED.name}",
                    required=required, batch_id=batch.batch_id,
                    batch_last_sequence=batch.last_sequence)
            for contract, rows in zip(
                    PROTECTION_CHANGE_TABLES, (protection_rows, protection_entry_rows)):
                if rows:
                    dispatch.seal_verified_operation(
                        run_id=batch.run_id, table=contract.name,
                        token=f"{batch.batch_id}:{contract.name}",
                        required=required, batch_id=batch.batch_id,
                        batch_last_sequence=batch.last_sequence)
            if snapshot_rows:
                dispatch.seal_verified_operation(
                    run_id=batch.run_id, table=PROTECTED_EXIT_SNAPSHOT.name,
                    token=f"{batch.batch_id}:{PROTECTED_EXIT_SNAPSHOT.name}",
                    required=required, batch_id=batch.batch_id,
                    batch_last_sequence=batch.last_sequence)
            if allocation_rows:
                dispatch.seal_verified_operation(
                    run_id=batch.run_id, table=PORTFOLIO_ALLOCATION_FILL.name,
                    token=f"{batch.batch_id}:{PORTFOLIO_ALLOCATION_FILL.name}",
                    required=required, batch_id=batch.batch_id,
                    batch_last_sequence=batch.last_sequence)
        table = _profile_table("trading_commit_v1", journal_profile)
        dispatch.seal_verified_operation(
            run_id=batch.run_id, table=table,
            token=f"{batch.batch_id}:{table}:commit", required=required,
            batch_id=batch.batch_id,
            batch_last_sequence=batch.last_sequence)
        commit_readback = existing if existing else verified
        commit_hash = sha256(canonical_json(commit_readback[0]).encode()).hexdigest()
        operations = tuple((
            _profile_table(name, journal_profile),
            f"{batch.batch_id}:{_profile_table(name, journal_profile)}"
        ) for name, rows in families if rows) + (
            ((SQUEEZE_EPISODE.name, f"{batch.batch_id}:{SQUEEZE_EPISODE.name}"),)
            if journal_profile == "backtest_v3" and v3_rows else ()) + (
            ((RESERVATION_REASON.name, f"{batch.batch_id}:{RESERVATION_REASON.name}"),)
            if journal_profile == "backtest_v3" and reason_rows else ()) + (
            ((RECONCILIATION_DIFFERENCE.name,
              f"{batch.batch_id}:{RECONCILIATION_DIFFERENCE.name}"),)
            if journal_profile == "backtest_v3" and difference_rows else ()) + (
            ((PORTFOLIO_CONTROL.name,
              f"{batch.batch_id}:{PORTFOLIO_CONTROL.name}"),)
            if journal_profile == "backtest_v3" and control_rows else ()) + tuple(
            (table.name, f"{batch.batch_id}:{table.name}")
            for table in TRADE_PROPOSAL_TABLES
            if journal_profile == "backtest_v3" and any(
                name == table.name for name, _ in proposal_rows)) + tuple(
            (table.name, f"{batch.batch_id}:{table.name}")
            for table, rows in zip(BROKER_OMS_TABLES, broker_oms_rows)
            if journal_profile == "backtest_v3" and rows) + tuple(
            (table.name, f"{batch.batch_id}:{table.name}")
            for table, rows in zip(ENTRY_REPRICE_CAPACITY_TABLES, capacity_rows)
            if journal_profile == "backtest_v3" and rows) + (
            ((ENTRY_REPRICE_REJECTED.name,
              f"{batch.batch_id}:{ENTRY_REPRICE_REJECTED.name}"),)
            if journal_profile == "backtest_v3" and rejected_rows else ()) + (
            ((PROTECTED_EXIT_SATISFIED.name,
              f"{batch.batch_id}:{PROTECTED_EXIT_SATISFIED.name}"),)
            if journal_profile == "backtest_v3" and satisfied_rows else ()) + tuple(
            (contract.name, f"{batch.batch_id}:{contract.name}")
            for contract, rows in zip(
                PROTECTION_CHANGE_TABLES, (protection_rows, protection_entry_rows))
            if journal_profile == "backtest_v3" and rows) + (
            ((PROTECTED_EXIT_SNAPSHOT.name,
              f"{batch.batch_id}:{PROTECTED_EXIT_SNAPSHOT.name}"),)
            if journal_profile == "backtest_v3" and snapshot_rows else ()) + (
            ((PORTFOLIO_ALLOCATION_FILL.name,
              f"{batch.batch_id}:{PORTFOLIO_ALLOCATION_FILL.name}"),)
            if journal_profile == "backtest_v3" and allocation_rows else ()) + ((
            table, f"{batch.batch_id}:{table}:commit"),)
        dispatch.compact_verified_batch(
            run_id=batch.run_id, batch_id=batch.batch_id,
            prior_batch_id=batch.prior_batch_id,
            first_sequence=batch.first_sequence, last_sequence=batch.last_sequence,
            commit_hash=commit_hash, operations=operations)
    return batch.batch_id


def load_committed_prefix(
    client: Any, run_id: str, *, journal_profile: str = "v1",
) -> CommittedPrefix | V2CommittedPrefix | None:
    """Verify the entire contiguous typed prefix; ignore unfenced fact rows."""
    if not run_id:
        raise ValueError("Journal run identity is required")
    if journal_profile not in {"v1", "backtest_v2"}:
        raise ValueError("Unknown typed journal profile")
    if journal_profile == "backtest_v2":
        # Cold readers can be SELECT-only. Writer grants were checked at
        # construction; this path validates physical contracts without INSERT.
        storage_preflight(client, tables=versioned_journal_v2_contracts())
        if _rows(client,
            "SELECT batch_id FROM arte.trading_commit_v1 "
            f"WHERE run_id={_literal(run_id)} LIMIT 1 FORMAT JSONEachRow"):
            raise RuntimeError("V2 journal cannot mix with legacy V1 commits")
    commits = _rows(client,
        f"SELECT {','.join(_COMMIT_COLUMNS)} FROM arte.{_profile_table('trading_commit_v1', journal_profile)} "
        f"WHERE run_id={_literal(run_id)} ORDER BY last_sequence,batch_id FORMAT JSONEachRow")
    if not commits:
        return None
    prior_id = _ZERO_UUID
    prior_sequence = 0
    prior_status = "running"
    batch_ids: list[str] = []
    for commit in commits:
        cursor = commit["source_cursor"]
        if not isinstance(cursor, str) or not cursor or _without_text_prefix(cursor).startswith(("{", "[")):
            raise RuntimeError("Typed journal commit has an opaque source cursor")
        batch_id = str(UUID(str(commit["batch_id"])))
        if (str(commit["run_id"]) != run_id
                or str(UUID(str(commit["prior_batch_id"]))) != prior_id
                or int(commit["first_sequence"]) != prior_sequence + 1
                or int(commit["last_sequence"]) < int(commit["first_sequence"])
                or int(commit["event_count"]) != (
                    int(commit["last_sequence"]) - int(commit["first_sequence"]) + 1)):
            raise RuntimeError("Typed journal commit chain is not contiguous")
        if commit["status"] not in {"running", "completed", "stopped", "failed"}:
            raise RuntimeError("Typed journal commit has invalid status")
        if batch_ids and prior_status != "running":
            raise RuntimeError("Typed journal continues after terminal status")
        prior_id = batch_id
        prior_sequence = int(commit["last_sequence"])
        prior_status = str(commit["status"])
        batch_ids.append(batch_id)
    chunk: list[dict[str, Any]] = []
    row_budget = 0
    for commit in commits:
        expected_rows = sum(int(commit[count_key]) for _, _, count_key, _ in _FAMILIES)
        if chunk and (len(chunk) >= 32 or row_budget + expected_rows > 50_000):
            _verify_recovery_chunk(client, chunk, journal_profile=journal_profile)
            chunk = []
            row_budget = 0
        chunk.append(commit)
        row_budget += expected_rows
    if chunk:
        _verify_recovery_chunk(client, chunk, journal_profile=journal_profile)
    prefix_type = V2CommittedPrefix if journal_profile == "backtest_v2" else CommittedPrefix
    return prefix_type(run_id, prior_sequence, prior_id,
                       str(commits[-1]["source_cursor"]),
                       str(commits[-1]["status"]), tuple(batch_ids))


def _verify_recovery_chunk(
    client: Any, commits: list[dict[str, Any]], *, journal_profile: str = "v1",
) -> None:
    batch_ids = tuple(str(UUID(str(commit["batch_id"]))) for commit in commits)
    ids = ",".join(f"toUUID({_literal(batch_id)})" for batch_id in batch_ids)
    actual = {batch_id: {name: [] for name, _, _, _ in _FAMILIES}
              for batch_id in batch_ids}
    for name, _, _, _ in _FAMILIES:
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        rows = _rows(client, f"SELECT {columns} FROM arte.{_profile_table(name, journal_profile)} "
                     f"WHERE batch_id IN ({ids}) FORMAT JSONEachRow")
        for row in rows:
            batch_id = str(UUID(str(row["batch_id"])))
            if batch_id not in actual:
                raise RuntimeError("Typed journal recovery returned an unexpected batch")
            content = {key: value for key, value in row.items() if key != "content_hash"}
            digest = sha256(canonical_json(_canonical_typed_content(
                name, content, stored_utc=True)).encode("utf-8")).hexdigest()
            if digest != str(row["content_hash"]):
                raise RuntimeError(f"Typed journal {name} row content differs from its hash")
            actual[batch_id][name].append((str(UUID(str(row["record_id"]))), digest))
    for commit, batch_id in zip(commits, batch_ids):
        for name, _, count_key, hash_key in _FAMILIES:
            rows = sorted(actual[batch_id][name])
            digest = sha256(canonical_json(rows).encode("utf-8")).hexdigest()
            if len(rows) != int(commit[count_key]) or digest != str(commit[hash_key]):
                raise RuntimeError(f"Typed journal {name} differs from committed fence")


VerifiedPrefix = CommittedPrefix | V2CommittedPrefix


def _valid_prefix(prefix: object) -> bool:
    # V3 is defined in the fixed Backtest package, which imports this writer.
    # Resolve its type only at call time to avoid an import cycle.
    from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    return isinstance(prefix, (CommittedPrefix, V2CommittedPrefix,
                               V3CommittedPrefix, V4CommittedPrefix)) and bool(prefix.batch_ids)


def _committed_batch_filter(
    prefix: VerifiedPrefix, *, batch_column: str = "batch_id",
) -> str:
    """Exclude interrupted fact inserts before page LIMIT or duplicate checks."""
    if not _valid_prefix(prefix):
        raise ValueError("Journal page requires a verified committed prefix")
    if batch_column not in {"batch_id", "c.batch_id"}:
        raise ValueError("Journal page has an invalid batch column")
    from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    fence = ("trading_commit_v4" if isinstance(prefix, V4CommittedPrefix)
             else "trading_commit_v3" if isinstance(prefix, V3CommittedPrefix)
             else "trading_commit_v2" if isinstance(prefix, V2CommittedPrefix)
             else "trading_commit_v1")
    return (
        f"AND {batch_column} IN (SELECT batch_id FROM arte.{fence} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND last_sequence<={int(prefix.last_sequence)}) "
    )


def load_committed_order_command_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Read one bounded page of commands from an already verified prefix.

    A durable command is an intent, not evidence of external delivery. A cold
    dispatcher must reconcile the client ID with the broker before any resend.
    """
    if not _valid_prefix(prefix):
        raise ValueError("Order recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Order recovery page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND ((category='order_management' AND entity_type='order_command') "
        "OR (category='command' AND entity_type='order')) "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed command page repeated an event identity")
    allowed_batches = set(prefix.batch_ids)
    if any(str(UUID(str(row["batch_id"]))) not in allowed_batches for row in events):
        raise RuntimeError("Order page contains an unfenced event")
    names = ",".join(column for column, _ in _CONTRACTS["trading_order_command_v1"].columns)
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    details = _rows(client, f"SELECT {names} FROM arte.trading_order_command_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed command page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed command page details differ from events")
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        record_id = str(UUID(str(event["record_id"])))
        detail = by_id[record_id]
        if (sequence <= prior
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or str(detail["event_month"]) != str(event["event_month"])
                or str(detail["account_id"]) != str(event["account_id"])):
            raise RuntimeError("Committed command page differs from its event envelope")
        prior = sequence
        result.append({"sequence": sequence, "event_time": event["event_time"],
                       **detail})
    return tuple(result)


def load_committed_order_context_page(
    client: Any, prefix: VerifiedPrefix,
    commands: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    """Join at most one typed strategy context to each committed command."""
    if not _valid_prefix(prefix):
        raise ValueError("Order recovery requires a verified committed prefix")
    if not commands or len(commands) > 1000:
        raise ValueError("Command context page must contain 1-1000 commands")
    by_id = {str(UUID(str(command["record_id"]))): command for command in commands}
    if len(by_id) != len(commands) or any(
        str(command["run_id"]) != prefix.run_id for command in commands
    ):
        raise ValueError("Command context page identity is invalid")
    ids = ",".join(f"toUUID({_literal(value)})" for value in by_id)
    columns = ",".join(column for column, _ in
                       _CONTRACTS["trading_order_command_context_v1"].columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.trading_order_command_context_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND parent_record_id IN ({ids}) "
        f"{_committed_batch_filter(prefix)}"
        f"LIMIT {len(commands) + 1} FORMAT JSONEachRow")
    if len(rows) > len(commands):
        raise RuntimeError("Committed order context page has excess rows")
    contexts: dict[str, dict[str, Any]] = {}
    for row in rows:
        parent_id = str(UUID(str(row["parent_record_id"])))
        command = by_id.get(parent_id)
        if command is None or parent_id in contexts:
            raise RuntimeError("Committed order context lacks one command parent")
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(
            "trading_order_command_context_v1", content, stored_utc=True,
        )).encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError("Committed order context differs from its row hash")
        if (str(UUID(str(row["batch_id"]))) != str(UUID(str(command["batch_id"])))
                or row["event_month"] != command["event_month"]
                or row["account_id"] != command["account_id"]):
            raise RuntimeError("Committed order context differs from its command")
        contexts[parent_id] = row
    for parent_id, command in by_id.items():
        has_strategy = bool(str(command.get("strategy_id") or ""))
        if has_strategy != (parent_id in contexts):
            raise RuntimeError("Strategy command lacks its typed order context")
    if contexts:
        use_columns = ",".join(column for column, _ in
                               _CONTRACTS["trading_strategy_intent_use_v1"].columns)
        context_ids = ",".join(f"toUUID({_literal(parent_id)})" for parent_id in contexts)
        uses = _rows(client,
            f"SELECT {use_columns} FROM arte.trading_strategy_intent_use_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND parent_record_id IN ({context_ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {len(contexts) + 1} FORMAT JSONEachRow")
        if len(uses) > len(contexts):
            raise RuntimeError("Strategy command has excess intent-revision links")
        uses_by_parent: dict[str, dict[str, Any]] = {}
        for use in uses:
            parent_id = str(UUID(str(use["parent_record_id"])))
            content = {key: value for key, value in use.items() if key != "content_hash"}
            digest = sha256(canonical_json(_canonical_typed_content(
                "trading_strategy_intent_use_v1", content, stored_utc=True,
            )).encode("utf-8")).hexdigest()
            if (parent_id not in contexts or parent_id in uses_by_parent
                    or digest != str(use["content_hash"])
                    or str(UUID(str(use["batch_id"]))) != str(UUID(str(by_id[parent_id]["batch_id"])))
                    or use["account_id"] != contexts[parent_id]["account_id"]
                    or use["event_month"] != contexts[parent_id]["event_month"]):
                raise RuntimeError("Strategy command has an invalid exact intent-revision link")
            uses_by_parent[parent_id] = use
        resolved: dict[str, dict[str, Any]] = {}
        if uses_by_parent:
            exact_ids = ",".join(f"toUUID({_literal(str(UUID(str(use['intent_record_id']))))})"
                                 for use in uses_by_parent.values())
            exact = _rows(client,
                "SELECT record_id,batch_id,account_id,intent_id,content_hash "
                "FROM arte.trading_strategy_intent_v1 "
                f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({exact_ids}) "
                f"{_committed_batch_filter(prefix)}"
                f"LIMIT {len(uses_by_parent) + 1} FORMAT JSONEachRow")
            by_exact = {str(UUID(str(row["record_id"]))): row for row in exact}
            if len(exact) != len(by_exact):
                raise RuntimeError("Strategy command has duplicated exact intent revisions")
            for parent_id, use in uses_by_parent.items():
                candidate = by_exact.get(str(UUID(str(use["intent_record_id"]))))
                context = contexts[parent_id]
                if (candidate is None or candidate["account_id"] != context["account_id"]
                        or candidate["intent_id"] != context["strategy_intent_id"]
                        or candidate["content_hash"] != use["intent_content_hash"]):
                    raise RuntimeError("Strategy command exact intent revision differs from source")
                resolved[parent_id] = candidate
        legacy = {parent_id: context for parent_id, context in contexts.items()
                  if parent_id not in uses_by_parent}
        if legacy:
            wanted = {(str(row["account_id"]), str(row["strategy_intent_id"]))
                      for row in legacy.values()}
            intent_ids = ",".join(_literal(value) for value in sorted({value for _, value in wanted}))
            accounts = ",".join(_literal(value) for value in sorted({value for value, _ in wanted}))
            candidates = _rows(client,
                "SELECT record_id,batch_id,account_id,intent_id,content_hash "
                "FROM arte.trading_strategy_intent_v1 "
                f"WHERE run_id={_literal(prefix.run_id)} "
                f"AND account_id IN ({accounts}) AND intent_id IN ({intent_ids}) "
                f"{_committed_batch_filter(prefix)}"
                f"LIMIT {len(legacy) + 1} FORMAT JSONEachRow")
            if len(candidates) > len(legacy):
                raise RuntimeError("Strategy command has ambiguous intent candidates")
            intents_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for candidate in candidates:
                key = (str(candidate["account_id"]), str(candidate["intent_id"]))
                if key not in wanted or key in intents_by_key:
                    raise RuntimeError("Strategy command has an ambiguous typed intent")
                intents_by_key[key] = candidate
            if set(intents_by_key) != wanted:
                raise RuntimeError("Strategy command lacks its committed typed intent")
            for parent_id, context in legacy.items():
                resolved[parent_id] = intents_by_key[(str(context["account_id"]),
                                                     str(context["strategy_intent_id"]))]
        unique_sources = {str(UUID(str(row["record_id"]))) for row in resolved.values()}
        source_ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(unique_sources))
        sources = _rows(client,
            "SELECT record_id,batch_id,sequence,account_id,category,entity_type "
            "FROM arte.trading_event_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND record_id IN ({source_ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {len(unique_sources) + 1} FORMAT JSONEachRow")
        by_source = {str(UUID(str(row["record_id"]))): row for row in sources}
        if len(sources) != len(unique_sources) or set(by_source) != unique_sources:
            raise RuntimeError("Strategy command intent event is missing or duplicated")
        allowed_batches = set(prefix.batch_ids)
        for parent_id, context in contexts.items():
            command = by_id[parent_id]
            intent = resolved[parent_id]
            source = by_source[str(UUID(str(intent["record_id"])))]
            if (str(UUID(str(source["batch_id"]))) != str(UUID(str(intent["batch_id"])))
                    or str(UUID(str(source["batch_id"]))) not in allowed_batches
                    or source["account_id"] != context["account_id"]
                    or source["category"] != "strategy"
                    or source["entity_type"] != "strategy_intent"
                    or int(source["sequence"]) >= int(command["sequence"])):
                raise RuntimeError("Strategy command intent is not an earlier committed event")
    return contexts


def load_committed_order_transition_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Read a bounded page of fence-verified order-state transitions."""
    if not _valid_prefix(prefix):
        raise ValueError("Order recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Order recovery page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND category='order_management' AND entity_type='order_transition' "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed transition page repeated an event identity")
    allowed_batches = set(prefix.batch_ids)
    if any(str(UUID(str(row["batch_id"]))) not in allowed_batches for row in events):
        raise RuntimeError("Order transition page contains an unfenced event")
    names = ",".join(column for column, _ in _CONTRACTS["trading_order_transition_v1"].columns)
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    details = _rows(client, f"SELECT {names} FROM arte.trading_order_transition_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed transition page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed transition page details differ from events")
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        detail = by_id[str(UUID(str(event["record_id"])))]
        if (sequence <= prior
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or str(detail["event_month"]) != str(event["event_month"])
                or str(detail["account_id"]) != str(event["account_id"])):
            raise RuntimeError("Committed transition page differs from its event envelope")
        prior = sequence
        result.append({"sequence": sequence, "event_time": event["event_time"],
                       **detail})
    return tuple(result)


def load_committed_execution_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Read committed fill evidence without admitting interrupted inserts."""
    return _load_committed_execution_detail_page(
        client, prefix, "fill", "trading_execution_v1",
        after_sequence=after_sequence, limit=limit,
    )


def load_committed_run_transition_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Read normalized run status from a fully verified committed prefix."""
    if not _valid_prefix(prefix):
        raise ValueError("Run transition recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Run transition page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time,entity_id "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND category='lifecycle' AND entity_type='run' "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed lifecycle page repeated an event identity")
    if any(str(UUID(str(row["batch_id"]))) not in prefix.batch_ids for row in events):
        raise RuntimeError("Lifecycle page contains an unfenced event")
    columns = ",".join(column for column, _ in _CONTRACTS["trading_run_transition_v1"].columns)
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    details = _rows(client, f"SELECT {columns} FROM arte.trading_run_transition_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed lifecycle page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed lifecycle page details differ from events")
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        detail = by_id[str(UUID(str(event["record_id"])))]
        if (sequence <= prior or event["entity_id"] != prefix.run_id
                or event["account_id"] or detail["account_id"]
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or detail["event_month"] != event["event_month"]
                or detail["source_event_time"] != event["event_time"]):
            raise RuntimeError("Committed lifecycle page differs from its event envelope")
        prior = sequence
        result.append({"sequence": sequence, **detail})
    return tuple(result)


def load_committed_operational_fault_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Cold-read broker/risk faults after verifying the complete commit chain."""
    if not _valid_prefix(prefix):
        raise ValueError("Operational fault recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Operational fault page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time,category,entity_type,entity_id "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND ((category='broker' AND entity_type='connection_state') "
        "OR (category='risk' AND entity_type='risk_snapshot')) "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed fault page repeated an event identity")
    if any(str(UUID(str(row["batch_id"]))) not in prefix.batch_ids for row in events):
        raise RuntimeError("Fault page contains an unfenced event")
    columns = ",".join(column for column, _ in _CONTRACTS["trading_operational_fault_v1"].columns)
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    details = _rows(client, f"SELECT {columns} FROM arte.trading_operational_fault_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed fault page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed fault page details differ from events")
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        detail = by_id[str(UUID(str(event["record_id"])))]
        expected_status = {
            ("broker", "connection_state"): "disconnected",
            ("risk", "risk_snapshot"): "stale",
        }.get((event["category"], event["entity_type"]))
        if (sequence <= prior or expected_status is None
                or event["entity_id"] != prefix.run_id
                or event["account_id"] or detail["account_id"]
                or detail["status"] != expected_status
                or int(detail["entries_frozen"]) != 1
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or detail["event_month"] != event["event_month"]
                or detail["source_event_time"] != event["event_time"]):
            raise RuntimeError("Committed fault page differs from its event envelope")
        prior = sequence
        result.append({"sequence": sequence, "category": event["category"],
                       "entity_type": event["entity_type"], **detail})
    return tuple(result)


def load_committed_account_risk_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 250,
) -> tuple[dict[str, Any], ...]:
    """Cold-read typed account risk metrics with bounded ordered reasons."""
    if not _valid_prefix(prefix):
        raise ValueError("Account risk recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 500:
        raise ValueError("Account risk page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time,entity_id "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND category='risk' AND entity_type='continuous_risk_state' "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed account risk page repeated an event identity")
    if any(str(UUID(str(row["batch_id"]))) not in prefix.batch_ids for row in events):
        raise RuntimeError("Account risk page contains an unfenced event")
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    columns = ",".join(column for column, _ in _CONTRACTS["trading_account_risk_state_v1"].columns)
    details = _rows(client, f"SELECT {columns} FROM arte.trading_account_risk_state_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed account risk page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed account risk details differ from events")
    child_budget = sum(int(row["reason_count"]) for row in details)
    if child_budget > 10_000:
        raise ValueError("Account risk reason page exceeds bound; lower the state limit")
    reason_columns = ",".join(column for column, _ in
                              _CONTRACTS["trading_account_risk_reason_v1"].columns)
    reasons = _rows(client, f"SELECT {reason_columns} FROM arte.trading_account_risk_reason_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND parent_record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}"
                    f"LIMIT {child_budget + 1} FORMAT JSONEachRow")
    if len(reasons) != child_budget:
        raise RuntimeError("Committed account risk reasons have missing or excess rows")
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for reason in reasons:
        parent_id = str(UUID(str(reason["parent_record_id"])))
        parent = by_id.get(parent_id)
        if (parent is None
                or str(UUID(str(reason["batch_id"]))) != str(UUID(str(parent["batch_id"])))
                or reason["event_month"] != parent["event_month"]):
            raise RuntimeError("Committed account risk reason differs from its state")
        by_parent.setdefault(parent_id, []).append(reason)
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        risk_id = str(UUID(str(event["record_id"])))
        detail = by_id[risk_id]
        children = sorted(by_parent.get(risk_id, []), key=lambda row: int(row["ordinal"]))
        if (sequence <= prior or event["entity_id"] != event["account_id"]
                or detail["account_id"] != event["account_id"]
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or detail["event_month"] != event["event_month"]
                or detail["source_event_time"] != event["event_time"]
                or len(children) != int(detail["reason_count"])
                or [int(row["ordinal"]) for row in children] != list(range(len(children)))
                or len({row["reason"] for row in children}) != len(children)):
            raise RuntimeError("Committed account risk page differs from its event or reasons")
        prior = sequence
        result.append({"sequence": sequence, **detail,
                       "reasons": tuple(row["reason"] for row in children)})
    return tuple(result)


def load_committed_commission_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[dict[str, Any], ...]:
    """Read committed fee revisions, including those after the original fill."""
    return _load_committed_execution_detail_page(
        client, prefix, "commission", "trading_commission_v1",
        after_sequence=after_sequence, limit=limit,
    )


def _load_committed_execution_detail_page(
    client: Any, prefix: VerifiedPrefix, entity_type: str, table: str,
    *, after_sequence: int, limit: int,
) -> tuple[dict[str, Any], ...]:
    if not _valid_prefix(prefix):
        raise ValueError("Execution recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Execution recovery page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,event_month,account_id,event_time,entity_id "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} "
        f"AND sequence<={int(prefix.last_sequence)} "
        "AND category='execution' "
        f"AND entity_type={_literal(entity_type)} "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed execution page repeated an event identity")
    if any(str(UUID(str(row["batch_id"]))) not in prefix.batch_ids for row in events):
        raise RuntimeError("Execution page contains an unfenced event")
    columns = ",".join(column for column, _ in _CONTRACTS[table].columns)
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    details = _rows(client, f"SELECT {columns} FROM arte.{table} "
                    f"WHERE run_id={_literal(prefix.run_id)} "
                    f"AND record_id IN ({ids_sql}) "
                    f"{_committed_batch_filter(prefix)}FORMAT JSONEachRow")
    if len(details) != len(events):
        raise RuntimeError("Committed execution page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in details}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed execution page details differ from events")
    result = []
    prior = after_sequence
    for event in events:
        sequence = int(event["sequence"])
        detail = by_id[str(UUID(str(event["record_id"])))]
        if (sequence <= prior
                or str(UUID(str(detail["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or detail["event_month"] != event["event_month"]
                or detail["account_id"] != event["account_id"]
                or detail["execution_id"] != event["entity_id"]
                or detail["source_event_time"] != event["event_time"]):
            raise RuntimeError("Committed execution page differs from its event envelope")
        prior = sequence
        result.append({"sequence": sequence, **detail})
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _DurabilityBarrier:
    """Queue marker ordered after every earlier typed publication."""


@dataclass(frozen=True, slots=True)
class _AdmissionUnit:
    batch: TypedJournalBatch
    captured: CapturedPortfolioSnapshot


@dataclass(frozen=True, slots=True)
class _PortfolioSyncUnit:
    batch: TypedJournalBatch
    captured: CapturedPortfolioSnapshot


@dataclass(frozen=True, slots=True)
class _TerminalBacktestUnit:
    batch: TypedJournalBatch
    captured: tuple[CapturedPortfolioSnapshot, ...]


class ArteJournalWriter:
    """A bounded, single-owner persistence lane with asynchronous receipts."""

    def __init__(self, client: Any, *, run_id: str, capacity: int = 8,
                 max_events_per_commit: int = 4096,
                 coalesce_batches: bool = True,
                 journal_profile: str = "v1") -> None:
        if capacity < 1 or max_events_per_commit < 1:
            raise ValueError("Journal queue capacity and commit bound must be positive")
        # Startup/control-plane validation, before a publication thread exists.
        # Never attempt to create tables or repair misplaced parts here.
        if journal_profile == "v1":
            storage_preflight(client)
            journal_permission_preflight(client)
        elif journal_profile == "backtest_v2":
            versioned_journal_v2_preflight(client)
        elif journal_profile == "backtest_v3":
            if coalesce_batches:
                raise ValueError("V3 squeeze batches require explicit uncoalesced children")
            _v3_preflight(client)
        elif journal_profile == "backtest_v4":
            if coalesce_batches:
                raise ValueError("V4 batches require explicit uncoalesced commits")
            from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
            if (getattr(client, "typed_insert_strict", False) is not True
                    or not isinstance(getattr(client, "typed_insert_dispatch", None),
                                      TypedInsertDispatch)):
                raise RuntimeError("V4 writer requires a strict Keeper-fenced insert dispatch")
            _v4_preflight(client)
        else:
            raise ValueError("Unknown typed journal profile")
        context = _verify_run_identity(client, run_id)
        if not isinstance(context, dict) or context.get("mode") not in {
            "live", "paper", "replay", "backtest", "integration_test",
        }:
            raise RuntimeError("Typed journal writer lacks a verified run mode")
        self._client = client
        self._run_id = run_id
        self._run_mode = context["mode"]
        self._journal_profile = journal_profile
        if journal_profile in {"backtest_v2", "backtest_v3", "backtest_v4"} and self._run_mode != "backtest":
            raise RuntimeError("Versioned journal profile requires a verified Backtest run")
        if self._run_mode == "backtest":
            account_ids = context.get("account_ids")
            if (not isinstance(account_ids, (tuple, list)) or not account_ids
                    or len(set(account_ids)) != len(account_ids)
                    or any(not isinstance(account_id, str) or not account_id
                           for account_id in account_ids)):
                raise RuntimeError("Backtest journal lacks verified account membership")
            self._run_account_ids = frozenset(account_ids)
        else:
            self._run_account_ids = frozenset()
        self._v2_authority = (
            _V2WriterAuthority(_V2_AUTHORITY_SEAL, client, run_id)
            if journal_profile in {"backtest_v2", "backtest_v3"} else None
        )
        self._max_events_per_commit = max_events_per_commit
        self._coalesce_batches = coalesce_batches
        self._queue: Queue[
            tuple[TypedJournalBatch | PreparedPortfolioSnapshot | CapturedPortfolioSnapshot
                  | V4StrategyOneEntryBatch | V4BrokerAcknowledgementBatch
                  | V4ProtectionChangeBatch
                  | _DurabilityBarrier | _AdmissionUnit
                  | _PortfolioSyncUnit | _TerminalBacktestUnit,
                  Future[str]] | None
        ] = Queue(maxsize=capacity)
        self._submission_lock = Lock()
        self._error: BaseException | None = None
        self._accepted_writes = False
        self._last_commit_id: str | None = None
        self._closed = False
        self._client_closed = False
        self._client_close_done = Event()
        self._client_close_error: BaseException | None = None
        self._metrics_lock = Lock()
        self._committed_units = 0
        self._failed_units = 0
        self._publish_ns_total = 0
        self._publish_ns_max = 0
        self._thread = Thread(target=self._run, name="arte-journal-writer", daemon=False)
        self._thread.start()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def run_mode(self) -> str:
        return self._run_mode

    @property
    def max_events_per_commit(self) -> int:
        return self._max_events_per_commit

    @property
    def coalesce_batches(self) -> bool:
        return self._coalesce_batches

    @property
    def journal_profile(self) -> str:
        return self._journal_profile

    def metrics(self) -> dict[str, int | bool]:
        """Cheap control-plane snapshot; never waits for the persistence worker."""
        with self._metrics_lock:
            return {
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "committed_units": self._committed_units,
                "failed_units": self._failed_units,
                "publish_ns_total": self._publish_ns_total,
                "publish_ns_max": self._publish_ns_max,
                "failed": self._error is not None,
            }

    def submit(self, batch: TypedJournalBatch) -> Future[str]:
        """Enqueue without waiting; the receipt names the durable combined batch."""
        with self._submission_lock:
            if self._journal_profile in {"backtest_v3", "backtest_v4"}:
                raise RuntimeError("Versioned writer requires an explicit family envelope")
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            if batch.run_id != self._run_id:
                raise ValueError("Typed journal writer cannot mix runs")
            if self._run_mode == "backtest" and batch.status != "running":
                raise ValueError("Terminal Backtest requires anchored account snapshots")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((batch, receipt))
            except Full as exc:
                raise JournalQueueFull("Typed journal queue is full; stop new admission") from exc
            self._accepted_writes = True
        return receipt

    def submit_base_v4(self, batch: TypedJournalBatch) -> Future[str]:
        """Queue an explicitly limited V4 base batch; no network I/O on caller."""
        if self._journal_profile != "backtest_v4" or not isinstance(batch, TypedJournalBatch):
            raise ValueError("V4 base submission requires its opt-in writer profile")
        if batch.status != "running":
            raise ValueError("V4 terminal publication requires a recovery anchor")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("V4 writer is closed or failed")
            if batch.run_id != self._run_id:
                raise ValueError("V4 writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((batch, receipt))
            except Full as exc:
                raise JournalQueueFull("V4 journal queue is full; stop admission") from exc
            self._accepted_writes = True
            return receipt

    def submit_strategy_one_entry_v4(self, unit: V4StrategyOneEntryBatch) -> Future[str]:
        """Queue the intent and its typed child without blocking execution."""
        if self._journal_profile != "backtest_v4" or not isinstance(
                unit, V4StrategyOneEntryBatch):
            raise ValueError("Strategy 1 entry requires the V4 writer profile")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("V4 writer is closed or failed")
            if unit.base.run_id != self._run_id:
                raise ValueError("V4 writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((unit, receipt))
            except Full as exc:
                raise JournalQueueFull("V4 journal queue is full; stop admission") from exc
            self._accepted_writes = True
            return receipt

    def submit_broker_acknowledgement_v4(
            self, unit: V4BrokerAcknowledgementBatch) -> Future[str]:
        """Queue one sealed broker reply without network I/O on the caller."""
        if (self._journal_profile != "backtest_v4"
                or not isinstance(unit, V4BrokerAcknowledgementBatch)):
            raise ValueError("V4 broker reply requires its typed writer profile")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("V4 writer is closed or failed")
            if unit.base.run_id != self._run_id:
                raise ValueError("V4 writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((unit, receipt))
            except Full as exc:
                raise JournalQueueFull("V4 journal queue is full; stop admission") from exc
            self._accepted_writes = True
            return receipt

    def submit_protection_change_v4(
            self, unit: V4ProtectionChangeBatch) -> Future[str]:
        """Queue normalized protection evidence without waiting on ClickHouse."""
        if (self._journal_profile != "backtest_v4"
                or not isinstance(unit, V4ProtectionChangeBatch)):
            raise ValueError("V4 protection change requires its typed writer profile")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("V4 writer is closed or failed")
            if unit.base.run_id != self._run_id:
                raise ValueError("V4 writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((unit, receipt))
            except Full as exc:
                raise JournalQueueFull("V4 journal queue is full; stop admission") from exc
            self._accepted_writes = True
            return receipt

    def submit_squeeze_v3(self, unit: V3SqueezeBatch) -> Future[str]:
        """Queue a closed V3 family without waiting for ClickHouse."""
        if self._journal_profile != "backtest_v3" or not isinstance(unit, V3SqueezeBatch):
            raise ValueError("V3 squeeze submission requires its opt-in writer profile")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("V3 squeeze writer is closed or failed")
            if unit.base.run_id != self._run_id:
                raise ValueError("V3 squeeze writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((unit, receipt))
            except Full as exc:
                raise JournalQueueFull("V3 squeeze queue is full; stop admission") from exc
            self._accepted_writes = True
            return receipt

    def submit_portfolio_snapshot(self, prepared: PreparedPortfolioSnapshot) -> Future[str]:
        """Enqueue immutable recovery rows without ClickHouse I/O or waiting."""
        from src.trading_runtime.arte_portfolio_snapshot import PreparedPortfolioSnapshot

        if self._journal_profile == "backtest_v2":
            raise RuntimeError("V2 portfolio recovery requires the terminal suffix fence")
        if not isinstance(prepared, PreparedPortfolioSnapshot):
            raise TypeError("Portfolio journal submission requires prepared immutable rows")
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            if prepared.run_id != self._run_id:
                raise ValueError("Typed journal writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((prepared, receipt))
            except Full as exc:
                raise JournalQueueFull("Typed journal queue is full; stop new admission") from exc
            self._accepted_writes = True
        return receipt

    def submit_captured_portfolio_snapshot(
        self, captured: CapturedPortfolioSnapshot,
    ) -> Future[str]:
        """Queue a cheap immutable actor capture; normalize only on this worker."""
        from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot

        if self._journal_profile == "backtest_v2":
            raise RuntimeError("V2 portfolio recovery requires the terminal suffix fence")
        if not isinstance(captured, CapturedPortfolioSnapshot):
            raise TypeError("Portfolio journal submission requires a frozen capture")
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            if captured.run_id != self._run_id:
                raise ValueError("Typed journal writer cannot mix runs")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((captured, receipt))
            except Full as exc:
                raise JournalQueueFull("Typed journal queue is full; stop new admission") from exc
            self._accepted_writes = True
        return receipt

    def submit_barrier(self) -> Future[str]:
        """Return immediately; resolve after every prior queued write is durable.

        A Keeper claim can be attached to this single ordered receipt after
        the admission has queued its event and recovery-state writes.
        """
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            if not self._accepted_writes:
                raise ValueError("Durability barrier requires a prior journal write")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((_DurabilityBarrier(), receipt))
            except Full as exc:
                raise JournalQueueFull("Typed journal queue is full; stop new admission") from exc
        return receipt

    def submit_admission(
        self, batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
    ) -> Future[str]:
        """Queue one fenced admission without blocking the realtime caller.

        The worker writes a persistent prepared fence, events, recovery image,
        and committed fence in that order. The receipt resolves only after
        the final fence is verified; failure retains the Keeper claim.
        """
        from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot

        if self._journal_profile == "backtest_v2":
            raise RuntimeError("V2 Backtest cannot publish live admission units")
        if not isinstance(batch, TypedJournalBatch) or not isinstance(
            captured, CapturedPortfolioSnapshot
        ):
            raise TypeError("Admission requires typed events and a frozen portfolio capture")
        if batch.run_id != self._run_id or captured.run_id != self._run_id:
            raise ValueError("Typed admission cannot mix runs")
        if (not captured.account_id or type(captured.state_revision) is not int
                or captured.state_revision < 1
                or not isinstance(captured.snapshot_at, datetime)
                or captured.snapshot_at.tzinfo is None
                or any(str(event["account_id"]) not in {"", captured.account_id}
                       for event in batch.events)
                or not any(str(event["account_id"]) == captured.account_id
                           for event in batch.events)):
            raise ValueError("Typed admission requires one causal account recovery image")
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            barrier: Future[str] = Future()
            try:
                self._queue.put_nowait((_AdmissionUnit(batch, captured), barrier))
            except Full as exc:
                raise JournalQueueFull("Typed admission queue is full; stop admission") from exc
            self._accepted_writes = True
        return barrier

    def submit_portfolio_sync(
        self, batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
    ) -> Future[str]:
        """Queue reconciliation event and snapshot behind one late durable fence."""
        from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot

        if self._journal_profile == "backtest_v2":
            raise RuntimeError("V2 Backtest cannot publish live portfolio sync")
        if (not isinstance(batch, TypedJournalBatch)
                or not isinstance(captured, CapturedPortfolioSnapshot)
                or batch.run_id != self._run_id or captured.run_id != self._run_id
                or len(batch.events) != 1
                or len(batch.portfolio_reconciliation_events) != 1
                or batch.events[0]["account_id"] != captured.account_id):
            raise ValueError("Typed portfolio sync needs one account reconciliation event")
        with self._submission_lock:
            if self._closed or self._error is not None:
                raise RuntimeError("Typed journal writer cannot accept portfolio sync")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((_PortfolioSyncUnit(batch, captured), receipt))
            except Full as exc:
                raise JournalQueueFull("Typed portfolio sync queue is full") from exc
            self._accepted_writes = True
        return receipt

    def submit_terminal_backtest(
        self, batch: TypedJournalBatch,
        captured: tuple[CapturedPortfolioSnapshot, ...],
    ) -> Future[str]:
        """Enqueue terminal events and every account recovery image as one unit.

        The caller captures state at the terminal simulation boundary. The
        worker commits events first, then each typed snapshot, then each
        prefix-to-snapshot anchor. A receipt resolves only after all anchors.
        """
        from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot

        if self._journal_profile == "backtest_v2":
            raise RuntimeError("V2 terminal publication requires the staged separate fence")

        if (not isinstance(batch, TypedJournalBatch)
                or self._run_mode != "backtest"
                or batch.status not in {"completed", "stopped", "failed"}
                or batch.run_id != self._run_id or not captured
                or any(not isinstance(row, CapturedPortfolioSnapshot)
                       or row.run_id != self._run_id for row in captured)
                or {row.account_id for row in captured} != self._run_account_ids
                or len(captured) != len(self._run_account_ids)):
            raise ValueError("Terminal Backtest requires one capture per run account")
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Typed journal writer is closed")
            if self._error is not None:
                raise RuntimeError("Typed journal writer failed") from self._error
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((_TerminalBacktestUnit(batch, tuple(captured)), receipt))
            except Full as exc:
                raise JournalQueueFull("Terminal Backtest queue is full; stop execution") from exc
            self._accepted_writes = True
        return receipt

    def _run(self) -> None:
        held: tuple[
            TypedJournalBatch | PreparedPortfolioSnapshot | CapturedPortfolioSnapshot
            | _DurabilityBarrier | _AdmissionUnit | _PortfolioSyncUnit | _TerminalBacktestUnit,
            Future[str],
        ] | None = None
        while True:
            item = held if held is not None else self._queue.get()
            held = None
            if item is None:
                self._queue.task_done()
                return
            group = [item]
            stopping = False
            while True:
                try:
                    following = self._queue.get_nowait()
                except Empty:
                    break
                if following is None:
                    stopping = True
                    break
                if (self._coalesce_batches
                        and isinstance(group[-1][0], TypedJournalBatch)
                        and isinstance(following[0], TypedJournalBatch)
                        and _can_coalesce(group[-1][0], following[0], self._max_events_per_commit)
                        and following[0].last_sequence - group[0][0].first_sequence + 1
                        <= self._max_events_per_commit):
                    group.append(following)
                else:
                    held = following
                    break
            started_ns = perf_counter_ns()
            try:
                if self._error is not None:
                    raise RuntimeError("Typed journal writer failed earlier") from self._error
                if (self._journal_profile in {"backtest_v2", "backtest_v3", "backtest_v4"}
                        and not isinstance(group[0][0],
                                           (TypedJournalBatch, V3SqueezeBatch,
                                            V4StrategyOneEntryBatch,
                                            V4BrokerAcknowledgementBatch,
                                            V4ProtectionChangeBatch,
                                            _DurabilityBarrier))
                        and not (self._journal_profile == "backtest_v4"
                                 and isinstance(group[0][0], _TerminalBacktestUnit))):
                    raise RuntimeError("Versioned journal cannot route legacy snapshot or admission units")
                if isinstance(group[0][0], V4ProtectionChangeBatch):
                    from src.trading_runtime.arte_journal_commit_v4 import (
                        publish_protection_change_batch_v4,
                    )
                    unit = group[0][0]
                    committed_id = publish_protection_change_batch_v4(
                        self._client, unit.base, change=unit.change,
                        entry_orders=unit.entry_orders)
                elif isinstance(group[0][0], V4BrokerAcknowledgementBatch):
                    from src.trading_runtime.arte_journal_commit_v4 import (
                        publish_broker_acknowledgement_batch_v4,
                    )
                    unit = group[0][0]
                    committed_id = publish_broker_acknowledgement_batch_v4(
                        self._client, unit.base,
                        acknowledgement=unit.acknowledgement)
                elif isinstance(group[0][0], V4StrategyOneEntryBatch):
                    from src.trading_runtime.arte_journal_commit_v4 import (
                        publish_strategy_one_entry_batch_v4,
                    )
                    unit = group[0][0]
                    committed_id = publish_strategy_one_entry_batch_v4(
                        self._client, unit.base, entry_evidence=unit.entry_evidence)
                elif isinstance(group[0][0], V3SqueezeBatch):
                    unit = group[0][0]
                    committed_id = _publish_typed_batch(
                        self._client, unit.base, journal_profile="backtest_v3",
                        authority=self._v2_authority,
                        squeeze_episodes=unit.episodes,
                        reservation_reasons=unit.reservation_reasons,
                        reconciliation_differences=unit.reconciliation_differences,
                        portfolio_controls=unit.portfolio_controls,
                        policy_selections=unit.policy_selections,
                        trade_proposal_rows=unit.trade_proposal_rows,
                        short_order_skips=unit.short_order_skips,
                        broker_reply_policy_events=unit.broker_reply_policy_events,
                        broker_reply_policy_messages=unit.broker_reply_policy_messages,
                        entry_reprice_deferred=unit.entry_reprice_deferred,
                        entry_reprice_capacities=unit.entry_reprice_capacities,
                        entry_reprice_capacity_reasons=unit.entry_reprice_capacity_reasons,
                        entry_reprice_rejections=unit.entry_reprice_rejections,
                        protected_exit_satisfied=unit.protected_exit_satisfied,
                        protection_changes=unit.protection_changes,
                        protection_entry_orders=unit.protection_entry_orders,
                        protected_exit_snapshots=unit.protected_exit_snapshots,
                        portfolio_allocation_fills=unit.portfolio_allocation_fills)
                elif isinstance(group[0][0], TypedJournalBatch):
                    batch = _coalesce_unpublished(tuple(row for row, _ in group))
                    if self._journal_profile == "v1":
                        committed_id = publish_typed_batch(self._client, batch)
                    elif self._journal_profile == "backtest_v4":
                        from src.trading_runtime.arte_journal_commit_v4 import (
                            publish_base_typed_batch_v4,
                        )
                        committed_id = publish_base_typed_batch_v4(self._client, batch)
                    else:
                        committed_id = _publish_typed_batch(
                            self._client, batch, journal_profile=self._journal_profile,
                            authority=self._v2_authority)
                elif isinstance(group[0][0], _DurabilityBarrier):
                    if self._last_commit_id is None:
                        raise RuntimeError("Durability barrier has no committed predecessor")
                    committed_id = self._last_commit_id
                elif isinstance(group[0][0], _AdmissionUnit):
                    from src.trading_runtime.arte_admission_fence import publish_fenced_admission
                    unit = group[0][0]
                    committed_id = publish_fenced_admission(
                        self._client, unit.batch, unit.captured)
                elif isinstance(group[0][0], _PortfolioSyncUnit):
                    from src.trading_runtime.arte_portfolio_sync import publish_fenced_portfolio_sync
                    unit = group[0][0]
                    committed_id = publish_fenced_portfolio_sync(
                        self._client, unit.batch, unit.captured)
                elif isinstance(group[0][0], _TerminalBacktestUnit):
                    unit = group[0][0]
                    if self._journal_profile == "backtest_v4":
                        from src.trading_runtime.arte_journal_commit_v4 import (
                            publish_terminal_typed_batch_v4,
                        )
                        prefix = publish_terminal_typed_batch_v4(
                            self._client, unit.batch, captures=unit.captured)
                        committed_id = prefix.last_batch_id
                    else:
                        from src.trading_runtime.arte_backtest_snapshot_anchor import (
                            publish_terminal_backtest_snapshots,
                        )
                        context = load_typed_run_context(self._client, unit.batch.run_id)
                        if (context["mode"] != "backtest"
                                or set(context["account_ids"]) != {
                                    row.account_id for row in unit.captured}):
                            raise RuntimeError("Terminal Backtest captures differ from run accounts")
                        committed_id = publish_typed_batch(self._client, unit.batch)
                        prefix = load_committed_prefix(self._client, unit.batch.run_id)
                        if (prefix is None or prefix.last_batch_id != committed_id
                                or prefix.last_sequence != unit.batch.last_sequence):
                            raise RuntimeError("Terminal Backtest event prefix is not committed")
                        publish_terminal_backtest_snapshots(
                            self._client, prefix, unit.captured)
                else:
                    from src.trading_runtime.arte_portfolio_snapshot import (
                        CapturedPortfolioSnapshot, prepare_captured_portfolio_snapshot,
                        publish_prepared_portfolio_snapshot,
                    )
                    snapshot = group[0][0]
                    if isinstance(snapshot, CapturedPortfolioSnapshot):
                        snapshot = prepare_captured_portfolio_snapshot(snapshot)
                    committed_id = publish_prepared_portfolio_snapshot(self._client, snapshot)
                self._last_commit_id = committed_id
                elapsed_ns = perf_counter_ns() - started_ns
                with self._metrics_lock:
                    self._committed_units += len(group)
                    self._publish_ns_total += elapsed_ns
                    self._publish_ns_max = max(self._publish_ns_max, elapsed_ns)
                for _, receipt in group:
                    if receipt.cancelled():
                        continue
                    try:
                        receipt.set_result(committed_id)
                    except InvalidStateError:
                        if not receipt.cancelled():
                            raise
            except BaseException as exc:
                self._error = exc
                with self._metrics_lock:
                    self._failed_units += len(group)
                for _, receipt in group:
                    if not receipt.done():
                        try:
                            receipt.set_exception(exc)
                        except InvalidStateError:
                            # The consumer can cancel while this thread settles
                            # the receipt. A pre-completed receipt cannot mask
                            # the original publication failure or kill the lane.
                            pass
            finally:
                for _ in group:
                    self._queue.task_done()
            if stopping:
                self._queue.task_done()
                return

    def close(self) -> None:
        """Drain only from a control-plane shutdown, never a market callback."""
        with self._submission_lock:
            enqueue_stop = not self._closed
            self._closed = True
        if enqueue_stop:
            self._queue.put(None)
        self._thread.join()
        with self._submission_lock:
            close_client = not self._client_closed
            self._client_closed = True
        if close_client:
            try:
                close = getattr(self._client, "close", None)
                if close is not None:
                    close()
            except BaseException as exc:
                self._client_close_error = exc
            finally:
                self._client_close_done.set()
        else:
            self._client_close_done.wait()
        if self._error is not None:
            raise RuntimeError("Typed journal did not drain durably") from self._error
        if self._client_close_error is not None:
            raise RuntimeError("Typed journal client did not close") from self._client_close_error
