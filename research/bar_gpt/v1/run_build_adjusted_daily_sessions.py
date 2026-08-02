from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from research.bar_gpt.v1.cohort import (
    BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE,
    BAR_GPT_ADJUSTED_DAILY_TABLE,
    BAR_GPT_COHORT_2TB,
)

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

DEFAULTS = {
    "start_date": "2017-01-01", "end_date": "auto", "adjustment_asof_date": "auto",
    "tickers": ",".join(BAR_GPT_COHORT_2TB), "target_table": BAR_GPT_ADJUSTED_DAILY_TABLE,
    "manifest_table": BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE, "workers": "8",
    "runtime_root": r"D:\TradingML\runtimes\bar_gpt\v1\build_adjusted_daily_sessions",
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Launch the adjusted three-session BarGPT daily build.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--adjustment-asof-date", default=DEFAULTS["adjustment_asof_date"])
    parser.add_argument("--tickers", default=DEFAULTS["tickers"])
    parser.add_argument("--target-table", default=DEFAULTS["target_table"])
    parser.add_argument("--manifest-table", default=DEFAULTS["manifest_table"])
    parser.add_argument("--workers", type=int, default=int(DEFAULTS["workers"]))
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_known_args(argv)


def resolve_adjustment_asof(value: str) -> str:
    return (
        dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        if value == "auto" else value
    )


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    tickers = tuple(sorted({value.strip().upper() for value in args.tickers.split(",") if value.strip()}))
    canonical = tuple(sorted(BAR_GPT_COHORT_2TB))
    if tickers != canonical and (args.target_table == BAR_GPT_ADJUSTED_DAILY_TABLE or args.manifest_table == BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE):
        raise SystemExit("Custom --tickers require custom target and manifest table names")
    resolved_asof = resolve_adjustment_asof(args.adjustment_asof_date)
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.build_adjusted_daily_sessions",
               "--start-date", args.start_date, "--end-date", args.end_date,
               "--adjustment-asof-date", resolved_asof, "--tickers", ",".join(tickers),
               "--target-table", args.target_table, "--manifest-table", args.manifest_table,
               "--workers", str(args.workers), "--runtime-root", DEFAULTS["runtime_root"],
               "--progress-layout", args.progress_layout]
    if args.execute:
        command.append("--execute")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo)


if __name__ == "__main__":
    raise SystemExit(main())
