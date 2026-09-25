"""Operator-owned, restart-safe layout for typed market-day certificates.

Dry run by default. Even --apply creates only absent normalized tables; this
command never publishes certificate rows or changes Backtest permissions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_journal_schema import STORAGE_POLICY, storage_preflight
from src.trading_runtime.arte_market_day_certification import TABLES


def _rows(client, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def install_layout(client, *, apply: bool) -> tuple[int, int]:
    """Return (verified existing, newly created), refusing incompatible state."""
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow")
    if policies != [{"disks": [STORAGE_POLICY]}]:
        raise RuntimeError("Market-day certificate requires SSD-only live_market_ssd")
    if client.execute("SELECT count() FROM system.databases WHERE name='arte'").strip() != "1":
        raise RuntimeError("Existing arte database is required")
    names = ",".join(f"'{table.name}'" for table in TABLES)
    actual = _rows(client, "SELECT name FROM system.tables WHERE database='arte' "
                   f"AND name IN ({names}) FORMAT JSONEachRow")
    present = {str(row.get("name")) for row in actual}
    expected = {table.name for table in TABLES}
    if len(actual) != len(present) or not present <= expected:
        raise RuntimeError("Market-day certificate catalog is ambiguous")
    installed = tuple(table for table in TABLES if table.name in present)
    missing = tuple(table for table in TABLES if table.name not in present)
    if installed:
        storage_preflight(client, tables=installed)
    print(f"Typed market-day certificate: {len(installed)} verified, {len(missing)} absent")
    if not apply:
        print("Plan only; 0 tables created, 0 rows inserted")
        return len(installed), 0
    created = 0
    for table in missing:
        client.execute(table.ddl())
        storage_preflight(client, tables=(table,))
        created += 1
        print(f"Created and verified {created}/{len(missing)}: arte.{table.name}", flush=True)
    storage_preflight(client, tables=TABLES)
    print(f"Complete: {len(TABLES)} verified, {created} created, 0 rows inserted")
    return len(installed), created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://DESKTOP-SAAI85T:18123")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-market-certificate-tables", action="store_true")
    args = parser.parse_args(argv)
    parsed = urlsplit(args.url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                parsed.query, parsed.fragment, parsed.username, parsed.password)
            != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
        parser.error("Run on DESKTOP-SAAI85T against its managed ClickHouse endpoint")
    if args.apply and not args.confirm_market_certificate_tables:
        parser.error("--apply requires --confirm-market-certificate-tables")
    try:
        client = _admin_client(args.url)
        try:
            install_layout(client, apply=args.apply)
        finally:
            client.close()
    except KeyboardInterrupt:
        print("Interrupted; verified tables remain. Rerun to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Market-day certificate layout blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
