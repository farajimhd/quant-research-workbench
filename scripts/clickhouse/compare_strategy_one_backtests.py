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


def _explain_difference(client, left_id: str, right_id: str) -> None:
    """Print bounded scalar lineage and fill deltas, never source payloads."""
    for table, fields in (
        ("trading_run_v1", ("configuration_hash", "code_hash",
                            "market_plan_token", "evaluation_interval_ms")),
        ("trading_backtest_definition_v1",
         ("configuration_revision_id", "causal_v7_plan_token",
          "ticker_population_mode", "start_local_ms", "end_local_ms",
          "simulation_profile", "activation_delay_us")),
        ("trading_backtest_definition_commit_v1",
         ("ticker_count", "ticker_hash", "assignment_hash")),
    ):
        snapshots = []
        for run_id in (left_id, right_id):
            rows = _rows(client, f"SELECT {','.join(fields)} FROM arte.{table} "
                         f"WHERE run_id={_literal(run_id)} LIMIT 2 FORMAT JSONEachRow")
            if len(rows) != 1 or set(rows[0]) != set(fields):
                raise RuntimeError(f"{table} lacks one exact scalar lineage row")
            snapshots.append(rows[0])
        changed = [field for field in fields
                   if snapshots[0][field] != snapshots[1][field]]
        print(f"{table}: {len(changed)} differing fields", flush=True)
        for field in changed:
            print(f"  {field}:\n    left:  {snapshots[0][field]}"
                  f"\n    right: {snapshots[1][field]}", flush=True)

    def fill_counts(run_id: str) -> dict[tuple[str, str, str], tuple[int, str, str]]:
        rows = _rows(client,
            "SELECT broker_order_id,ticker,side,count() AS fills,"
            "min(source_event_time) AS first_time,"
            "max(source_event_time) AS last_time "
            "FROM arte.trading_execution_v1 "
            f"WHERE run_id={_literal(run_id)} "
            "GROUP BY broker_order_id,ticker,side LIMIT 1001 FORMAT JSONEachRow")
        if len(rows) > 1000:
            raise RuntimeError("Execution comparison exceeds 1,000 order-side groups")
        return {(str(row["broker_order_id"]), str(row["ticker"]), str(row["side"])):
                (int(row["fills"]), str(row["first_time"]), str(row["last_time"]))
                for row in rows}

    left, right = fill_counts(left_id), fill_counts(right_id)
    changed = [(key, left.get(key), right.get(key)) for key in sorted(left.keys() | right.keys())
               if left.get(key) != right.get(key)]
    print(f"Execution order-side groups differing: {len(changed)}", flush=True)
    for (order_id, ticker, side), before, after in changed[:30]:
        print(f"  {ticker} {side} order={order_id}:"
              f" left={before} right={after}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--right", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--explain", action="store_true",
                        help="print bounded scalar lineage and order-side fill differences")
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
        if failed and args.explain:
            _explain_difference(client, args.left, args.right)
        if failed:
            raise SystemExit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
