from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE,
    BAR_GPT_DAILY_BOOTSTRAP_TABLE,
)


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

DEFAULTS = {
    "start_date": "2016-01-01",
    "end_date": "2019-01-02",
    "tickers": ",".join(BAR_GPT_COHORT_2TB),
    "target_table": BAR_GPT_DAILY_BOOTSTRAP_TABLE,
    "manifest_table": BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE,
    "workers": "8",
    "runtime_root": r"D:\TradingML\runtimes\bar_gpt\v1\build_daily_context",
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Launch the BarGPT pre-2019 Massive daily context bootstrap.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--tickers", default=DEFAULTS["tickers"])
    parser.add_argument("--target-table", default=DEFAULTS["target_table"])
    parser.add_argument("--manifest-table", default=DEFAULTS["manifest_table"])
    parser.add_argument("--workers", type=int, default=int(DEFAULTS["workers"]))
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    requested = tuple(sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()}))
    canonical = tuple(sorted(BAR_GPT_COHORT_2TB))
    if requested != canonical and (
        args.target_table == BAR_GPT_DAILY_BOOTSTRAP_TABLE
        or args.manifest_table == BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE
    ):
        raise SystemExit("Custom --tickers require custom target and manifest table names")
    command = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.build_daily_context",
        "--start-date", args.start_date, "--end-date", args.end_date,
        "--tickers", ",".join(requested), "--target-table", args.target_table,
        "--manifest-table", args.manifest_table, "--workers", str(args.workers),
        "--runtime-root", DEFAULTS["runtime_root"], "--progress-layout", args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
