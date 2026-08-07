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
        description="Build two isolated BarGPT pilot shards and fail-closed audit both outputs."
    )
    parser.add_argument("--execute", action="store_true", help="Required to build; omit for a read-only plan.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    if len(tickers) != 2:
        parser.error("the pilot requires exactly two distinct tickers")
    if len(set(tickers)) != 2:
        parser.error("the pilot tickers must be distinct")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    args.tickers = tickers
    return args


def commands(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    build = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.run_build_offline_shards",
        "--output-root",
        str(args.output_root),
        "--selection",
        "all",
        "--tickers",
        ",".join(args.tickers),
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--workers",
        str(args.workers),
        "--cpu-threads-per-worker",
        str(args.cpu_threads_per_worker),
        "--clickhouse-max-concurrent-pages",
        str(args.clickhouse_max_concurrent_pages),
        "--max-shards",
        "2",
    ]
    if args.execute:
        build.append("--execute")
    audit = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.audit_offline_shards",
        "--root",
        str(args.output_root),
        "--tickers",
        ",".join(args.tickers),
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--max-shards",
        "2",
        "--verify-sha256",
    ]
    return build, audit


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    build, audit = commands(args)
    print("Pilot build command:", subprocess.list2cmdline(build), flush=True)
    print("Pilot audit command:", subprocess.list2cmdline(audit), flush=True)
    if not args.execute:
        print("Plan only; add --execute to build and audit two isolated shards.", flush=True)
        return 0
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    status = subprocess.call(build, cwd=repo_root, env=environment)
    if status:
        return int(status)
    return int(subprocess.call(audit, cwd=repo_root, env=environment))


if __name__ == "__main__":
    raise SystemExit(main())
