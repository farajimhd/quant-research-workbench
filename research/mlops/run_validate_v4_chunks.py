from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "prepared_root": r"D:\market-data\prepared\us_stocks_sip\v4_compact_event_chunks_v1",
    "reference_dir": None,
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "events_per_chunk": 128,
    "stride_events": 1,
    "mode": "all",
    "sample_chunks": 1000,
    "boundary_sample_chunks": 1000,
    "issue_limit": 10000,
    "event_cache_size": 8,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch v4 compact chunk dataset validation with editable defaults.")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    defaults = dict(DEFAULTS)
    defaults["reference_dir"] = str(repo_root() / "research" / "market_references" / "massive")
    argv = build_argv(defaults)
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.mlops.validate_v4_chunk_dataset"]
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
