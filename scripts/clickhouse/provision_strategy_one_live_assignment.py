"""Install the normalized Strategy 1 assignment fact table, without rows.

Operator-only DDL. Dry-run by default; Backtest and the live runner have no
CREATE or INSERT authority on this table. No old assignment data is migrated.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_trading_journal import _admin_client, SECRET_ROOT
from scripts.clickhouse.provision_fixed_backtest_v3_principals import WORKSTATION_IPV4
from src.backend.live_strategy_one_assignment import TABLE
from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight


_EMPTY_PREPUBLICATION = TableContract(
    TABLE.name,
    tuple(column for column in TABLE.columns if column[0] != "publication_id"),
    TABLE.partition,
    "configuration_revision_id, session_date, run_plan_id, assignment_id, revision",
)


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def apply(client: Any, *, replace_empty_v1: bool = False) -> bool:
    """Create once after SSD policy proof; verify exact schema and placement."""
    policy = _rows(client,
        "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if policy != [{"disks": ["live_market_ssd"]}]:
        raise RuntimeError("Strategy 1 assignment requires SSD-only live_market_ssd")
    existing = _rows(client,
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name='{TABLE.name}' FORMAT JSONEachRow")
    if existing not in ([], [{"name": TABLE.name}]):
        raise RuntimeError("Strategy 1 assignment table inventory is ambiguous")
    created = not existing
    if existing:
        columns = _rows(client,
            "SELECT name,type FROM system.columns WHERE database='arte' "
            f"AND table='{TABLE.name}' ORDER BY position FORMAT JSONEachRow")
        shape = tuple((row.get("name"), row.get("type")) for row in columns)
        if shape == TABLE.columns:
            storage_preflight(client, tables=(TABLE,))
            return False
        if not replace_empty_v1 or shape != _EMPTY_PREPUBLICATION.columns:
            raise RuntimeError("Strategy 1 assignment layout differs; no automatic replacement")
        storage_preflight(client, tables=(_EMPTY_PREPUBLICATION,))
        count = client.execute(
            f"SELECT count() FROM arte.{TABLE.name} FORMAT TabSeparated").strip()
        parts = client.execute(
            "SELECT count() FROM system.parts WHERE active AND database='arte' "
            f"AND table='{TABLE.name}' FORMAT TabSeparated").strip()
        if count != "0" or parts != "0":
            raise RuntimeError("Strategy 1 assignment has rows or parts; refusing replacement")
        client.execute(f"DROP TABLE arte.{TABLE.name} SYNC")
        created = True
    if created:
        client.execute(TABLE.ddl())
    storage_preflight(client, tables=(TABLE,))
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace-empty-v1", action="store_true",
                        help="replace only the verified empty prepublication layout")
    args = parser.parse_args(argv)
    if args.replace_empty_v1 and not args.apply:
        parser.error("--replace-empty-v1 requires --apply")
    if not args.apply:
        print(f"Plan: arte.{TABLE.name}; live_market_ssd; no data migration")
        return 0
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        raise RuntimeError("Strategy 1 assignment DDL requires the managed workstation")
    client = _admin_client(f"http://{WORKSTATION_IPV4}:18123")
    try:
        created = apply(client, replace_empty_v1=args.replace_empty_v1)
    finally:
        client.close()
    print(f"arte.{TABLE.name}: {'created' if created else 'already present'}; "
          "SSD schema and part placement verified; 0 rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
