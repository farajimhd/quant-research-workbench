"""Read-only, distinct V3 running and terminal principal admission."""
from __future__ import annotations

import re
from typing import Any

from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
    PROTECTED_EXIT_SATISFIED, PROTECTION_CHANGE_TABLES,
    PROTECTED_EXIT_SNAPSHOT,
    PORTFOLIO_ALLOCATION_FILL,
    PORTFOLIO_CONTROL,
    RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
    SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE,
)
from src.backend.backtest_terminal_v3_fence import TERMINAL_COMMIT_V3
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.trading_runtime.arte_market_day_certification import TABLES as MARKET_DAY_CERTIFICATE_TABLES
from src.trading_runtime.arte_journal_schema import (
    POLICY_ALLOWED_TABLES, TABLES, fixed_backtest_v2_contracts, journal_permission_preflight,
    storage_preflight, versioned_journal_v2_contracts,
)


def policy_catalog_v3_contracts() -> tuple[Any, ...]:
    """Exact immutable catalog facts and fence needed by V3 selections."""
    names = {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}
    names.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
    contracts = tuple(table for table in TABLES if table.name in names)
    if {table.name for table in contracts} != names:
        raise RuntimeError("V3 selected policy catalog contract is incomplete")
    return contracts


def running_v3_contracts() -> tuple[Any, ...]:
    return versioned_journal_v2_contracts() + (
        SQUEEZE_EPISODE, RESERVATION_REASON,
        RECONCILIATION_DIFFERENCE, PORTFOLIO_CONTROL,
        *TRADE_PROPOSAL_TABLES, *BROKER_OMS_TABLES,
        *ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
        PROTECTED_EXIT_SATISFIED, *PROTECTION_CHANGE_TABLES,
        PROTECTED_EXIT_SNAPSHOT,
        PORTFOLIO_ALLOCATION_FILL,
        SQUEEZE_COMMIT_V3)


def terminal_v3_contracts() -> tuple[Any, ...]:
    return fixed_backtest_v2_contracts() + (
        SQUEEZE_EPISODE, RESERVATION_REASON,
        RECONCILIATION_DIFFERENCE, PORTFOLIO_CONTROL,
        *TRADE_PROPOSAL_TABLES, *BROKER_OMS_TABLES,
        *ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
        PROTECTED_EXIT_SATISFIED, *PROTECTION_CHANGE_TABLES,
        PROTECTED_EXIT_SNAPSHOT,
        PORTFOLIO_ALLOCATION_FILL,
        SQUEEZE_COMMIT_V3,
        TERMINAL_COMMIT_V3)


def _exact_grants(client: Any, writable: frozenset[str]) -> None:
    """Reject an effective extra INSERT, including staged live-signal grants."""
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise RuntimeError("V3 journal principal is invalid")
    grants = client.execute("SHOW GRANTS FINAL").splitlines()
    if not grants:
        raise RuntimeError("V3 journal grants are unavailable")
    for line in grants:
        match = re.fullmatch(
            r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*|\*)\."
            r"([A-Za-z_][A-Za-z0-9_]*|\*) TO ([A-Za-z_][A-Za-z0-9_]*)",
            line.strip())
        if match is None or match.group(4) != user:
            raise RuntimeError("V3 journal principal has unknown effective grant")
        if "INSERT" in {part.strip() for part in match.group(1).split(",")}:
            if match.group(2) != "arte" or match.group(3) not in writable:
                raise RuntimeError("V3 journal principal has extra INSERT authority")


def running_v3_preflight(client: Any) -> None:
    """Only normal event/detail/child families and V3 running commit are writable."""
    from src.trading_runtime.arte_journal_writer import _FAMILIES, _profile_table

    contracts = running_v3_contracts()
    writable = frozenset(_profile_table(table, "backtest_v3")
                         for table, _, _, _ in _FAMILIES) | frozenset({
                             SQUEEZE_EPISODE.name, RESERVATION_REASON.name,
                             RECONCILIATION_DIFFERENCE.name,
                             PORTFOLIO_CONTROL.name,
                             SQUEEZE_COMMIT_V3.name}) | frozenset(
                                 table.name for table in policy_catalog_v3_contracts()) | frozenset(
                                     table.name for table in (
                                         *TRADE_PROPOSAL_TABLES, *BROKER_OMS_TABLES,
                                         *ENTRY_REPRICE_CAPACITY_TABLES,
                                         ENTRY_REPRICE_REJECTED,
                                         PROTECTED_EXIT_SATISFIED,
                                         *PROTECTION_CHANGE_TABLES,
                                         PROTECTED_EXIT_SNAPSHOT,
                                         PORTFOLIO_ALLOCATION_FILL))
    available = {table.name for table in contracts}
    if not writable <= available:
        raise RuntimeError("V3 running writer references an unprovisioned family")
    storage_preflight(client, tables=contracts)
    journal_permission_preflight(
        client, journal_tables=writable,
        read_only_tables=frozenset(available - writable))
    _exact_grants(client, writable)


def terminal_v3_preflight(client: Any) -> None:
    """Only terminal facts, portfolio captures, anchors and V3 seal writable."""
    from src.backend.backtest_terminal_v3_dispatch import _TABLES

    contracts = terminal_v3_contracts()
    available = {table.name for table in contracts}
    writable = frozenset(_TABLES)
    if not writable <= available:
        raise RuntimeError("V3 terminal writer references an unprovisioned family")
    storage_preflight(client, tables=contracts)
    journal_permission_preflight(
        client, journal_tables=writable,
        read_only_tables=frozenset(available - writable))
    _exact_grants(client, writable)


def read_v3_preflight(client: Any) -> None:
    """A separate cold-audit principal must have no arte INSERT grant."""
    contracts = terminal_v3_contracts()
    certificate_names = frozenset(table.name for table in MARKET_DAY_CERTIFICATE_TABLES)
    storage_preflight(client, tables=contracts + MARKET_DAY_CERTIFICATE_TABLES)
    journal_permission_preflight(
        client, journal_tables=frozenset(),
        read_only_tables=frozenset(table.name for table in contracts) | certificate_names,
        reference_read_tables=frozenset({("q_live", "market_stock_split_v1")}))
    _exact_grants(client, frozenset())


def terminal_v3_keeper_namespace_preflight(keeper: Any) -> None:
    """Read-only namespace check; CREATE/CAS ACL still needs operator proof."""
    from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator, _ROOT

    if not isinstance(keeper, KeeperOwnershipCoordinator):
        raise TypeError("V3 terminal needs Keeper ownership coordinator")
    keeper._require_connected()
    for suffix in ("portfolio", "typed_dispatch_gate",
                   "backtest_terminal_v3", "backtest_terminal_v3_operations"):
        if keeper._client.exists(f"{_ROOT}/{suffix}") is None:
            raise RuntimeError(f"V3 Keeper namespace is absent: {suffix}")
    keeper._require_connected()
