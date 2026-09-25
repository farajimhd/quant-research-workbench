"""Operator-only staged definition DDL/grants and read-only placement preflight."""
from __future__ import annotations

import json
from typing import Any

from src.backend.live_strategy_definition_authority import TABLES


def operator_ddl() -> tuple[str, ...]:
    return tuple(table.ddl() for table in TABLES)


def staged_grants(principal: str) -> tuple[str, ...]:
    if type(principal) is not str or not principal.isidentifier():
        raise ValueError("definition journal principal is invalid")
    return tuple(f"GRANT SELECT, INSERT ON arte.{table.name} TO {principal}"
                 for table in TABLES)


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def staged_definition_storage_preflight(client: Any) -> None:
    """Fail on missing schema, default-disk fallback, or misplaced active parts."""
    policy = _rows(client, "SELECT disks FROM system.storage_policies "
                   "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policy) != 1 or policy[0].get("disks") != ["live_market_ssd"]:
        raise RuntimeError("definition tables require SSD-only live_market_ssd")
    names = ",".join(f"'{table.name}'" for table in TABLES)
    tables = _rows(client, "SELECT name,engine,storage_policy,partition_key,sorting_key "
                   "FROM system.tables WHERE database='arte' "
                   f"AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row.get("name"): row for row in tables}
    if len(tables) != len(TABLES) or len(by_name) != len(TABLES):
        raise RuntimeError("definition table inventory differs")
    for contract in TABLES:
        row = by_name.get(contract.name)
        if row is None or (row.get("engine"), row.get("storage_policy"),
                           row.get("partition_key"), row.get("sorting_key")) != (
                "MergeTree", "live_market_ssd", contract.partition, contract.order):
            raise RuntimeError(f"definition table layout differs: {contract.name}")
    columns = _rows(client, "SELECT table,name,type FROM system.columns "
                    "WHERE database='arte' "
                    f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for contract in TABLES:
        actual = tuple((row.get("name"), row.get("type")) for row in columns
                       if row.get("table") == contract.name)
        if actual != contract.columns:
            raise RuntimeError(f"definition table columns differ: {contract.name}")
    if {row.get("table") for row in columns} != {table.name for table in TABLES}:
        raise RuntimeError("definition column inventory differs")
    misplaced = _rows(client, "SELECT table,disk_name FROM system.parts "
                      "WHERE active AND database='arte' "
                      f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                      "LIMIT 1 FORMAT JSONEachRow")
    if misplaced:
        raise RuntimeError("definition active parts are outside live_market_ssd")
