from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v1.audit_offline_shards import DEFAULT_PILOT_ROOT
from research.bar_gpt.v1.cohort import BAR_GPT_SOURCE_ALIAS_TICKERS


PILOT_ONE_SECOND_TABLE = "bar_gpt_1s_bars_v2_pilot"
PILOT_ONE_SECOND_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v2_pilot"
PILOT_DAILY_TABLE = "bar_gpt_daily_session_bars_v2_pilot"
PILOT_DAILY_MANIFEST_TABLE = "bar_gpt_daily_session_bars_manifest_v2_pilot"
PILOT_SOURCE_ALIAS_MANIFEST_TABLE = "bar_gpt_1s_source_alias_manifest_v2_pilot"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build bounded embedded-condition v7 pilot, 2026-context, and split-boundary shards."
    )
    parser.add_argument("--execute", action="store_true", help="Required to build; omit for a read-only plan.")
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Replace existing pilot shards when the model-ready shard contract changed.",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--context-check-ticker", default="AAPL")
    parser.add_argument("--context-check-start-date", default="2026-01-02")
    parser.add_argument("--context-check-end-date", default="2026-01-03")
    parser.add_argument("--split-check-start-date", default="2020-08-28")
    parser.add_argument("--split-check-end-date", default="2020-09-03")
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
    args.context_check_ticker = str(args.context_check_ticker).strip().upper()
    if args.context_check_ticker not in tickers:
        parser.error("--context-check-ticker must be one of the two pilot tickers")
    context_start = dt.date.fromisoformat(str(args.context_check_start_date))
    context_end = dt.date.fromisoformat(str(args.context_check_end_date))
    if context_start >= context_end:
        parser.error("the context-check date range must be non-empty")
    if context_start.strftime("%Y-%m") != (context_end - dt.timedelta(days=1)).strftime("%Y-%m"):
        parser.error("the context-check date range must stay within one month")
    args.tickers = tickers
    return args


def commands(args: argparse.Namespace) -> tuple[tuple[str, list[str]], ...]:
    requested_tickers = ",".join(args.tickers)
    source_tickers = ",".join(dict.fromkeys((*args.tickers, *BAR_GPT_SOURCE_ALIAS_TICKERS)))
    def one_second(start_date: str, end_date: str, tickers: tuple[str, ...]) -> list[str]:
        command = [
            sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_1s",
            "--start-date", start_date, "--end-date", end_date,
            "--tickers", ",".join(tickers),
            "--events-table-base", "events",
            "--target-table", PILOT_ONE_SECOND_TABLE,
            "--manifest-table", PILOT_ONE_SECOND_MANIFEST_TABLE,
        ]
        if args.execute:
            command.append("--execute")
        return command

    daily = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_daily",
        "--start-date", "2019-01-01", "--end-date", "2026-08-01",
        "--tickers", source_tickers,
        "--events-table-base", "events",
        "--target-table", PILOT_DAILY_TABLE,
        "--manifest-table", PILOT_DAILY_MANIFEST_TABLE,
    ]
    if args.execute:
        daily.append("--execute")

    aliases = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_1s_aliases",
        "--start-date", "2019-01-01", "--end-date", "2026-08-01",
        "--events-table-base", "events",
        "--target-table", PILOT_ONE_SECOND_TABLE,
        "--manifest-table", PILOT_SOURCE_ALIAS_MANIFEST_TABLE,
    ]
    if args.execute:
        aliases.append("--execute")

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
        "--one-second-table", PILOT_ONE_SECOND_TABLE,
        "--daily-table", PILOT_DAILY_TABLE,
    ]
    if args.execute:
        build.append("--execute")
    if args.force_rebuild:
        build.append("--force-rebuild")
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
    context_build = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.run_build_offline_shards",
        "--output-root",
        str(args.output_root),
        "--selection",
        "all",
        "--tickers",
        str(args.context_check_ticker),
        "--start-date",
        str(args.context_check_start_date),
        "--end-date",
        str(args.context_check_end_date),
        "--workers",
        "1",
        "--cpu-threads-per-worker",
        str(args.cpu_threads_per_worker),
        "--clickhouse-max-concurrent-pages",
        str(args.clickhouse_max_concurrent_pages),
        "--max-shards",
        "1",
        "--one-second-table", PILOT_ONE_SECOND_TABLE,
        "--daily-table", PILOT_DAILY_TABLE,
    ]
    if args.execute:
        context_build.append("--execute")
    if args.force_rebuild:
        context_build.append("--force-rebuild")
    context_day = dt.date.fromisoformat(str(args.context_check_start_date))
    context_month_start = context_day.replace(day=1)
    context_month_end = (context_month_start + dt.timedelta(days=32)).replace(day=1)
    context_audit = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.audit_offline_shards",
        "--root",
        str(args.output_root),
        "--tickers",
        str(args.context_check_ticker),
        "--start-date",
        context_month_start.isoformat(),
        "--end-date",
        context_month_end.isoformat(),
        "--max-shards",
        "1",
        "--verify-sha256",
        "--require-calendar-context",
    ]
    split_build = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_offline_shards",
        "--output-root", str(args.output_root), "--selection", "all", "--tickers", "AAPL",
        "--start-date", str(args.split_check_start_date), "--end-date", str(args.split_check_end_date),
        "--workers", "1", "--max-shards", "1",
        "--one-second-table", PILOT_ONE_SECOND_TABLE, "--daily-table", PILOT_DAILY_TABLE,
    ]
    if args.execute:
        split_build.append("--execute")
    if args.force_rebuild:
        split_build.append("--force-rebuild")
    split_audit = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.audit_offline_shards",
        "--root", str(args.output_root), "--tickers", "AAPL",
        "--start-date", "2020-08-01", "--end-date", "2020-10-01", "--max-shards", "1", "--verify-sha256",
    ]
    return (
        ("continuous pilot one-second context authority", one_second("2019-01-01", "2026-08-01", args.tickers)),
        ("point-in-time source alias pilot authority", aliases),
        ("pilot daily/calendar authority", daily),
        ("pilot shards", build), ("pilot audit", audit),
        ("2026 shard", context_build), ("2026 audit", context_audit),
        ("split-boundary shard", split_build), ("split-boundary audit", split_audit),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    stages = commands(args)
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Stage {index}/{len(stages)} - {label}: {subprocess.list2cmdline(command)}", flush=True)
    if not args.execute:
        print("Plan only; add --execute to build and audit the bounded pilot set.", flush=True)
        return 0
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for _label, command in stages:
        status = subprocess.call(command, cwd=repo_root, env=environment)
        if status:
            return int(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
