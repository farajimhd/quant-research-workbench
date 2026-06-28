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
    "bar_mode": "macro",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "bars_table": "live_market_bars",
    "bars_by_symbol_time_table": "bars_by_symbol_time",
    "bars_by_time_symbol_table": "bars_by_time_symbol",
    "start_date": "2019-01-01",
    "end_date": "2026-12-31",
    "timeframes": "1d,1w,1y",
    "max_threads": 32,
    "max_memory_usage": "400G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\trade_bars",
    "progress_layout": "auto",
    "progress_refresh_per_second": 2.0,
    "progress_log_lines": 12,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for SIP macro-bar build from compact events.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--bar-mode", choices=("macro", "qmd"), default=DEFAULTS["bar_mode"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--bars-table", default=DEFAULTS["bars_table"])
    parser.add_argument("--bars-by-symbol-time-table", default=DEFAULTS["bars_by_symbol_time_table"])
    parser.add_argument("--bars-by-time-symbol-table", default=DEFAULTS["bars_by_time_symbol_table"])
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--timeframes", default=DEFAULTS["timeframes"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--progress-refresh-per-second", type=float, default=DEFAULTS["progress_refresh_per_second"])
    parser.add_argument("--progress-log-lines", type=int, default=DEFAULTS["progress_log_lines"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help=(
            "Repair/rebuild the macro bar table from scratch. This implies --drop-table in macro mode "
            "and is the recommended path after an old all-bars build left UTC-midnight or 1mo rows."
        ),
    )
    parser.add_argument(
        "--purge-unsupported-macro-timeframes",
        action="store_true",
        help="Macro mode only: delete stale non-macro rows such as 1mo before rebuilding the requested range.",
    )
    parser.add_argument("--drop-table", action="store_true")
    parser.add_argument("--no-replace-range", action="store_true")
    parser.add_argument("--no-expand-boundaries", action="store_true")
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
        "--bar-mode",
        args.bar_mode,
        "--macro-bars-table",
        args.macro_bars_table,
        "--bars-table",
        args.bars_table,
        "--bars-by-symbol-time-table",
        args.bars_by_symbol_time_table,
        "--bars-by-time-symbol-table",
        args.bars_by_time_symbol_table,
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
        "--progress-layout",
        args.progress_layout,
        "--progress-refresh-per-second",
        str(args.progress_refresh_per_second),
        "--progress-log-lines",
        str(args.progress_log_lines),
    ]
    if args.storage_policy:
        argv.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        argv.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        argv.extend(["--user", args.user])
    if args.password:
        argv.extend(["--password", args.password])
    if args.full_rebuild and args.bar_mode != "macro":
        raise ValueError("--full-rebuild is only valid with --bar-mode macro")
    drop_table = bool(args.drop_table or args.full_rebuild)
    purge_unsupported = bool(args.purge_unsupported_macro_timeframes or args.full_rebuild)
    if drop_table:
        argv.append("--drop-table")
    if purge_unsupported:
        argv.append("--purge-unsupported-macro-timeframes")
    if args.no_replace_range:
        argv.append("--no-replace-range")
    if args.no_expand_boundaries:
        argv.append("--no-expand-boundaries")
    if args.dry_run:
        argv.append("--dry-run")

    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if args.print_only:
        return 0
    try:
        return subprocess.call(argv)
    except KeyboardInterrupt:
        print("Interrupted by user.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
