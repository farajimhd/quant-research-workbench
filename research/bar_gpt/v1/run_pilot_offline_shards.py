from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v1.audit_offline_shards import DEFAULT_PILOT_ROOT
from research.bar_gpt.v1.config import DataConfig


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build direct-event BarGPT pilot shards and automatically run the fail-closed audit."
    )
    parser.add_argument("--execute", action="store_true", help="Required to build; omit for a safe plan.")
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PILOT_ROOT)
    # AMC and GME both have confirmed LULD-category authority rows on
    # 2021-01-28.  These defaults therefore exercise positive conditions
    # instead of merely proving the all-zero path.
    parser.add_argument("--tickers", default="AMC,GME")
    parser.add_argument("--start-date", default="2021-01-25")
    parser.add_argument("--end-date", default="2021-02-01")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-threads-per-query", type=int, default=2)
    parser.add_argument("--clickhouse-prefetch-pages", type=int, default=16)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=32)
    parser.add_argument("--max-shards", type=int, default=2)
    parser.add_argument("--samples-per-shard", type=int, default=1)
    parser.add_argument("--clickhouse-audit-samples", type=int, default=2)
    parser.add_argument("--audit-seed", type=int, default=17)
    parser.add_argument("--progress-layout", choices=("rich", "text"), default="rich")
    args = parser.parse_args(list(argv) if argv is not None else None)
    tickers = tuple(dict.fromkeys(item.strip().upper() for item in str(args.tickers).split(",") if item.strip()))
    if not tickers:
        parser.error("--tickers cannot be empty")
    if (
        args.workers <= 0
        or args.max_shards <= 0
        or args.samples_per_shard <= 0
        or args.clickhouse_audit_samples <= 0
        or args.clickhouse_max_threads_per_query <= 0
    ):
        parser.error("worker, shard, sample, and ClickHouse thread counts must be positive")
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
        "--clickhouse-max-threads-per-worker", str(args.clickhouse_max_threads_per_query),
        "--clickhouse-prefetch-pages", str(args.clickhouse_prefetch_pages),
        "--clickhouse-max-concurrent-pages", str(args.clickhouse_max_concurrent_pages),
        "--progress-layout", str(args.progress_layout),
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
        "--verify-direct-source",
    ]
    # The first authority month intentionally contains zero-filled masked
    # calendar prefixes. Later pilots must prove complete calendar warm-up.
    if str(args.start_date) != str(DataConfig().daily_history_start_date):
        audit.append("--require-calendar-context")
    sampled_audit = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_audit_shard_data",
        "--root", str(args.output_root),
        "--output-root", str(args.output_root / "manifest" / "sample_audits"),
        "--tickers", ",".join(args.tickers),
        "--max-shards", str(args.max_shards),
        "--samples-per-shard", str(args.samples_per_shard),
        "--clickhouse-samples", str(args.clickhouse_audit_samples),
        "--clickhouse-prefetch-pages", str(args.clickhouse_prefetch_pages),
        "--clickhouse-max-threads-per-query", str(args.clickhouse_max_threads_per_query),
        "--seed", str(args.audit_seed),
        "--verify-sha256",
    ]
    return (
        ("direct event-to-shard pilot", build),
        ("automatic complete pilot audit", audit),
        ("sampled ClickHouse tensor reconstruction audit", sampled_audit),
    )


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
