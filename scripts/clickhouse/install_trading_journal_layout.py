"""Install normalized ARTE journal DDL; never insert rows.

The default is a read-only plan. An interrupted --apply is restart-safe: each
installed table is checked against its exact contract before work continues.
The journal writer principal has no CREATE privilege; this is operator-owned.
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

from scripts.clickhouse.plan_trading_journal_layout import plan_missing, profile_contracts
from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_journal_schema import (
    STORAGE_POLICY, TableContract, fixed_backtest_v2_contracts, storage_preflight,
)
from src.backend.backtest_squeeze_episode_schema import (
    RESERVATION_REASON, SQUEEZE_COMMIT_V3, staged_reservation_reason_ddl,
)


_REASON_COLUMNS = frozenset({"portfolio_reservation_reason_count",
                             "portfolio_reservation_reason_hash"})


def upgrade_v3_reservation_reason(client: object, *, apply: bool) -> str:
    """Restart-safe operator DDL; no row INSERT and no occupied fence rewrite."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = SQUEEZE_COMMIT_V3.columns
    old = tuple(column for column in full if column[0] not in _REASON_COLUMNS)
    partial = tuple(column for column in full
                    if column[0] != "portfolio_reservation_reason_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown schema; no ALTER attempted")
    shape = TableContract(SQUEEZE_COMMIT_V3.name, actual,
                          SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(shape,))
    child_count = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_reservation_reason_v1'"
    ).strip()
    if child_count not in {"0", "1"}:
        raise RuntimeError("Reservation reason table catalog is ambiguous")
    child_present = child_count == "1"
    if child_present:
        storage_preflight(client, tables=(RESERVATION_REASON,))
    if actual == full and child_present:
        print("V3 reservation reasons: schema and SSD placement verified; no change")
        return "verified"
    fence_rows = client.execute("SELECT count() FROM arte.trading_commit_v3").strip()
    if fence_rows != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if child_present and client.execute(
            "SELECT count() FROM arte.trading_portfolio_reservation_reason_v1"
    ).strip() != "0":
        raise RuntimeError("Reservation reason child has rows; no ALTER attempted")
    pending = ["child table"] if not child_present else []
    if actual == old:
        pending.append("V3 reason count and hash")
    elif actual == partial:
        pending.append("V3 reason hash")
    print("V3 reservation reasons: empty fence verified; pending " + ", ".join(pending))
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return "planned"
    child_ddl, count_ddl, hash_ddl = staged_reservation_reason_ddl()
    if not child_present:
        client.execute(child_ddl)
        storage_preflight(client, tables=(RESERVATION_REASON,))
    if actual == old:
        client.execute(count_ddl)
    if actual in {old, partial}:
        client.execute(hash_ddl)
    storage_preflight(client, tables=(RESERVATION_REASON, SQUEEZE_COMMIT_V3))
    print("V3 reservation reasons: table and commit fence verified; 0 rows inserted")
    return "upgraded"


def install_missing(client: object, *, apply: bool, profile: str = "fixed-v2",
                    verify_batch_size: int = 8) -> tuple[int, int]:
    """Return (already installed, newly created); verify bounded DDL batches."""
    if type(verify_batch_size) is not int or not 1 <= verify_batch_size <= 16:
        raise ValueError("Journal verification batch size must be 1–16")
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Journal install requires SSD-only live_market_ssd")
    if client.execute("SELECT count() FROM system.databases WHERE name='arte'").strip() != "1":
        raise RuntimeError("Existing arte database is required")
    contracts = profile_contracts(profile)
    by_name = {table.name: table for table in contracts}
    missing, _ = (plan_missing(client) if profile == "fixed-v2" else
                  plan_missing(client, profile=profile))
    installed = len(contracts) - len(missing)
    print(f"Normalized journal: {installed} verified, {len(missing)} absent")
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return installed, 0
    created = 0
    pending = []
    for name in missing:
        # IF NOT EXISTS protects restart after a lost response. The exact
        # batch check catches an incompatible table; it is never overwritten.
        client.execute(by_name[name].ddl())
        pending.append(by_name[name])
        if len(pending) == verify_batch_size or name == missing[-1]:
            storage_preflight(client, tables=tuple(pending))
            created += len(pending)
            print(f"Created and verified {created}/{len(missing)}; "
                  f"latest arte.{name}", flush=True)
            pending.clear()
    storage_preflight(client, tables=contracts)
    print(f"Complete: {len(contracts)} verified; {created} newly created; 0 rows inserted")
    return installed, created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://DESKTOP-SAAI85T:18123")
    parser.add_argument("--apply", action="store_true",
                        help="apply requested table DDL after exact preflight")
    parser.add_argument("--profile", choices=("fixed-v2", "fixed-v3"),
                        default="fixed-v2", help="exact journal table layout")
    parser.add_argument("--upgrade-v3-reservation-reason", action="store_true",
                        help="verify or install the empty-fence V3 reason upgrade")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                parsed.query, parsed.fragment, parsed.username, parsed.password)
            != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
        parser.error("Run on DESKTOP-SAAI85T against its managed ClickHouse endpoint")
    try:
        client = _admin_client(args.url)
        try:
            if args.upgrade_v3_reservation_reason:
                upgrade_v3_reservation_reason(client, apply=args.apply)
            else:
                install_missing(client, apply=args.apply, profile=args.profile)
        finally:
            client.close()
    except KeyboardInterrupt:
        print("Interrupted; completed DDL remains. Rerun to verify or finish.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Journal layout blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
