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
import socket
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
    PORTFOLIO_CONTROL, RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
    SQUEEZE_COMMIT_V3, staged_portfolio_control_ddl,
    staged_reconciliation_difference_ddl, staged_reservation_reason_ddl,
    staged_trade_proposal_ddl,
)
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from scripts.clickhouse.provision_fixed_backtest_v3_principals import WORKSTATION_IPV4


_REASON_COLUMNS = frozenset({"portfolio_reservation_reason_count",
                             "portfolio_reservation_reason_hash"})
_RECONCILIATION_COLUMNS = frozenset({
    "portfolio_reconciliation_difference_count",
    "portfolio_reconciliation_difference_hash",
})
_CONTROL_COLUMNS = frozenset({"portfolio_control_count", "portfolio_control_hash"})
_PROPOSAL_COLUMNS = frozenset({"trade_proposal_child_count", "trade_proposal_child_hash"})


def _without_proposals(columns):
    return tuple(column for column in columns if column[0] not in _PROPOSAL_COLUMNS)


def _with_existing_proposals(columns, actual):
    """Preserve an already staged proposal suffix during older empty-fence upgrades."""
    present = {name for name, _ in actual} & _PROPOSAL_COLUMNS
    if present not in (set(), {"trade_proposal_child_count"}, _PROPOSAL_COLUMNS):
        raise RuntimeError("V3 commit has an invalid trade-proposal suffix")
    return tuple(column for column in columns
                 if column[0] not in _PROPOSAL_COLUMNS or column[0] in present)


def upgrade_v3_portfolio_control(client: object, *, apply: bool) -> str:
    """Stage scalar control child only on a proved-empty V3 fence."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
    old = tuple(column for column in full
                if column[0] not in {"portfolio_control_count", "portfolio_control_hash"})
    partial = tuple(column for column in full
                    if column[0] != "portfolio_control_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown control schema")
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    exists = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_control_v3'").strip()
    if exists not in {"0", "1"}:
        raise RuntimeError("Portfolio control table catalog is ambiguous")
    if exists == "1":
        storage_preflight(client, tables=(PORTFOLIO_CONTROL,))
    if actual == full and exists == "1":
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; a versioned migration is required")
    if exists == "1" and client.execute(
            "SELECT count() FROM arte.trading_portfolio_control_v3").strip() != "0":
        raise RuntimeError("Portfolio control child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    table_ddl, count_ddl, hash_ddl = staged_portfolio_control_ddl()
    if exists == "0":
        client.execute(table_ddl)
        storage_preflight(client, tables=(PORTFOLIO_CONTROL,))
    if actual == old:
        client.execute(count_ddl)
    if actual in {old, partial}:
        client.execute(hash_ddl)
    storage_preflight(client, tables=(PORTFOLIO_CONTROL, TableContract(
        SQUEEZE_COMMIT_V3.name, full, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order)))
    return "upgraded"


def upgrade_v3_trade_proposal(client: object, *, apply: bool) -> str:
    """Resume the nine child tables and two seal columns only on empty V3."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = SQUEEZE_COMMIT_V3.columns
    old = _without_proposals(full)
    partial = tuple(column for column in full
                    if column[0] != "trade_proposal_child_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown trade-proposal schema")
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    names = ",".join(f"'{table.name}'" for table in TRADE_PROPOSAL_TABLES)
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = {row["name"] for row in rows}
    expected = {table.name for table in TRADE_PROPOSAL_TABLES}
    if (len(installed) != len(rows) or not installed <= expected
            or any(set(row) != {"name"} for row in rows)):
        raise RuntimeError("V3 proposal child inventory is ambiguous")
    for table in TRADE_PROPOSAL_TABLES:
        if table.name in installed:
            storage_preflight(client, tables=(table,))
    if actual == full and installed == expected:
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    for table in TRADE_PROPOSAL_TABLES:
        if table.name in installed and client.execute(
                f"SELECT count() FROM arte.{table.name}").strip() != "0":
            raise RuntimeError("V3 proposal child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_trade_proposal_ddl()
    for table, ddl in zip(TRADE_PROPOSAL_TABLES, ddls):
        if table.name not in installed:
            client.execute(ddl)
            storage_preflight(client, tables=(table,))
    if actual == old:
        client.execute(ddls[-2])
    if actual in {old, partial}:
        client.execute(ddls[-1])
    storage_preflight(client, tables=TRADE_PROPOSAL_TABLES + (SQUEEZE_COMMIT_V3,))
    return "upgraded"


def upgrade_v3_reconciliation_difference(client: object, *, apply: bool) -> str:
    """Operator-only, restart-safe child/fence upgrade; never insert rows."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = tuple(column for column in _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
                 if column[0] not in _CONTROL_COLUMNS or
                 column[0] in {name for name, _ in actual})
    old = tuple(column for column in full
                if column[0] not in _RECONCILIATION_COLUMNS)
    partial = tuple(column for column in full
                    if column[0] != "portfolio_reconciliation_difference_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown schema; no ALTER attempted")
    shape = TableContract(SQUEEZE_COMMIT_V3.name, actual,
                          SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(shape,))
    count = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_reconciliation_difference_v3'"
    ).strip()
    if count not in {"0", "1"}:
        raise RuntimeError("Reconciliation difference table catalog is ambiguous")
    child_present = count == "1"
    if child_present:
        storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE,))
    if actual == full and child_present:
        print("V3 reconciliation differences: schema and SSD placement verified; no change")
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if child_present and client.execute(
            "SELECT count() FROM arte.trading_portfolio_reconciliation_difference_v3"
    ).strip() != "0":
        raise RuntimeError("Reconciliation difference child has rows; no ALTER attempted")
    pending = ["child table"] if not child_present else []
    if actual == old:
        pending.append("V3 difference count and hash")
    elif actual == partial:
        pending.append("V3 difference hash")
    print("V3 reconciliation differences: empty fence verified; pending "
          + ", ".join(pending))
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return "planned"
    child_ddl, count_ddl, hash_ddl = staged_reconciliation_difference_ddl()
    if not child_present:
        client.execute(child_ddl)
        storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE,))
    if actual == old:
        client.execute(count_ddl)
    if actual in {old, partial}:
        client.execute(hash_ddl)
    storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE, TableContract(
        SQUEEZE_COMMIT_V3.name, full, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order)))
    print("V3 reconciliation differences: table and commit fence verified; 0 rows inserted")
    return "upgraded"


def upgrade_v3_reservation_reason(client: object, *, apply: bool) -> str:
    """Restart-safe operator DDL; no row INSERT and no occupied fence rewrite."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = tuple(column for column in _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
                 if column[0] not in _CONTROL_COLUMNS or
                 column[0] in {name for name, _ in actual})
    actual_core = tuple(column for column in actual
                        if column[0] not in _RECONCILIATION_COLUMNS)
    core_full = tuple(column for column in full
                      if column[0] not in _RECONCILIATION_COLUMNS)
    old = tuple(column for column in core_full if column[0] not in _REASON_COLUMNS)
    partial = tuple(column for column in core_full
                    if column[0] != "portfolio_reservation_reason_hash")
    difference_names = {name for name, _ in actual} & _RECONCILIATION_COLUMNS
    allowed_difference_names = (frozenset(),
        frozenset({"portfolio_reconciliation_difference_count"}),
        _RECONCILIATION_COLUMNS)
    if (actual_core not in {old, partial, core_full}
            or frozenset(difference_names) not in allowed_difference_names
            or actual != tuple(column for column in full
                               if column[0] in {name for name, _ in actual})):
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
    if actual_core == core_full and child_present:
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
    if actual_core == old:
        pending.append("V3 reason count and hash")
    elif actual_core == partial:
        pending.append("V3 reason hash")
    print("V3 reservation reasons: empty fence verified; pending " + ", ".join(pending))
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return "planned"
    child_ddl, count_ddl, hash_ddl = staged_reservation_reason_ddl()
    if not child_present:
        client.execute(child_ddl)
        storage_preflight(client, tables=(RESERVATION_REASON,))
    if actual_core == old:
        client.execute(count_ddl)
    if actual_core in {old, partial}:
        client.execute(hash_ddl)
    final_names = {name for name, _ in actual} | _REASON_COLUMNS
    final_shape = TableContract(SQUEEZE_COMMIT_V3.name,
                                tuple(column for column in full if column[0] in final_names),
                                SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(RESERVATION_REASON, final_shape))
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
    parser.add_argument("--upgrade-v3-reconciliation-difference", action="store_true",
                        help="verify or install the empty-fence V3 reconciliation child upgrade")
    parser.add_argument("--upgrade-v3-portfolio-control", action="store_true",
                        help="verify or install the empty-fence V3 scalar control upgrade")
    parser.add_argument("--upgrade-v3-trade-proposal", action="store_true",
                        help="verify or install the empty-fence V3 proposal children")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                parsed.query, parsed.fragment, parsed.username, parsed.password)
            != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
        parser.error("Run on DESKTOP-SAAI85T against its managed ClickHouse endpoint")
    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        client = _admin_client(f"http://{WORKSTATION_IPV4}:{parsed.port}")
        try:
            if sum((args.upgrade_v3_reservation_reason,
                    args.upgrade_v3_reconciliation_difference,
                    args.upgrade_v3_portfolio_control,
                    args.upgrade_v3_trade_proposal)) > 1:
                parser.error("Select only one V3 upgrade at a time")
            if args.upgrade_v3_trade_proposal:
                result = upgrade_v3_trade_proposal(client, apply=args.apply)
                print(f"V3 trade-proposal layout: {result}; no rows inserted")
            elif args.upgrade_v3_portfolio_control:
                result = upgrade_v3_portfolio_control(client, apply=args.apply)
                print(f"V3 portfolio-control layout: {result}; no rows inserted")
            elif args.upgrade_v3_reconciliation_difference:
                result = upgrade_v3_reconciliation_difference(client, apply=args.apply)
                print(f"V3 reconciliation-difference layout: {result}; no rows inserted")
            elif args.upgrade_v3_reservation_reason:
                result = upgrade_v3_reservation_reason(client, apply=args.apply)
                print(f"V3 reservation-reason layout: {result}; no rows inserted")
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
