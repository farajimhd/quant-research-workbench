from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "database": "market_sip_compact",
    "quote_table": "quotes",
    "trade_table": "trades",
    "train_table": "train_2019_to_2025",
    "validation_table": "validation_2026",
    "train_start_date": "2019-01-01",
    "train_end_date": "2025-12-31",
    "validation_start_date": "2026-01-01",
    "validation_end_date": "2099-12-31",
    "events_per_chunk": 128,
    "min_events": 128,
    "clean_mode": "structural",
    "max_threads": 32,
    "max_memory_usage": "400G",
    "rebuild": False,
    "dry_run": False,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for compact SIP sampling index build.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--quote-table", default=DEFAULTS["quote_table"])
    parser.add_argument("--trade-table", default=DEFAULTS["trade_table"])
    parser.add_argument("--train-table", default=DEFAULTS["train_table"])
    parser.add_argument("--validation-table", default=DEFAULTS["validation_table"])
    parser.add_argument("--train-start-date", default=DEFAULTS["train_start_date"])
    parser.add_argument("--train-end-date", default=DEFAULTS["train_end_date"])
    parser.add_argument("--validation-start-date", default=DEFAULTS["validation_start_date"])
    parser.add_argument("--validation-end-date", default=DEFAULTS["validation_end_date"])
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--min-events", type=int, default=DEFAULTS["min_events"])
    parser.add_argument("--clean-mode", choices=("structural", "issue_flags_zero"), default=DEFAULTS["clean_mode"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--rebuild", action="store_true", default=DEFAULTS["rebuild"])
    parser.add_argument("--dry-run", action="store_true", default=DEFAULTS["dry_run"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = Path(__file__).with_name("clickhouse_build_compact_sampling_index.py")
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
        "--train-table",
        args.train_table,
        "--validation-table",
        args.validation_table,
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
        "--min-events",
        str(args.min_events),
        "--clean-mode",
        args.clean_mode,
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
    ]
    if args.rebuild:
        command.append("--rebuild")
    if args.dry_run:
        command.append("--dry-run")
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
