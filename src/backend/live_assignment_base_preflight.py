"""Operator-only installation plan and exact layout checks for staged base revisions.

No live route imports this module. In particular, a successful preflight is not
an assignment publication fence or permission to trade.
"""
from __future__ import annotations

import json
from typing import Any

from src.backend.live_assignment_base_revision import BASE_REVISION
from src.backend.live_assignment_state_snapshot import STATE_TABLES
from src.trading_runtime.arte_long_momentum_parameter_journal import PARAMETER_TABLES


def operator_ddl() -> tuple[str, ...]:
    return (BASE_REVISION.ddl(),)


def staged_state_operator_ddl() -> tuple[str, ...]:
    return tuple(table.ddl() for table in STATE_TABLES)


ASSIGNMENT_CHILD_TABLES = PARAMETER_TABLES + STATE_TABLES
if len({table.name for table in ASSIGNMENT_CHILD_TABLES}) != len(ASSIGNMENT_CHILD_TABLES):
    raise AssertionError("assignment child table names must be unique")


def staged_assignment_child_operator_ddl() -> tuple[str, ...]:
    return tuple(table.ddl() for table in ASSIGNMENT_CHILD_TABLES)


def staged_state_grants(*, reader_principal: str, writer_principal: str) -> tuple[str, ...]:
    return _child_grants(STATE_TABLES, reader_principal=reader_principal,
                         writer_principal=writer_principal)


def staged_assignment_child_grants(*, reader_principal: str,
                                   writer_principal: str) -> tuple[str, ...]:
    return _child_grants(ASSIGNMENT_CHILD_TABLES, reader_principal=reader_principal,
                         writer_principal=writer_principal)


def _child_grants(tables: tuple[Any, ...], *, reader_principal: str,
                  writer_principal: str) -> tuple[str, ...]:
    for principal in (reader_principal, writer_principal):
        if type(principal) is not str or not principal.isidentifier():
            raise ValueError("assignment state principal is invalid")
    if reader_principal == writer_principal:
        raise ValueError("assignment state reader and writer principals must differ")
    return tuple(
        statement for table in tables for statement in (
            f"GRANT SELECT ON arte.{table.name} TO {reader_principal}",
            f"GRANT SELECT, INSERT ON arte.{table.name} TO {writer_principal}",
        )
    )


def staged_grants(*, reader_principal: str, writer_principal: str) -> tuple[str, ...]:
    for principal in (reader_principal, writer_principal):
        if type(principal) is not str or not principal.isidentifier():
            raise ValueError("assignment journal principal is invalid")
    if reader_principal == writer_principal:
        raise ValueError("assignment reader and writer principals must be distinct")
    return (
        f"GRANT SELECT ON arte.{BASE_REVISION.name} TO {reader_principal}",
        f"GRANT SELECT, INSERT ON arte.{BASE_REVISION.name} TO {writer_principal}",
    )


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def staged_base_revision_preflight(client: Any) -> None:
    """Fail closed on schema, placement, or policy mismatch (read-only)."""
    policy = _rows(client, "SELECT disks FROM system.storage_policies "
                   "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policy) != 1 or policy[0].get("disks") != ["live_market_ssd"]:
        raise RuntimeError("assignment base requires SSD-only live_market_ssd")
    name = BASE_REVISION.name
    tables = _rows(client, "SELECT name,engine,storage_policy,partition_key,sorting_key "
                   "FROM system.tables WHERE database='arte' "
                   f"AND name='{name}' FORMAT JSONEachRow")
    if len(tables) != 1 or (tables[0].get("name"), tables[0].get("engine"),
                            tables[0].get("storage_policy"),
                            tables[0].get("partition_key"),
                            tables[0].get("sorting_key")) != (
            name, "MergeTree", "live_market_ssd", BASE_REVISION.partition,
            BASE_REVISION.order):
        raise RuntimeError("assignment base table layout differs")
    columns = _rows(client, "SELECT name,type FROM system.columns "
                    "WHERE database='arte' "
                    f"AND table='{name}' ORDER BY position FORMAT JSONEachRow")
    if tuple((row.get("name"), row.get("type")) for row in columns) != BASE_REVISION.columns:
        raise RuntimeError("assignment base table columns differ")
    parts = _rows(client, "SELECT disk_name FROM system.parts "
                  "WHERE active AND database='arte' "
                  f"AND table='{name}' AND disk_name!='live_market_ssd' "
                  "LIMIT 1 FORMAT JSONEachRow")
    if parts:
        raise RuntimeError("assignment base active parts are outside live_market_ssd")


def staged_state_storage_preflight(client: Any) -> None:
    """Read-only exact state-family inventory, including actual part placement."""
    _child_storage_preflight(client, STATE_TABLES)


def staged_assignment_child_storage_preflight(client: Any) -> None:
    """Read-only exact state and parameter inventory for staged cold join."""
    _child_storage_preflight(client, ASSIGNMENT_CHILD_TABLES)


def _child_storage_preflight(client: Any, tables_to_check: tuple[Any, ...]) -> None:
    policy = _rows(client, "SELECT disks FROM system.storage_policies "
                   "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policy) != 1 or policy[0].get("disks") != ["live_market_ssd"]:
        raise RuntimeError("assignment state requires SSD-only live_market_ssd")
    names = ",".join(f"'{table.name}'" for table in tables_to_check)
    actual = _rows(client, "SELECT name,engine,storage_policy,partition_key,sorting_key "
                   "FROM system.tables WHERE database='arte' "
                   f"AND name IN ({names}) FORMAT JSONEachRow")
    if len(actual) != len(tables_to_check) or {row.get("name") for row in actual} != {
            table.name for table in tables_to_check}:
        raise RuntimeError("assignment state table inventory differs")
    by_name = {row["name"]: row for row in actual}
    for table in tables_to_check:
        row = by_name[table.name]
        if (row.get("engine"), row.get("storage_policy"), row.get("partition_key"),
                row.get("sorting_key")) != (
                "MergeTree", "live_market_ssd", table.partition, table.order):
            raise RuntimeError(f"assignment state table layout differs: {table.name}")
    columns = _rows(client, "SELECT table,name,type FROM system.columns "
                    "WHERE database='arte' "
                    f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for table in tables_to_check:
        if tuple((row.get("name"), row.get("type")) for row in columns
                 if row.get("table") == table.name) != table.columns:
            raise RuntimeError(f"assignment state table columns differ: {table.name}")
    if {row.get("table") for row in columns} != {table.name for table in tables_to_check}:
        raise RuntimeError("assignment state column inventory differs")
    parts = _rows(client, "SELECT table,disk_name FROM system.parts "
                  "WHERE active AND database='arte' "
                  f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                  "LIMIT 1 FORMAT JSONEachRow")
    if parts:
        raise RuntimeError("assignment state active parts are outside live_market_ssd")
