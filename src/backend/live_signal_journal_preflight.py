"""Operator-only preflight for staged typed dispatch and completion tables."""
from __future__ import annotations

import json
from typing import Any

from src.backend.signal_dispatch_typed_cursor import DISPATCH_TABLES
from src.backend.live_signal_work_completion import COMPLETION


LIVE_SIGNAL_TABLES = (*DISPATCH_TABLES, COMPLETION)


def operator_ddl() -> tuple[str, ...]:
    """Review/install separately; never called by the live runtime."""
    return tuple(table.ddl() for table in LIVE_SIGNAL_TABLES)


def staged_grants(principal: str) -> tuple[str, ...]:
    if not principal.isidentifier():
        raise ValueError("live signal journal principal is invalid")
    return tuple(f"GRANT SELECT, INSERT ON arte.{table.name} TO {principal}"
                 for table in LIVE_SIGNAL_TABLES)


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def staged_live_signal_storage_preflight(client: Any) -> None:
    """Exact schema, SSD-only policy and active part placement; read-only."""
    policy = _rows(client, "SELECT disks FROM system.storage_policies "
                   "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policy) != 1 or policy[0].get("disks") != ["live_market_ssd"]:
        raise RuntimeError("staged live signal requires SSD-only live_market_ssd")
    names = ",".join(f"'{table.name}'" for table in LIVE_SIGNAL_TABLES)
    tables = _rows(client, "SELECT name,engine,storage_policy,partition_key,sorting_key "
                   "FROM system.tables WHERE database='arte' "
                   f"AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row.get("name"): row for row in tables}
    if len(tables) != len(LIVE_SIGNAL_TABLES) or len(by_name) != len(LIVE_SIGNAL_TABLES):
        raise RuntimeError("staged live signal table inventory differs")
    for contract in LIVE_SIGNAL_TABLES:
        row = by_name.get(contract.name)
        if row is None or (row.get("engine"), row.get("storage_policy"),
                           row.get("partition_key"), row.get("sorting_key")) != (
                "MergeTree", "live_market_ssd", "toYYYYMM(session_key)", contract.order):
            raise RuntimeError(f"staged live signal table layout differs: {contract.name}")
    columns = _rows(client, "SELECT table,name,type FROM system.columns "
                    "WHERE database='arte' "
                    f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for contract in LIVE_SIGNAL_TABLES:
        actual = tuple((row.get("name"), row.get("type")) for row in columns
                       if row.get("table") == contract.name)
        if actual != contract.columns:
            raise RuntimeError(f"staged live signal columns differ: {contract.name}")
    if {row.get("table") for row in columns} != {table.name for table in LIVE_SIGNAL_TABLES}:
        raise RuntimeError("staged live signal column inventory differs")
    misplaced = _rows(client, "SELECT table,disk_name FROM system.parts "
                      "WHERE active AND database='arte' "
                      f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                      "LIMIT 1 FORMAT JSONEachRow")
    if misplaced:
        raise RuntimeError("staged live signal parts are outside live_market_ssd")
