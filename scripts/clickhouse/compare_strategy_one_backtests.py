"""Cold-verify and compare two completed Strategy 1 Backtest outcomes.

This is read-only: it never writes market, journal, or local data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_v4_saved_review import load_v4_terminal_review_page
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, backtest_v4_operator_client_from_env,
)


FIELDS = {
    "trading_execution_v1": (
        "account_id", "ticker", "side", "quantity", "price", "exchange",
        "currency", "net_amount", "cumulative_quantity", "average_price",
        "liquidity", "liquidation_trade", "signal_price", "arrival_midpoint",
        "planned_risk", "source_event_time", "strategy_id",
        "strategy_revision", "setup", "exit_reason",
    ),
    "trading_commission_v1": (
        "account_id", "commission", "currency", "status", "time_authority",
        "realized_pnl", "source_event_time",
    ),
    "trading_order_command_v1": (
        "account_id", "conid", "ticker", "side", "order_type", "time_in_force",
        "quantity", "cash_quantity", "limit_price", "aux_price",
        "outside_rth", "strategy_id", "strategy_revision", "created_at",
        "security_type", "listing_exchange", "trailing_amount", "trailing_type",
    ),
}


def _rows_for(client, run_id: str, table: str, fields: tuple[str, ...]) -> list[tuple]:
    rows = _rows(client, f"SELECT {','.join(fields)} FROM arte.{table} "
                 f"WHERE run_id={_literal(run_id)} "
                 "LIMIT 100001 FORMAT JSONEachRow")
    if len(rows) > 100_000:
        raise RuntimeError(f"{table} comparison exceeds its 100,000-row bound")
    if any(set(row) != set(fields) for row in rows):
        raise RuntimeError(f"{table} comparison returned malformed rows")
    return sorted(tuple(str(row[field]) for field in fields) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--right", required=True, type=lambda value: str(UUID(value)))
    args = parser.parse_args()
    _load_private_credentials()
    client = backtest_v4_operator_client_from_env()
    try:
        reviews = [load_v4_terminal_review_page(client, run_id, limit=1)
                   for run_id in (args.left, args.right)]
        if any(review["status"] != "completed" for review in reviews):
            raise RuntimeError("Both runs must have cold-verified completed journals")
        print("Cold V4 journal verification: passed for both runs", flush=True)
        financial = [{account: {key: value for key, value in row.items()
                                if key != "source_timestamp_ms"}
                      for account, row in review["financial_accounts"].items()}
                     for review in reviews]
        failed = financial[0] != financial[1]
        print(f"Final financial accounts: {'MATCH' if not failed else 'DIFFER'} ",
              f"left={len(financial[0])} right={len(financial[1])}", flush=True)
        for table, fields in FIELDS.items():
            left = _rows_for(client, args.left, table, fields)
            right = _rows_for(client, args.right, table, fields)
            match = left == right
            failed |= not match
            print(f"{table}: {'MATCH' if match else 'DIFFER'} "
                  f"left={len(left)} right={len(right)}", flush=True)
        if failed:
            raise SystemExit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
