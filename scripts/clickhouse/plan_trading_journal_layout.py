"""Read-only inventory and optional DDL plan for the typed ARTE journal.

This command never creates tables, changes grants, or inserts rows. The
dedicated journal principal is used only to inspect existing table contracts.
"""
from __future__ import annotations

import argparse
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
from src.trading_runtime.arte_journal_writer import journal_client_from_env


def plan_missing(client: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Verify every installed contract, then return only missing table DDL."""
    contracts = fixed_backtest_v2_contracts()
    missing_names = missing_fixed_backtest_v2_tables(client)
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
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("journal credential file is unavailable")
    # The explicitly selected private file, not an inherited shell credential,
    # owns this read-only audit's identity.
    load_dotenv(args.env_file, override=True)
    client = journal_client_from_env()
    try:
        missing, ddl = plan_missing(client)
    finally:
        client.close()
    print(f"Fixed V2 expected: {len(fixed_backtest_v2_contracts())}; missing: {len(missing)}")
    for name in missing:
        print(name)
    if args.show_ddl:
        for statement in ddl:
            print(statement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
