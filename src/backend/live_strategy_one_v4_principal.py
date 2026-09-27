"""Inactive dedicated Strategy 1 Live V4 journal principal and client factory.

This is not an order-admission gate. A caller must hold a separate exclusive
Keeper lease, and complete live cold recovery remains unimplemented. No DDL,
grant, credential, or ClickHouse row operation is performed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import platform
from typing import Any, Callable

from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, V4_COMMIT_TABLES, journal_permission_preflight,
    storage_preflight,
)
from src.trading_runtime.arte_journal_writer import (
    _FAMILIES, _v4_family_table, v4_storage_contracts,
)
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE,
)
from src.backend.live_strategy_one_approval import TABLE as APPROVAL
from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


PRINCIPAL = "strategy_one_live_v4_runner"
MANAGED_URL = "http://DESKTOP-SAAI85T:18123"
_CONFIG_READ = frozenset({NODE_TABLE.split(".", 1)[1],
                          RELEASE_TABLE.split(".", 1)[1], APPROVAL.name})
_EXCLUDED_FAMILY = frozenset({
    "trading_backtest_cursor_v1", "trading_backtest_market_authority_v1",
    "trading_backtest_progress_v1", "trading_prepared_v7_lease_v1",
})
_V4_LIVE_DETAIL = frozenset({
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


def desired_plan() -> LiveV4PrincipalPlan:
    family = frozenset(_v4_family_table(name) for name, _, _, _ in _FAMILIES
                       if name not in _EXCLUDED_FAMILY)
    writable = (family | frozenset(table.name for table in V4_COMMIT_TABLES)
                | _V4_LIVE_DETAIL | _PORTFOLIO_WRITE | _CONTEXT_WRITE)
    contracts = {table.name for table in v4_storage_contracts()}
    if not writable | _POLICY_READ <= contracts:
        raise RuntimeError("Live V4 principal references an unmodeled table")
    if any("backtest" in name for name in writable):
        raise RuntimeError("Live V4 principal would write a Backtest table")
    return LiveV4PrincipalPlan(
        PRINCIPAL, writable | _POLICY_READ | _CONFIG_READ | MARKET_READ_TABLES,
        writable,
        frozenset({"storage_policies", "tables", "columns", "parts",
                   "data_skipping_indices"}),
        frozenset({("q_live", "market_stock_split_v1")}),
    )


def live_v4_preflight(client: Any) -> None:
    """Verify exact live grants and SSD layout without a row mutation."""
    plan = desired_plan()
    contracts = {table.name: table for table in v4_storage_contracts()}
    names = plan.insert_arte | _POLICY_READ
    storage_preflight(client, tables=tuple(contracts[name] for name in sorted(names)))
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
    client = client_factory(endpoint, credential_user, credential_password)
    try:
        live_v4_preflight(client)
        lease.assert_current()
        from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
        client.typed_insert_dispatch = TypedInsertDispatch(lease.owner._session.client)
        client.typed_insert_strict = True
        client.live_v4_lease = lease
        return client
    except BaseException:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        raise
