"""Inactive dedicated Strategy 1 Live V4 journal principal and client factory.

This is not an order-admission gate. A caller must hold a separate exclusive
Keeper lease, and complete live cold recovery remains unimplemented. No DDL,
grant, credential, or ClickHouse row operation is performed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import os
import platform
import re
from typing import Any, Callable

from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, V4_COMMIT_TABLES, journal_permission_preflight,
    storage_preflight,
)
from src.trading_runtime.arte_journal_writer import (
    _FAMILIES, _v4_family_table, v4_storage_contracts,
)
from src.trading_runtime.arte_broker_acknowledgement_v5 import ACKNOWLEDGEMENT_V5
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE,
)
from src.backend.live_strategy_one_approval import TABLE as APPROVAL
from src.backend.live_plan_membership import TABLES as PLAN_MEMBERSHIP_TABLES
from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


PRINCIPAL = "strategy_one_live_v4_runner"
MANAGED_URL = "http://DESKTOP-SAAI85T:18123"
_CONFIG_READ = (frozenset({NODE_TABLE.split(".", 1)[1],
                           RELEASE_TABLE.split(".", 1)[1], APPROVAL.name})
                | frozenset(table.name for table in PLAN_MEMBERSHIP_TABLES))
_LEGACY_COMMIT_READ = frozenset({"trading_commit_v1", "trading_commit_v2"})
_EXCLUDED_FAMILY = frozenset({
    "trading_backtest_cursor_v1", "trading_backtest_market_authority_v1",
    "trading_backtest_progress_v1", "trading_prepared_v7_lease_v1",
})
_V4_LIVE_DETAIL = frozenset({
    ACKNOWLEDGEMENT_V5.name,
    "trading_strategy_one_entry_evidence_v1",
    "trading_portfolio_allocation_fill_v4",
    "trading_portfolio_reservation_reason_v1",
    "trading_broker_acknowledgement_v4",
    "trading_order_cancel_activity_v4", "trading_order_reprice_v4",
    "trading_risk_action_v4", "trading_risk_action_reply_v4",
    "trading_oms_execution_tactic_v1", "trading_oms_execution_step_v1",
    "trading_protection_change_v3", "trading_protection_entry_order_v3",
    "trading_protection_reconciliation_v4",
    "trading_protection_reconciliation_action_v4",
    "trading_protection_reconciliation_reply_v4",
})


def live_v4_storage_contracts() -> tuple[Any, ...]:
    """Keep live-only normalized extensions out of Backtest's storage gate."""
    return (*v4_storage_contracts(), ACKNOWLEDGEMENT_V5)
_PORTFOLIO_WRITE = frozenset({
    "trading_portfolio_snapshot_v1", "trading_portfolio_disabled_strategy_v1",
    "trading_portfolio_command_v1", "trading_portfolio_request_v1",
    "trading_portfolio_request_reason_v1", "trading_portfolio_reservation_v1",
    "trading_portfolio_allocation_v1", "trading_portfolio_reconciliation_v1",
    "trading_portfolio_snapshot_commit_v1", "trading_admission_fence_v1",
    "trading_portfolio_sync_fence_v1",
    "trading_portfolio_sync_snapshot_marker_v1",
})
_CONTEXT_WRITE = frozenset({
    "trading_run_v1", "trading_runtime_config_v1", "trading_run_account_v1",
    "trading_run_context_commit_v1",
})
_POLICY_READ = frozenset({
    "trading_portfolio_policy_v1", "trading_portfolio_policy_security_type_v1",
    "trading_portfolio_policy_currency_v1",
    "trading_portfolio_policy_restricted_symbol_v1",
    "trading_portfolio_policy_execution_policy_v1",
    "trading_portfolio_policy_protection_profile_v1",
    "trading_portfolio_policy_commit_v2",
})


@dataclass(frozen=True, slots=True)
class LiveV4PrincipalPlan:
    principal: str
    select_arte: frozenset[str]
    insert_arte: frozenset[str]
    select_system: frozenset[str]
    select_reference: frozenset[tuple[str, str]]

    def grants(self) -> tuple[str, ...]:
        return tuple(
            [f"GRANT SELECT ON arte.{name} TO {self.principal}"
             for name in sorted(self.select_arte)]
            + [f"GRANT INSERT ON arte.{name} TO {self.principal}"
               for name in sorted(self.insert_arte)]
            + [f"GRANT SELECT ON {database}.{name} TO {self.principal}"
               for database, name in sorted(self.select_reference)]
            + [f"GRANT SELECT ON system.{name} TO {self.principal}"
               for name in sorted(self.select_system)])


@lru_cache(maxsize=1)
def desired_plan() -> LiveV4PrincipalPlan:
    family = frozenset(_v4_family_table(name) for name, _, _, _ in _FAMILIES
                       if name not in _EXCLUDED_FAMILY)
    writable = (family | frozenset(table.name for table in V4_COMMIT_TABLES)
                | _V4_LIVE_DETAIL | _PORTFOLIO_WRITE | _CONTEXT_WRITE)
    contracts = {table.name for table in live_v4_storage_contracts()}
    if not writable | _POLICY_READ <= contracts:
        raise RuntimeError("Live V4 principal references an unmodeled table")
    if any("backtest" in name for name in writable):
        raise RuntimeError("Live V4 principal would write a Backtest table")
    return LiveV4PrincipalPlan(
        PRINCIPAL, writable | _POLICY_READ | _CONFIG_READ | _LEGACY_COMMIT_READ
        | MARKET_READ_TABLES,
        writable,
        frozenset({"storage_policies", "tables", "columns", "parts",
                   "data_skipping_indices"}),
        frozenset({("q_live", "market_stock_split_v1")}),
    )


def live_v4_preflight(client: Any) -> None:
    """Verify exact live grants and SSD layout without a row mutation."""
    plan = desired_plan()
    contracts = {table.name: table for table in live_v4_storage_contracts()}
    names = plan.insert_arte | _POLICY_READ
    storage_preflight(client, tables=tuple(contracts[name] for name in sorted(names)))
    storage_preflight(client, tables=PLAN_MEMBERSHIP_TABLES)
    # The configuration tree has its own exact schema verifier; the approval
    # contract is separately checked by journal_permission_preflight.
    journal_permission_preflight(
        client, journal_tables=plan.insert_arte,
        read_only_tables=plan.select_arte - plan.insert_arte - MARKET_READ_TABLES,
        reference_read_tables=plan.select_reference)
    if client.execute("SELECT currentUser()").strip() != PRINCIPAL:
        raise RuntimeError("Live V4 credential authenticates as another principal")


class _LiveRunOwner(KeeperAssignmentHead):
    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(c in run_id for c in "\r\n\x00")):
            raise ValueError("Live V4 run identity is invalid")
        return "/trading/strategy-one-live-v4/v1/" + sha256(run_id.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LiveV4KeeperLease:
    owner: _LiveRunOwner
    run_id: str
    owner_id: str
    epoch: int

    @classmethod
    def acquire(cls, session: ManagedKeeperSession, *, run_id: str,
                owner_id: str) -> "LiveV4KeeperLease":
        owner = _LiveRunOwner(session)
        epoch = owner.acquire(run_id, owner_id=owner_id)
        if epoch is None:
            raise RuntimeError("Live V4 run Keeper claim is held")
        return cls(owner, run_id, owner_id, epoch)

    def assert_current(self) -> None:
        if not self.owner.is_current(self.run_id, owner_id=self.owner_id,
                                     epoch=self.epoch):
            raise RuntimeError("Live V4 Keeper lease lost")

    def release(self) -> bool:
        return self.owner.release(self.run_id, owner_id=self.owner_id,
                                  epoch=self.epoch)


class LiveV4WriterClient:
    """Keep ordinary live-client calls read-only; dispatch owns INSERT calls.

    This is an application guard, not a substitute for the Keeper operation
    receipt or the dedicated ClickHouse principal's exact grants.
    """

    def __init__(self, raw: Any, lease: LiveV4KeeperLease) -> None:
        self._raw = raw
        self.live_v4_lease = lease
        self.typed_insert_strict = True
        self.typed_insert_dispatch: Any = None
        self.typed_sync_insert_dispatch: Any = None

    def execute(self, sql: str, **kwargs: Any) -> Any:
        grant_audit = sql == "SHOW GRANTS FINAL"
        select = isinstance(sql, str) and re.match(
            r"^\s*(?:SELECT|WITH)\b", sql, re.IGNORECASE)
        mutation = isinstance(sql, str) and re.search(
            r"\b(?:INSERT|ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|"
            r"KILL|GRANT|REVOKE|ATTACH|DETACH)\b", sql, re.IGNORECASE)
        if not grant_audit and (not select or mutation or ";" in sql):
            raise RuntimeError("Live V4 direct ClickHouse mutation is forbidden")
        return self._raw.execute(sql, **kwargs)

    def execute_registered_insert(self, sql: str, *, query_id: str) -> Any:
        self.live_v4_lease.assert_current()
        match = re.match(r"^INSERT INTO arte\.([a-z][a-z0-9_]*) \(", sql)
        if (match is None or match.group(1) not in desired_plan().insert_arte
                or not re.fullmatch(r"arte_(?:typed|sync)_[0-9a-f]{64}", query_id)
                or "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" not in sql):
            raise RuntimeError("Live V4 INSERT lacks a registered typed dispatch contract")
        table = match.group(1)
        digest = sha256(sql.encode()).hexdigest()
        if query_id.startswith("arte_typed_"):
            from src.trading_runtime.arte_typed_insert_dispatch import _operation_path

            dispatch = self.typed_insert_dispatch
            if dispatch is None or dispatch.keeper is not self.live_v4_lease.owner._session.client:
                raise RuntimeError("Live V4 typed dispatch authority is missing")
            gate, _ = dispatch._read_gate(self.live_v4_lease.run_id)
            try:
                value, _ = dispatch.keeper.get(_operation_path(
                    self.live_v4_lease.run_id, query_id))
                fields = value.decode("ascii").split("\n")
            except Exception as exc:
                raise RuntimeError("Live V4 typed INSERT lacks a Keeper operation") from exc
            if (gate.mode != "open" or gate.inflight < 1 or len(fields) != 9
                    or fields[:4] != ["3", self.live_v4_lease.run_id, table, query_id]
                    or fields[5] != digest or fields[8] != "pending"):
                raise RuntimeError("Live V4 typed INSERT is not pending in Keeper")
        else:
            from src.trading_runtime.arte_portfolio_sync_dispatch import _query_id

            dispatch = self.typed_sync_insert_dispatch
            if dispatch is None or dispatch.keeper is not self.live_v4_lease.owner._session.client:
                raise RuntimeError("Live V4 sync dispatch authority is missing")
            gate, _ = dispatch._read(self.live_v4_lease.run_id)
            operation = (gate.marker if table == "trading_portfolio_sync_snapshot_marker_v1"
                         else gate.fence if table == "trading_portfolio_sync_fence_v1"
                         else None)
            if (gate.mode != "open" or operation is None
                    or operation.status != "pending" or operation.sql_hash != digest
                    or query_id != _query_id(self.live_v4_lease.run_id,
                                             gate.account_id, gate.revision, table)):
                raise RuntimeError("Live V4 sync INSERT is not pending in Keeper")
        self.live_v4_lease.assert_current()
        return self._raw.execute(sql, query_id=query_id)

    def close(self) -> None:
        self._raw.close()


def open_live_v4_client(
    *, lease: LiveV4KeeperLease | None, endpoint: str,
    credential_user: str, credential_password: str,
    client_factory: Callable[[str, str, str], Any],
) -> Any:
    """Factory admits an owned client only under an existing current lease."""
    if not isinstance(lease, LiveV4KeeperLease):
        raise RuntimeError("Live V4 client requires an exclusive Keeper lease")
    if endpoint != MANAGED_URL or platform.node().upper() != "DESKTOP-SAAI85T":
        raise ValueError("Live V4 requires the managed workstation ClickHouse endpoint")
    if credential_user != PRINCIPAL or not isinstance(credential_password, str) \
            or len(credential_password) < 40:
        raise ValueError("Live V4 requires its own private principal credential")
    lease.assert_current()
    raw = client_factory(endpoint, credential_user, credential_password)
    try:
        live_v4_preflight(raw)
        lease.assert_current()
        from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
        client = LiveV4WriterClient(raw, lease)
        client.typed_insert_dispatch = TypedInsertDispatch(lease.owner._session.client)
        return client
    except BaseException:
        close = getattr(raw, "close", None)
        if callable(close):
            close()
        raise


def live_v4_client_from_env(*, lease: LiveV4KeeperLease) -> Any:
    """Open the provisioned live principal; never fall back to another user."""
    from research.mlops.clickhouse import ClickHouseHttpClient
    from src.backend.managed_live_strategy_one_credentials import (
        load_managed_live_v4_credentials,
    )
    from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport

    load_managed_live_v4_credentials()
    endpoint = os.environ.get("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD", "")
    if endpoint != MANAGED_URL or user != PRINCIPAL or len(password) < 40:
        raise RuntimeError("Provisioned Strategy 1 live V4 credential is unavailable")
    transport = workstation_ipv4_transport(endpoint)
    return open_live_v4_client(
        lease=lease, endpoint=endpoint, credential_user=user,
        credential_password=password,
        client_factory=lambda _endpoint, identity, secret: ClickHouseHttpClient(
            transport, identity, secret, timeout_seconds=60, persistent=True,
            default_query_params={"max_threads": 2, "max_execution_time": 60}),
    )
