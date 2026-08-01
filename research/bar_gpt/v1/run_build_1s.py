from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


DEFAULTS = {
    "start_date": "auto",
    "end_date": "auto",
    "ticker_batch_max_events": "40000000",
    "ticker_batch_max_tickers": "256",
    "max_threads": "8",
    "max_memory_usage": "48G",
    "max_bytes_before_external_group_by": "12G",
    "runtime_root": r"D:\TradingML\runtimes\bar_gpt\v1\build_1s",
}


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Workstation launcher for the BarGPT v1 one-second ClickHouse build.")
    parser.add_argument("--execute", action="store_true", help="Required to write. Omit for a safe SQL/plan preview.")
    parser.add_argument("--validate-sql", action="store_true")
    parser.add_argument("--no-print-sql", action="store_true")
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--tickers", default="")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    args, extra = parser.parse_known_args()
    return args, extra


def main() -> int:
    args, extra = parse_args()
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.build_1s",
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--ticker-batch-max-events",
        DEFAULTS["ticker_batch_max_events"],
        "--ticker-batch-max-tickers",
        DEFAULTS["ticker_batch_max_tickers"],
        "--max-threads",
        DEFAULTS["max_threads"],
        "--max-memory-usage",
        DEFAULTS["max_memory_usage"],
        "--max-bytes-before-external-group-by",
        DEFAULTS["max_bytes_before_external_group_by"],
        "--runtime-root",
        DEFAULTS["runtime_root"],
        "--progress-layout",
        args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
    if args.validate_sql:
        command.append("--validate-sql")
    if args.no_print_sql:
        command.append("--no-print-sql")
    if args.tickers:
        command.extend(("--tickers", args.tickers))
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
