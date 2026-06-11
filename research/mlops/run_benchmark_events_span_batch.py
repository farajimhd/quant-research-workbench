from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "clickhouse_url": "http://localhost:18123",
    "database": "market_sip_compact",
    "events_table": "events",
    "continuity_table": "events_ordinal_continuity",
    "train_index_table": "train_2019_to_2025",
    "validation_index_table": "validation_2026",
    "index_source": "auto",
    "batch_sizes": "4096,8192,16384",
    "context_events": 128,
    "origins_per_span": 32,
    "min_origin_stride": 1,
    "max_origin_stride": 16,
    "query_bundle_spans": 64,
    "benchmark_batches": 5,
    "max_threads": 8,
    "max_memory_usage": "80G",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launcher for final events-table v4 span batch benchmark.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    argv = build_argv(DEFAULTS)
    if known.dry_run:
        argv.append("--dry-run")
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.mlops.clickhouse_benchmark_events_span_batch"]
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return argv


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
