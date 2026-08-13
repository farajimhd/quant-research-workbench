from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v2.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE,
    BAR_GPT_SIP_DAILY_SESSION_TABLE,
)
from research.bar_gpt.v2.schema import FEATURE_VERSION, SCHEMA_VERSION


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Build condition-eligible BarGPT daily/session context authority.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--validate-sql", action="store_true")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2026-08-01")
    parser.add_argument("--tickers", default=",".join(BAR_GPT_COHORT_2TB))
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--target-table", default=BAR_GPT_SIP_DAILY_SESSION_TABLE)
    parser.add_argument("--manifest-table", default=BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    args, extra = parser.parse_known_args(argv)
    return args, extra


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    command = [
        sys.executable, "-B", "-m", "pipelines.market_sip.events.clickhouse_build_daily_session_bars",
        "--start-date", args.start_date,
        "--end-date", args.end_date,
        "--tickers", args.tickers,
        "--events-table-base", args.events_table_base,
        "--target-table", args.target_table,
        "--manifest-table", args.manifest_table,
        "--bar-gpt-condition-eligibility",
        "--schema-version", str(SCHEMA_VERSION),
        "--feature-version", FEATURE_VERSION,
        "--progress-layout", args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
    if args.validate_sql:
        command.append("--validate-sql")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    return subprocess.call(command, cwd=repo_root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


if __name__ == "__main__":
    raise SystemExit(main())
