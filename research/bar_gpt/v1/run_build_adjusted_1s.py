from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v1.cohort import BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE, BAR_GPT_ADJUSTED_1S_TABLE, BAR_GPT_COHORT_2TB, BAR_GPT_SPLIT_FACTOR_TABLE

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

DEFAULTS = {"start_date": "2019-01-01", "end_date": "auto", "adjustment_asof_date": "auto",
            "tickers": ",".join(BAR_GPT_COHORT_2TB), "target_table": BAR_GPT_ADJUSTED_1S_TABLE,
            "manifest_table": BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE, "factor_table": BAR_GPT_SPLIT_FACTOR_TABLE,
            "max_threads": "8", "max_memory_usage": "48G", "external_group_by": "12G",
            "runtime_root": r"D:\TradingML\runtimes\bar_gpt\v1\build_adjusted_1s"}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Launch the split-adjusted BarGPT 1s v2 build.")
    parser.add_argument("--execute", action="store_true")
    for name in ("start_date", "end_date", "adjustment_asof_date", "tickers", "target_table", "manifest_table", "factor_table"):
        parser.add_argument("--" + name.replace("_", "-"), default=DEFAULTS[name])
    parser.add_argument("--max-threads", type=int, default=int(DEFAULTS["max_threads"]))
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    tickers = tuple(sorted({value.strip().upper() for value in args.tickers.split(",") if value.strip()}))
    canonical = tuple(sorted(BAR_GPT_COHORT_2TB))
    if tickers != canonical and any((args.target_table == BAR_GPT_ADJUSTED_1S_TABLE,
                                     args.manifest_table == BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE,
                                     args.factor_table == BAR_GPT_SPLIT_FACTOR_TABLE)):
        raise SystemExit("Custom --tickers require custom target, manifest, and factor table names")
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.build_adjusted_1s",
               "--start-date", args.start_date, "--end-date", args.end_date,
               "--adjustment-asof-date", args.adjustment_asof_date, "--tickers", ",".join(tickers),
               "--target-table", args.target_table, "--manifest-table", args.manifest_table,
               "--factor-table", args.factor_table, "--max-threads", str(args.max_threads),
               "--max-memory-usage", DEFAULTS["max_memory_usage"],
               "--max-bytes-before-external-group-by", DEFAULTS["external_group_by"],
               "--runtime-root", DEFAULTS["runtime_root"], "--progress-layout", args.progress_layout]
    if args.execute:
        command.append("--execute")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo)


if __name__ == "__main__":
    raise SystemExit(main())
