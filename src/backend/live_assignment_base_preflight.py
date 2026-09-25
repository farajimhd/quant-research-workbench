"""Operator-only installation plan and exact layout checks for staged base revisions.

No live route imports this module. In particular, a successful preflight is not
an assignment publication fence or permission to trade.
"""
from __future__ import annotations

import json
from typing import Any

from src.backend.live_assignment_base_revision import BASE_REVISION


def operator_ddl() -> tuple[str, ...]:
    return (BASE_REVISION.ddl(),)


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
