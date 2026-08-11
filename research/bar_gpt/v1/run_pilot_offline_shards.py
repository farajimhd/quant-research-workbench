from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v1.audit_offline_shards import DEFAULT_PILOT_ROOT


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build direct-event BarGPT pilot shards and automatically run the fail-closed audit."
    )
    parser.add_argument("--execute", action="store_true", help="Required to build; omit for a safe plan.")
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default="2026-02-01")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    parser.add_argument("--max-shards", type=int, default=2)
    args = parser.parse_args(list(argv) if argv is not None else None)
    tickers = tuple(dict.fromkeys(item.strip().upper() for item in str(args.tickers).split(",") if item.strip()))
    if not tickers:
        parser.error("--tickers cannot be empty")
    if args.workers <= 0 or args.max_shards <= 0:
        parser.error("--workers and --max-shards must be positive")
    args.tickers = tickers
    return args


def commands(args: argparse.Namespace) -> tuple[tuple[str, list[str]], ...]:
    common = [
        "--output-root", str(args.output_root),
        "--tickers", ",".join(args.tickers),
        "--start-date", str(args.start_date),
        "--end-date", str(args.end_date),
    ]
    build = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_offline_shards",
        *common,
        "--selection", "all",
        "--source-mode", "direct_events",
        "--workers", str(args.workers),
        "--cpu-threads-per-worker", str(args.cpu_threads_per_worker),
        "--clickhouse-max-concurrent-pages", str(args.clickhouse_max_concurrent_pages),
        "--max-shards", str(args.max_shards),
    ]
    if args.execute:
        build.append("--execute")
    if args.force_rebuild:
        build.append("--force-rebuild")
    audit = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.audit_offline_shards",
        "--root", str(args.output_root),
        "--tickers", ",".join(args.tickers),
        "--start-date", str(args.start_date),
        "--end-date", str(args.end_date),
        "--max-shards", str(args.max_shards),
        "--verify-sha256",
        "--require-calendar-context",
        "--verify-direct-source",
    ]
    return (("direct event-to-shard pilot", build), ("automatic complete pilot audit", audit))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    stages = commands(args)
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Stage {index}/{len(stages)} - {label}: {subprocess.list2cmdline(command)}", flush=True)
    if not args.execute:
        print("Plan only; add --execute to build the pilot and run its audit.", flush=True)
        return 0
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for label, command in stages:
        status = subprocess.call(command, cwd=repo_root, env=environment)
        if status:
            raise RuntimeError(f"{label} failed with exit code {status}")
    print(f"Pilot build and automatic audit passed: {args.output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
