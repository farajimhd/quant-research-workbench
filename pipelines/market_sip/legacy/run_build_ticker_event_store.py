from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "flatfiles_root": r"D:\market-data\flatfiles\us_stocks_sip",
    "output_root": r"D:\market-data\prepared\us_stocks_sip\ticker_compact_events_v1",
    "reference_dir": None,
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "tickers": "ALL",
    "bucket_count": 1024,
    "fragment_bucket_count": 128,
    "derive_batch_rows": 500_000,
    "max_rows_per_fragment_file": 2_000_000,
    "processes": 48,
    "derive_processes": 32,
    "compact_processes": 24,
    "index_processes": 16,
    "polars_threads_per_process": 2,
    "max_pending": 0,
    "max_tasks_per_worker": 1,
    "heartbeat_seconds": 30,
    "keep_temp": True,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch ticker compact event store builder with editable defaults.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--stage", choices=("all", "derive", "compact", "index"), default="")
    known, extra = parser.parse_known_args()
    defaults = dict(DEFAULTS)
    defaults["reference_dir"] = str(repo_root() / "research" / "market_references" / "massive")
    if known.rebuild:
        defaults["rebuild"] = True
    if known.stage:
        defaults["stage"] = known.stage
    argv = build_argv(defaults)
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "pipelines.market_sip.legacy.build_ticker_event_store"]
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        elif value is not None:
            argv.extend([flag, str(value)])
    return argv


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
