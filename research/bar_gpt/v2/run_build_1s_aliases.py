from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from research.bar_gpt.v2.cohort import (
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
    BAR_GPT_SOURCE_ALIAS_TICKERS,
)


DEFAULT_RUNTIME_ROOT = r"D:\TradingML\runtimes\bar_gpt\v2\build_1s_identity_aliases"


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Build raw one-second rows for point-in-time source-ticker aliases.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="auto")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_args(argv)
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v2.build_1s",
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--tickers",
        ",".join(BAR_GPT_SOURCE_ALIAS_TICKERS),
        "--target-table",
        BAR_GPT_COHORT_2TB_TABLE,
        "--manifest-table",
        BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
        "--ticker-batch-max-events",
        "40000000",
        "--ticker-batch-max-tickers",
        "16",
        "--max-threads",
        "8",
        "--max-memory-usage",
        "48G",
        "--max-bytes-before-external-group-by",
        "12G",
        "--runtime-root",
        DEFAULT_RUNTIME_ROOT,
        "--progress-layout",
        args.progress_layout,
    ]
    if args.execute:
        command.append("--execute")
    command.extend(extra)
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.call(command, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
