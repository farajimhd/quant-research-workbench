from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache",
    "splits": "train,validation",
    "sample_record_checks": 256,
    "audit_clickhouse_checks": 25,
    "clickhouse_url": "",
    "database": "market_sip_compact",
    "events_table": "events",
    "max_threads": 8,
    "max_memory_usage": "80G",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launcher for compact event sample-cache validation.")
    parser.add_argument("--verify-sha256", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    argv = build_argv(DEFAULTS)
    if known.verify_sha256:
        argv.append("--verify-sha256")
    if known.allow_partial:
        argv.append("--allow-partial")
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.mlops.validate_event_sample_cache"]
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        elif value is None or value == "":
            continue
        else:
            argv.extend([flag, str(value)])
    return argv


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
