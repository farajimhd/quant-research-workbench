from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "database": "market_sip_compact",
    "quote_table": "quotes",
    "trade_table": "trades",
    "events_table": "events",
    "manifest_table": "events_build_manifest",
    "train_index_table": "train_2019_to_2025",
    "validation_index_table": "validation_2026",
    "source_start_date": "2019-01-01",
    "source_end_date": "2099-12-31",
    "train_start_date": "2019-01-01",
    "train_end_date": "2025-12-31",
    "validation_start_date": "2026-01-01",
    "validation_end_date": "2099-12-31",
    "events_per_chunk": 128,
    "build_buckets": 256,
    "start_bucket": 0,
    "end_bucket": 255,
    "max_threads": 32,
    "max_memory_usage": "400G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\unified_events",
    "clean_mode": "issue_flags_zero",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for the unified ClickHouse event table builder.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--quote-table", default=DEFAULTS["quote_table"])
    parser.add_argument("--trade-table", default=DEFAULTS["trade_table"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--manifest-table", default=DEFAULTS["manifest_table"])
    parser.add_argument("--train-index-table", default=DEFAULTS["train_index_table"])
    parser.add_argument("--validation-index-table", default=DEFAULTS["validation_index_table"])
    parser.add_argument("--source-start-date", default=DEFAULTS["source_start_date"])
    parser.add_argument("--source-end-date", default=DEFAULTS["source_end_date"])
    parser.add_argument("--train-start-date", default=DEFAULTS["train_start_date"])
    parser.add_argument("--train-end-date", default=DEFAULTS["train_end_date"])
    parser.add_argument("--validation-start-date", default=DEFAULTS["validation_start_date"])
    parser.add_argument("--validation-end-date", default=DEFAULTS["validation_end_date"])
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--build-buckets", type=int, default=DEFAULTS["build_buckets"])
    parser.add_argument("--start-bucket", type=int, default=DEFAULTS["start_bucket"])
    parser.add_argument("--end-bucket", type=int, default=DEFAULTS["end_bucket"])
    parser.add_argument("--limit-buckets", type=int, default=0)
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--clean-mode", choices=("issue_flags_zero", "structural"), default=DEFAULTS["clean_mode"])
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--force-bucket-delete", action="store_true")
    parser.add_argument("--no-build-events", action="store_true")
    parser.add_argument("--no-build-index", action="store_true")
    parser.add_argument("--optimize-final", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = Path(__file__).with_name("clickhouse_build_unified_events.py")
    command = [
        sys.executable,
        "-u",
        str(script),
        "--database",
        args.database,
        "--quote-table",
        args.quote_table,
        "--trade-table",
        args.trade_table,
        "--events-table",
        args.events_table,
        "--manifest-table",
        args.manifest_table,
        "--train-index-table",
        args.train_index_table,
        "--validation-index-table",
        args.validation_index_table,
        "--source-start-date",
        args.source_start_date,
        "--source-end-date",
        args.source_end_date,
        "--train-start-date",
        args.train_start_date,
        "--train-end-date",
        args.train_end_date,
        "--validation-start-date",
        args.validation_start_date,
        "--validation-end-date",
        args.validation_end_date,
        "--events-per-chunk",
        str(args.events_per_chunk),
        "--build-buckets",
        str(args.build_buckets),
        "--start-bucket",
        str(args.start_bucket),
        "--end-bucket",
        str(args.end_bucket),
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root-win",
        args.output_root_win,
        "--clean-mode",
        args.clean_mode,
    ]
    if args.limit_buckets:
        command.extend(["--limit-buckets", str(args.limit_buckets)])
    if args.storage_policy:
        command.extend(["--storage-policy", args.storage_policy])
    if args.rebuild:
        command.append("--rebuild")
    if args.retry_failed:
        command.append("--retry-failed")
    if args.retry_started:
        command.append("--retry-started")
    if args.force_bucket_delete:
        command.append("--force-bucket-delete")
    if args.no_build_events:
        command.append("--no-build-events")
    if args.no_build_index:
        command.append("--no-build-index")
    if args.optimize_final:
        command.append("--optimize-final")
    if args.dry_run:
        command.append("--dry-run")
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
