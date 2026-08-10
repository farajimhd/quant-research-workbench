from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v1.cohort import BAR_GPT_COHORT_2TB


TRAIN_START_DATE = "2019-01-01"
TRAIN_END_DATE = "2022-01-01"
VALIDATION_START_DATE = "2026-01-01"
VALIDATION_END_DATE = "2026-08-01"
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v4")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build BarGPT condition authority and offline shards for calendar years "
            "2019-2021 plus the available January-July 2026 validation range."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Run all stages; omit for a read-only plan.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    parser.add_argument("--condition-max-threads", type=int, default=8)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Replace compatible existing shards instead of resuming; normally leave this disabled.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.condition_max_threads <= 0:
        parser.error("--condition-max-threads must be positive")
    return args


def commands(args: argparse.Namespace) -> tuple[tuple[str, list[str]], ...]:
    # Build the complete selected 100-symbol data authority. Training/holdout
    # selection remains a downstream experiment concern and must not mutate it.
    tickers = ",".join(BAR_GPT_COHORT_2TB)

    def condition_command(start_date: str, end_date: str) -> list[str]:
        return [
            sys.executable,
            "-B",
            "-m",
            "research.bar_gpt.v1.run_build_conditions_1s",
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--tickers",
            tickers,
            "--max-threads",
            str(args.condition_max_threads),
            "--progress-layout",
            str(args.progress_layout),
        ]

    def shard_command(selection: str, start_date: str, end_date: str) -> list[str]:
        command = [
            sys.executable,
            "-B",
            "-m",
            "research.bar_gpt.v1.run_build_offline_shards",
            "--output-root",
            str(args.output_root),
            "--selection",
            "all",
            "--tickers",
            tickers,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--workers",
            str(args.workers),
            "--cpu-threads-per-worker",
            str(args.cpu_threads_per_worker),
            "--clickhouse-max-concurrent-pages",
            str(args.clickhouse_max_concurrent_pages),
            "--progress-layout",
            str(args.progress_layout),
        ]
        if args.execute:
            command.append("--execute")
        if args.force_rebuild:
            command.append("--force-rebuild")
        return command

    return (
        ("2019-2021 condition authority", condition_command(TRAIN_START_DATE, TRAIN_END_DATE)),
        ("2019-2021 training shards", shard_command("train", TRAIN_START_DATE, TRAIN_END_DATE)),
        ("2026 condition authority", condition_command(VALIDATION_START_DATE, VALIDATION_END_DATE)),
        ("2026 validation shards", shard_command("validation", VALIDATION_START_DATE, VALIDATION_END_DATE)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    stages = commands(args)
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Stage {index}/{len(stages)} - {label}:", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
    if not args.execute:
        print("Plan only; add --execute to run all four restart-safe stages.", flush=True)
        return 0

    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Starting stage {index}/{len(stages)}: {label}", flush=True)
        status = subprocess.call(command, cwd=repo_root, env=environment)
        if status:
            print(f"Stage failed with exit code {status}: {label}", flush=True)
            return int(status)
    print(
        "All requested condition ranges and certified offline shards completed; "
        "the validated pilot was not rebuilt.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
