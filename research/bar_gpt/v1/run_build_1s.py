from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_COHORT_2TB_TABLE,
)
from research.bar_gpt.v1.build_1s import LEGACY_COHORT_TABLE


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


DEFAULTS = {
    "start_date": "2019-01-01",
    "end_date": "auto",
    "ticker_batch_max_events": "40000000",
    "ticker_batch_max_tickers": "256",
    "max_threads": "8",
    "max_memory_usage": "48G",
    "max_bytes_before_external_group_by": "12G",
    "runtime_root": r"D:\TradingML\runtimes\bar_gpt\v1\build_1s",
    "tickers": ",".join(BAR_GPT_COHORT_2TB),
    "target_table": BAR_GPT_COHORT_2TB_TABLE,
    "manifest_table": BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Workstation launcher for the BarGPT v1 one-second ClickHouse build.")
    parser.add_argument("--execute", action="store_true", help="Required to write. Omit for a safe SQL/plan preview.")
    parser.add_argument("--validate-sql", action="store_true")
    parser.add_argument("--no-print-sql", action="store_true")
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--tickers", default=DEFAULTS["tickers"])
    parser.add_argument("--target-table", default=DEFAULTS["target_table"])
    parser.add_argument("--manifest-table", default=DEFAULTS["manifest_table"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument(
        "--keep-v1",
        action="store_true",
        help="Do not retire the canonical v1 cohort table before the first canonical v2 write.",
    )
    args, extra = parser.parse_known_args(argv)
    return args, extra


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    requested = tuple(sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()}))
    canonical = tuple(sorted(BAR_GPT_COHORT_2TB))
    if requested != canonical and (
        args.target_table == BAR_GPT_COHORT_2TB_TABLE
        or args.manifest_table == BAR_GPT_COHORT_2TB_MANIFEST_TABLE
    ):
        raise SystemExit(
            "Custom --tickers require custom --target-table and --manifest-table; "
            "the canonical 2 TB cohort tables cannot mix cohort identities."
        )
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
        "--target-table",
        args.target_table,
        "--manifest-table",
        args.manifest_table,
        "--progress-layout",
        args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
        if (
            not args.keep_v1
            and args.target_table == BAR_GPT_COHORT_2TB_TABLE
            and args.manifest_table == BAR_GPT_COHORT_2TB_MANIFEST_TABLE
        ):
            command.extend(("--drop-v1-cohort-first-run", "--confirm-drop-v1-table", LEGACY_COHORT_TABLE))
    if args.validate_sql:
        command.append("--validate-sql")
    if args.no_print_sql:
        command.append("--no-print-sql")
    command.extend(("--tickers", ",".join(requested)))
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
