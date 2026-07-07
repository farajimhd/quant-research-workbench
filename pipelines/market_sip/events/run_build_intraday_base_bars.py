from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_intraday_base_bars.py"


DEFAULTS = {
    "database": "market_sip_compact",
    "events_table": "events",
    "intraday_base_bars_table": "intraday_base_bars_by_time_ticker",
    "intraday_condition_bars_table": "intraday_condition_bars_by_time_ticker",
    "condition_token_reference_table": "event_condition_token_reference",
    "ticker_day_index_table": "events_ticker_day_index",
    "status_table": "intraday_base_bars_build_status",
    "resolutions": "100ms,1s,5s,30s,60s",
    "chunk_mode": "month",
    "chunk_days": 31,
    "ticker_batch_max_events": 100_000_000,
    "ticker_batch_max_tickers": 256,
    "max_threads": 32,
    "max_memory_usage": "300G",
    "output_root": r"D:\market-data\prepared\clickhouse_sip_ingest\intraday_base_bars",
    "progress_layout": "auto",
    "progress_refresh_per_second": 2.0,
    "progress_log_lines": 10,
    "progress_screen": True,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for ClickHouse intraday base-bar build from compact events.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--intraday-base-bars-table", default=DEFAULTS["intraday_base_bars_table"])
    parser.add_argument("--intraday-condition-bars-table", default=DEFAULTS["intraday_condition_bars_table"])
    parser.add_argument("--condition-token-reference-table", default=DEFAULTS["condition_token_reference_table"])
    parser.add_argument("--ticker-day-index-table", default=DEFAULTS["ticker_day_index_table"])
    parser.add_argument("--status-table", default=DEFAULTS["status_table"])
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--resolutions", default=DEFAULTS["resolutions"])
    parser.add_argument("--tickers", default="")
    parser.add_argument("--chunk-mode", choices=("month", "fixed"), default=DEFAULTS["chunk_mode"])
    parser.add_argument("--chunk-days", type=int, default=DEFAULTS["chunk_days"])
    parser.add_argument("--ticker-batch-max-events", type=int, default=DEFAULTS["ticker_batch_max_events"])
    parser.add_argument("--ticker-batch-max-tickers", type=int, default=DEFAULTS["ticker_batch_max_tickers"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--progress-refresh-per-second", type=float, default=DEFAULTS["progress_refresh_per_second"])
    parser.add_argument("--progress-log-lines", type=int, default=DEFAULTS["progress_log_lines"])
    parser.add_argument("--progress-screen", action=argparse.BooleanOptionalAction, default=DEFAULTS["progress_screen"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--adopt-existing-complete", action="store_true")
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = [
        sys.executable,
        str(SCRIPT),
        "--database",
        args.database,
        "--events-table",
        args.events_table,
        "--intraday-base-bars-table",
        args.intraday_base_bars_table,
        "--intraday-condition-bars-table",
        args.intraday_condition_bars_table,
        "--condition-token-reference-table",
        args.condition_token_reference_table,
        "--ticker-day-index-table",
        args.ticker_day_index_table,
        "--status-table",
        args.status_table,
        "--resolutions",
        args.resolutions,
        "--chunk-mode",
        args.chunk_mode,
        "--chunk-days",
        str(args.chunk_days),
        "--ticker-batch-max-events",
        str(args.ticker_batch_max_events),
        "--ticker-batch-max-tickers",
        str(args.ticker_batch_max_tickers),
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root",
        args.output_root,
        "--progress-layout",
        args.progress_layout,
        "--progress-refresh-per-second",
        str(args.progress_refresh_per_second),
        "--progress-log-lines",
        str(args.progress_log_lines),
    ]
    command.append("--progress-screen" if args.progress_screen else "--no-progress-screen")
    if args.date:
        command.extend(["--date", args.date])
    else:
        command.extend(["--start-date", args.start_date, "--end-date", args.end_date])
    if args.tickers:
        command.extend(["--tickers", args.tickers])
    if args.storage_policy:
        command.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        command.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        command.extend(["--user", args.user])
    if args.password:
        command.extend(["--password", args.password])
    if args.replace_existing:
        command.append("--replace-existing")
    if args.adopt_existing_complete:
        command.append("--adopt-existing-complete")
    if args.no_audit:
        command.append("--no-audit")
    if args.dry_run:
        command.append("--dry-run")
    print("COMMAND", " ".join(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
