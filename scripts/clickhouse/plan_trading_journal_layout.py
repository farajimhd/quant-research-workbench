"""Read-only inventory and optional DDL plan for the typed ARTE journal.

This command never creates tables, changes grants, or inserts rows. The
dedicated journal principal is used only to inspect existing table contracts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from dotenv import load_dotenv

from src.trading_runtime.arte_journal_schema import (
    fixed_backtest_v2_contracts, missing_fixed_backtest_v2_tables,
    storage_preflight,
)
from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
    PROTECTED_EXIT_SATISFIED, PROTECTION_CHANGE_TABLES,
    PROTECTED_EXIT_SNAPSHOT,
    PORTFOLIO_CONTROL,
    RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
    SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE,
)
from src.backend.backtest_terminal_v3_fence import TERMINAL_COMMIT_V3
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.trading_runtime.arte_journal_writer import journal_client_from_env


def profile_contracts(profile: str = "fixed-v2") -> tuple[Any, ...]:
    if profile == "fixed-v2":
        return fixed_backtest_v2_contracts()
    if profile == "fixed-v3":
        contracts = fixed_backtest_v2_contracts() + (
            SQUEEZE_EPISODE, RESERVATION_REASON,
            RECONCILIATION_DIFFERENCE, PORTFOLIO_CONTROL,
            *TRADE_PROPOSAL_TABLES, *BROKER_OMS_TABLES,
            *ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
            PROTECTED_EXIT_SATISFIED, *PROTECTION_CHANGE_TABLES,
            PROTECTED_EXIT_SNAPSHOT,
            SQUEEZE_COMMIT_V3, TERMINAL_COMMIT_V3)
        if len({table.name for table in contracts}) != len(contracts):
            raise RuntimeError("Fixed V3 journal table contract repeats a name")
        return contracts
    raise ValueError("Unknown journal layout profile")


def plan_missing(client: Any, *, profile: str = "fixed-v2") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Verify every installed contract, then return only missing table DDL."""
    contracts = profile_contracts(profile)
    if profile == "fixed-v2":
        missing_names = missing_fixed_backtest_v2_tables(client)
    else:
        names = ",".join(f"'{table.name}'" for table in contracts)
        rows = [json.loads(line) for line in client.execute(
            "SELECT name FROM system.tables WHERE database='arte' "
            f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
        installed = [row.get("name") for row in rows]
        expected = {table.name for table in contracts}
        if (len(installed) != len(set(installed)) or any(
                set(row) != {"name"} or row["name"] not in expected for row in rows)):
            raise RuntimeError("Fixed V3 journal catalog is ambiguous")
        missing_names = tuple(table.name for table in contracts
                              if table.name not in installed)
    present = tuple(table for table in contracts if table.name not in missing_names)
    # Existing but incompatible tables must be repaired explicitly; creating
    # the missing tables cannot make an incompatible layout safe.
    if present:
        storage_preflight(client, tables=present)
    missing = tuple(table for table in contracts if table.name in missing_names)
    return tuple(table.name for table in missing), tuple(table.ddl() for table in missing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True,
                        help="private journal credential file; never printed")
    parser.add_argument("--show-ddl", action="store_true",
                        help="print operator-reviewed CREATE TABLE statements")
    parser.add_argument("--profile", choices=("fixed-v2", "fixed-v3"),
                        default="fixed-v2", help="exact journal table layout")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("journal credential file is unavailable")
    # The explicitly selected private file, not an inherited shell credential,
    # owns this read-only audit's identity.
    load_dotenv(args.env_file, override=True)
    client = journal_client_from_env()
    try:
        missing, ddl = plan_missing(client, profile=args.profile)
    finally:
        client.close()
    print(
        f"{args.profile} expected: {len(profile_contracts(args.profile))}; "
        f"not visible to this principal or absent: {len(missing)}"
    )
    for name in missing:
        print(name)
    if args.show_ddl:
        for statement in ddl:
            print(statement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
