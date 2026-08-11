from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v1.cohort import (
    BAR_GPT_TRAINING_TICKERS,
)


TRAIN_START_DATE = "2019-01-01"
TRAIN_END_DATE = "2022-01-01"
VALIDATION_START_DATE = "2026-01-01"
VALIDATION_END_DATE = "2026-08-01"
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v11")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compile direct-event BarGPT shards for 2019-2021 and January-July 2026 "
            "without persisting intermediate one-second or daily tables."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Run all stages; omit for a read-only plan.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-threads-per-query", type=int, default=2)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="rich")
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Replace compatible existing shards instead of resuming; normally leave this disabled.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.workers <= 0 or args.clickhouse_max_threads_per_query <= 0:
        parser.error("--workers and --clickhouse-max-threads-per-query must be positive")
    return args


def commands(args: argparse.Namespace) -> tuple[tuple[str, list[str]], ...]:
    # MOGO remains identity-quarantined, so the runnable authority contains
    # the 99 point-in-time-resolvable members of the selected 100-symbol cohort.
    tickers = ",".join(BAR_GPT_TRAINING_TICKERS)
    def shard_command(start_date: str, end_date: str) -> list[str]:
        command = [
            sys.executable,
            "-B",
            "-m",
            "research.bar_gpt.v1.run_build_offline_shards",
            "--output-root",
            str(args.output_root),
            "--selection",
            "all",
            "--source-mode",
            "direct_events",
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
            "--clickhouse-max-threads-per-worker",
            str(args.clickhouse_max_threads_per_query),
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
        ("2019-2021 direct-event training shards", shard_command(TRAIN_START_DATE, TRAIN_END_DATE)),
        ("2026 direct-event validation shards", shard_command(VALIDATION_START_DATE, VALIDATION_END_DATE)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    stages = commands(args)
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Stage {index}/{len(stages)} - {label}:", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
    if not args.execute:
        print(f"Plan only; add --execute to run all {len(stages)} restart-safe stages.", flush=True)
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
        "All requested direct-event tensor shards completed; no intermediate 1s or daily table was persisted.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
