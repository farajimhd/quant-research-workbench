from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_trade_bars.py"


DEFAULTS = {
    "database": "market_sip_compact",
    "events_table": "events",
    "bars_table": "live_market_bars",
    "start_date": "2019-01-01",
    "end_date": "2026-12-31",
    "timeframes": "1s,5s,1m,5m,1d,1w,1mo",
    "max_threads": 32,
    "max_memory_usage": "400G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\trade_bars",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for qmd-compatible SIP live_market_bars build.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--bars-table", default=DEFAULTS["bars_table"])
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--timeframes", default=DEFAULTS["timeframes"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--drop-table", action="store_true")
    parser.add_argument("--no-replace-range", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    argv = [
        sys.executable,
        str(SCRIPT),
        "--database",
        args.database,
        "--events-table",
        args.events_table,
        "--bars-table",
        args.bars_table,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--timeframes",
        args.timeframes,
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root-win",
        args.output_root_win,
    ]
    if args.storage_policy:
        argv.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        argv.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        argv.extend(["--user", args.user])
    if args.password:
        argv.extend(["--password", args.password])
    if args.drop_table:
        argv.append("--drop-table")
    if args.no_replace_range:
        argv.append("--no-replace-range")
    if args.dry_run:
        argv.append("--dry-run")

    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(argv)


if __name__ == "__main__":
    raise SystemExit(main())
