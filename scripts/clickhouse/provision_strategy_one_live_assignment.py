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
from src.trading_runtime.arte_journal_schema import storage_preflight


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def apply(client: Any) -> bool:
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
    if created:
        client.execute(TABLE.ddl())
    storage_preflight(client, tables=(TABLE,))
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        print(f"Plan: arte.{TABLE.name}; live_market_ssd; no data migration")
        return 0
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        raise RuntimeError("Strategy 1 assignment DDL requires the managed workstation")
    client = _admin_client(f"http://{WORKSTATION_IPV4}:18123")
    try:
        created = apply(client)
    finally:
        client.close()
    print(f"arte.{TABLE.name}: {'created' if created else 'already present'}; "
          "SSD schema and part placement verified; 0 rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
