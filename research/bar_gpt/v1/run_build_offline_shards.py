from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v1.cohort import (
    BAR_GPT_IDENTITY_HOLDOUT_TICKERS,
    BAR_GPT_TRAINING_TICKERS,
)


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Workstation launcher for the BarGPT offline tensor compiler.")
    parser.add_argument("--execute", action="store_true", help="Required to write; omit for the safe plan.")
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v7")
    parser.add_argument("--workers", type=int, default=min(12, max(2, (os.cpu_count() or 8) // 4)))
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    parser.add_argument("--tickers", default=",".join(BAR_GPT_TRAINING_TICKERS))
    parser.add_argument("--selection", choices=("all", "train", "validation"), default="all")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2026-08-01")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    args, extra = parser.parse_known_args(argv)
    return args, extra


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    if args.selection == "train":
        tickers = tuple(
            ticker for ticker in BAR_GPT_TRAINING_TICKERS
            if ticker not in BAR_GPT_IDENTITY_HOLDOUT_TICKERS
        )
    elif args.selection == "validation":
        tickers = BAR_GPT_TRAINING_TICKERS
    else:
        tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    command = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.offline_shards",
        "--output-root", args.output_root,
        "--workers", str(args.workers),
        "--cpu-threads-per-worker", str(args.cpu_threads_per_worker),
        "--clickhouse-max-concurrent-pages", str(args.clickhouse_max_concurrent_pages),
        "--tickers", ",".join(tickers),
        "--start-date", args.start_date,
        "--end-date", args.end_date,
        "--progress-layout", args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
