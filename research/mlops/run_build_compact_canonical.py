from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "flatfiles_root": r"D:\market-data\flatfiles\us_stocks_sip",
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_compact_v1",
    "issue_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_compact_v1_issues",
    "temp_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\_tmp_compact_canonical_parts",
    "start_date": "2025-11-01",
    "end_date": "2025-12-05",
    "tickers": "ALL",
    "processes": 16,
    "normalize_processes": 16,
    "merge_processes": 16,
    "polars_threads_per_process": 2,
    "heartbeat_seconds": 30,
    "rebuild": False,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch compact canonical preprocessing with editable defaults.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    known, extra = parser.parse_known_args()
    defaults = dict(DEFAULTS)
    if known.rebuild:
        defaults["rebuild"] = True
    argv = build_argv(defaults)
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.mlops.build_compact_canonical"]
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
