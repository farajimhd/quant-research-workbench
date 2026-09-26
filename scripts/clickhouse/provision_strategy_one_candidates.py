"""Install and verify producer-owned Strategy 1 derived-product tables.

Dry-run by default. This operator command never writes market bars, indicators,
or liquidity and never grants a Backtest principal write authority.
"""
from __future__ import annotations

import argparse
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

from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, STORAGE_POLICY, install_tables,
    rename_empty_legacy_rule_column,
)
from src.trading_runtime.strategy_one_pivot_schema import (
    PIVOT_TABLE, COVERAGE_TABLE as PIVOT_COVERAGE_TABLE,
    install_tables as install_pivot_tables,
)
from src.trading_runtime.strategy_one_hod_schema import (
    CONTEXT_TABLE as HOD_CONTEXT_TABLE,
    COVERAGE_TABLE as HOD_COVERAGE_TABLE,
    install_tables as install_hod_tables,
)


URL = "http://DESKTOP-SAAI85T:18123"
WORKSTATION_IPV4 = "192.168.1.218"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create the six Strategy 1 app-owned tables")
    parser.add_argument("--confirm-strategy-one-candidates", action="store_true",
                        help="required second confirmation for --apply")
    parser.add_argument("--rename-empty-rule-column", action="store_true",
                        help="one-time repair of the empty draft coverage table")
    args = parser.parse_args(argv)
    if not args.apply:
        if args.rename_empty_rule_column:
            parser.error("--rename-empty-rule-column requires --apply")
        print(f"DRY RUN: {CANDIDATE_TABLE}, {COVERAGE_TABLE}, "
              f"{PIVOT_TABLE}, {PIVOT_COVERAGE_TABLE}, "
              f"{HOD_CONTEXT_TABLE}, {HOD_COVERAGE_TABLE}; "
              f"storage={STORAGE_POLICY}; no connection or database change.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-strategy-one-candidates.")
        return 0
    if not args.confirm_strategy_one_candidates:
        parser.error("--apply requires --confirm-strategy-one-candidates")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: table installation requires the managed workstation.",
              file=sys.stderr)
        return 1
    parsed = urlsplit(URL)
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path, parsed.query,
            parsed.fragment, parsed.username, parsed.password) != (
            "http", "desktop-saai85t", 18123, "", "", "", None, None):
        print("Blocked: unexpected ClickHouse endpoint.", file=sys.stderr)
        return 1
    client = None
    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        client = _admin_client(f"http://{WORKSTATION_IPV4}:{parsed.port}")
        if args.rename_empty_rule_column:
            rename_empty_legacy_rule_column(client)
        install_tables(client)
        install_pivot_tables(client)
        install_hod_tables(client)
    except Exception as exc:
        # Driver errors can embed SQL or credentials. Keep terminal output safe.
        print(f"Strategy 1 product installation stopped: {type(exc).__name__}. "
              "Inspect private operator diagnostics before retry.", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    print("Strategy 1 derived-product tables verified: exact schema and SSD placement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
