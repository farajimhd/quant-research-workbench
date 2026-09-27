"""Install the normalized producer-owned market-day session-seal table.

Dry-run by default. This operator command creates no market bars, indicators,
liquidity, or Backtest journal rows and grants no Backtest write permission.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.install_market_day_certificate_layout import (
    workstation_clickhouse_url,
)
from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_market_day_session_seal import SESSION_SEAL


def install(client, *, apply: bool) -> str:
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name='{SESSION_SEAL.name}' FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    if len(rows) > 1 or any(row != {"name": SESSION_SEAL.name} for row in rows):
        raise RuntimeError("Session-seal table catalogue is ambiguous")
    if rows:
        storage_preflight(client, tables=(SESSION_SEAL,))
        return "verified_existing"
    storage_preflight_policy = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    if storage_preflight_policy != [{"disks": ["live_market_ssd"]}]:
        raise RuntimeError("Session seal requires SSD-only live_market_ssd")
    if not apply:
        return "absent_plan_only"
    client.execute(SESSION_SEAL.ddl())
    storage_preflight(client, tables=(SESSION_SEAL,))
    return "created_verified"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-market-day-session-seal", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_market_day_session_seal:
        parser.error("--apply requires --confirm-market-day-session-seal")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Session-seal installation blocked: managed workstation required.",
              file=sys.stderr)
        return 1
    try:
        with closing(_admin_client(workstation_clickhouse_url())) as client:
            result = install(client, apply=args.apply)
    except KeyboardInterrupt:
        print("Interrupted; inspect the table and rerun to verify.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Session-seal installation blocked: {type(exc).__name__}.",
              file=sys.stderr)
        return 1
    print(f"arte.{SESSION_SEAL.name}: {result}; "
          f"tables_created={int(result == 'created_verified')}; rows_inserted=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
