from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "clickhouse_url": "",
    "database": "market_sip_compact",
    "events_table": "events",
    "train_index_table": "train_2019_to_2025",
    "validation_index_table": "validation_2026",
    "cache_root": r"D:\market-data\prepared\event_sample_cache",
    "cache_version": 2,
    "label_chunks": 8,
    "train_cache_gib": 128,
    "validation_cache_gib": 4,
    "shard_size_gib": 16,
    "builder_micro_batch_samples": 65536,
    "origins_per_span": 512,
    "min_origin_stride": 1,
    "max_origin_stride": 16,
    "query_bundle_spans": 64,
    "workers": 8,
    "eta_recent_window": 50,
    "heartbeat_seconds": 30,
    "clickhouse_max_threads": 8,
    "clickhouse_max_memory_usage": "80G",
    "audit_samples_per_split": 256,
    "tickers": "ALL",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launcher for labeled compact event sample-cache v2 build.")
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
    argv = [sys.executable, "-m", "pipelines.market_sip.sample_cache.build_event_sample_cache"]
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
