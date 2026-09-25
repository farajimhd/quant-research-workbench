"""Read-only operator admission for staged Backtest terminal V2 tables.

This module neither installs schema nor grants privileges. It is deliberately
not called by fixed Backtest launch or the active journal writer.
"""
from __future__ import annotations

import json
import re
from typing import Any

from src.trading_runtime.arte_journal_schema import (
    BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES, BATCH_LOOKUP_INDEX,
    MARKET_READ_TABLES, STORAGE_POLICY, TABLES,
)


_V2 = {table.name for table in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES}
_V1 = {table.name for table in TABLES}
_READ = _V1 | _V2
_PORTFOLIO_INSERT = {
    "trading_backtest_snapshot_anchor_v1",
    "trading_portfolio_snapshot_v1", "trading_portfolio_disabled_strategy_v1",
    "trading_portfolio_command_v1", "trading_portfolio_request_v1",
    "trading_portfolio_request_reason_v1", "trading_portfolio_reservation_v1",
    "trading_portfolio_allocation_v1", "trading_portfolio_reconciliation_v1",
    "trading_portfolio_snapshot_commit_v1", "trading_portfolio_policy_v1",
    "trading_portfolio_policy_commit_v2",
    "trading_portfolio_policy_security_type_v1",
    "trading_portfolio_policy_currency_v1",
    "trading_portfolio_policy_restricted_symbol_v1",
    "trading_portfolio_policy_execution_policy_v1",
    "trading_portfolio_policy_protection_profile_v1",
}
_INSERT = _V2 | _PORTFOLIO_INSERT | {
    "trading_event_v1", "trading_run_transition_v1"}
if not _INSERT <= _READ:
    raise RuntimeError("Terminal V2 writer references an unprovisioned typed table")
_SYSTEM_READ = {
    "storage_policies", "tables", "columns", "parts",
    "data_skipping_indices",
}
_GRANT = re.compile(
    r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*|\*)\."
    r"([A-Za-z_][A-Za-z0-9_]*|\*) TO ([A-Za-z_][A-Za-z0-9_]*)"
)


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines()
            if line.strip()]


def terminal_v2_storage_preflight(client: Any) -> None:
    """Verify exact staged layout and actual active-part SSD placement."""
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
                     "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Terminal V2 requires SSD-only live_market_ssd policy")
    names = ",".join(f"'{name}'" for name in sorted(_V2))
    tables = _rows(client,
        "SELECT name,engine,storage_policy,partition_key,sorting_key FROM system.tables "
        f"WHERE database='arte' AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row.get("name"): row for row in tables}
    if len(tables) != len(_V2) or set(by_name) != _V2:
        raise RuntimeError("Terminal V2 tables are missing or duplicated")
    for contract in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES:
        row = by_name[contract.name]
        if (row.get("engine"), row.get("storage_policy"),
                row.get("partition_key"), row.get("sorting_key")) != (
                    "MergeTree", STORAGE_POLICY, contract.partition, contract.order):
            raise RuntimeError(f"Terminal V2 layout differs: {contract.name}")
    columns = _rows(client,
        "SELECT table,name,type FROM system.columns WHERE database='arte' "
        f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for contract in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES:
        actual = tuple((row.get("name"), row.get("type")) for row in columns
                       if row.get("table") == contract.name)
        if actual != contract.columns:
            raise RuntimeError(f"Terminal V2 columns differ: {contract.name}")
    if {row.get("table") for row in columns} != _V2:
        raise RuntimeError("Terminal V2 column catalog has a missing or extra table")
    indexes = _rows(client,
        "SELECT table,name,type,expr,granularity FROM system.data_skipping_indices "
        f"WHERE database='arte' AND table IN ({names}) FORMAT JSONEachRow")
    if (len(indexes) != len(_V2)
            or {row.get("table") for row in indexes} != _V2
            or any((row.get("name"), row.get("type"), row.get("expr"),
                    int(row.get("granularity") or 0))
                   != (BATCH_LOOKUP_INDEX, "bloom_filter", "batch_id", 1)
                   for row in indexes)):
        raise RuntimeError("Terminal V2 batch indexes differ")
    bad_parts = _rows(client,
        "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
        "LIMIT 1 FORMAT JSONEachRow")
    if bad_parts:
        raise RuntimeError("Terminal V2 has active parts outside live_market_ssd")
    missing_indexes = _rows(client,
        "SELECT table,name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({names}) AND secondary_indices_compressed_bytes=0 "
        "LIMIT 1 FORMAT JSONEachRow")
    if missing_indexes:
        raise RuntimeError("Terminal V2 has active parts without materialized indexes")


def terminal_v2_permission_preflight(client: Any) -> None:
    """Require a narrow append/read journal principal and deny market writes."""
    names = ",".join(f"'{name}'" for name in sorted(_READ | MARKET_READ_TABLES))
    actual = _rows(client, "SELECT name FROM system.tables WHERE database='arte' "
                   f"AND name IN ({names}) FORMAT JSONEachRow")
    if {row.get("name") for row in actual} != _READ | MARKET_READ_TABLES:
        raise RuntimeError("Terminal V2 grant audit lacks required catalog tables")
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise RuntimeError("Terminal V2 principal identity is unsafe")
    grants = client.execute("SHOW GRANTS").splitlines()
    if not grants:
        raise RuntimeError("Terminal V2 principal grants cannot be inspected")
    for line in grants:
        match = _GRANT.fullmatch(line.strip())
        if match is None or match.group(4) != user:
            raise RuntimeError("Terminal V2 principal has an unknown or delegated grant")
        privileges = {item.strip() for item in match.group(1).split(",")}
        database, table = match.group(2), match.group(3)
        if not privileges or "" in privileges:
            raise RuntimeError("Terminal V2 principal has an invalid grant")
        for privilege in privileges:
            if (privilege == "SELECT"
                    and ((database == "arte" and table in _READ | MARKET_READ_TABLES)
                         or (database == "system" and table in _SYSTEM_READ))):
                continue
            if privilege == "INSERT" and database == "arte" and table in _INSERT:
                continue
            if privilege in {"SHOW DATABASES", "SHOW TABLES", "SHOW COLUMNS", "CHECK"}:
                continue
            raise RuntimeError(f"Terminal V2 principal has unauthorized {privilege} grant")

    def allowed(privilege: str, scope: str) -> bool:
        result = client.execute(f"CHECK GRANT {privilege} ON {scope}").strip()
        if result not in {"0", "1"}:
            raise RuntimeError("Terminal V2 grant check returned an invalid result")
        return result == "1"

    for scope in ("*.*", "arte.*", "market_sip_compact.*", "q_live.*"):
        for privilege in ("INSERT", "ALTER", "DROP TABLE", "TRUNCATE"):
            if allowed(privilege, scope):
                raise RuntimeError(f"Terminal V2 principal has broad {scope} {privilege}")
    for name in sorted(_READ | MARKET_READ_TABLES):
        scope = f"arte.{name}"
        if allowed("INSERT", scope) != (name in _INSERT):
            raise RuntimeError(f"Terminal V2 principal has incorrect INSERT on {scope}")
        if name in _READ and not allowed("SELECT", scope):
            raise RuntimeError(f"Terminal V2 principal cannot SELECT {scope}")
        if name in MARKET_READ_TABLES and not allowed("SELECT", scope):
            raise RuntimeError(f"Terminal V2 principal cannot SELECT {scope}")
        for privilege in ("ALTER", "ALTER DELETE", "ALTER UPDATE",
                          "DROP TABLE", "TRUNCATE"):
            if allowed(privilege, scope):
                raise RuntimeError(f"Terminal V2 principal can mutate {scope}")


def terminal_v2_operator_preflight(client: Any) -> None:
    """Read-only admission; use a dedicated principal, never market writer."""
    terminal_v2_storage_preflight(client)
    terminal_v2_permission_preflight(client)


def terminal_v2_keeper_proof_preflight(keeper: Any) -> None:
    """Read-only check that the persistent proof namespace is accessible.

    This cannot prove Keeper CREATE/CAS ACL without performing a mutation;
    the fixed launch gate remains closed until an operator verifies that ACL.
    """
    from src.trading_runtime.keeper_ownership import (
        KeeperOwnershipCoordinator, _BACKTEST_TERMINAL_RECEIPTS, _ROOT,
    )
    if not isinstance(keeper, KeeperOwnershipCoordinator):
        raise TypeError("Terminal V2 needs the Keeper ownership coordinator")
    keeper._require_connected()
    for path in (f"{_ROOT}/portfolio", _BACKTEST_TERMINAL_RECEIPTS):
        if keeper._client.exists(path) is None:
            raise RuntimeError(f"Terminal V2 Keeper proof namespace is absent: {path}")
    keeper._require_connected()


def terminal_v2_operator_provisioning_sql(principal: str) -> tuple[str, ...]:
    """Review-only staged DDL/grants; never executed by this module."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", principal) is None:
        raise ValueError("Terminal V2 principal name is unsafe")
    return (
        *(table.ddl() for table in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES),
        *(f"GRANT SELECT ON arte.{name} TO {principal}" for name in sorted(
            _READ | MARKET_READ_TABLES)),
        *(f"GRANT INSERT ON arte.{name} TO {principal}" for name in sorted(_INSERT)),
        *(f"GRANT SELECT ON system.{name} TO {principal}" for name in sorted(_SYSTEM_READ)),
    )
