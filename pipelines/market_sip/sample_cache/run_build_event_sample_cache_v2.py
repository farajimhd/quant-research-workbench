from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
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
    # "future" means label-only. Store only the next two 128-event chunks as
    # y bytes; the rest of the fetched future span is used for future_* labels.
    "label_chunks": 2,
    # Fetch 2,048 events ending at the origin so non-future span labels/features
    # can be computed without lookahead. The stored x chunk remains the last
    # 128 events ending at the origin.
    "past_span_events": 2048,
    "future_span_events": 2048,
    # Args are GiB. 2720 + 64 = 2784 GiB, which is about 2.99 decimal TB on disk.
    "train_cache_gib": 2720,
    "validation_cache_gib": 64,
    "shard_size_gib": 16,
    # v2 writes x plus two future chunks and one labels parquet sidecar.
    "builder_micro_batch_samples": 8192,
    "origins_per_span": 128,
    "min_origin_stride": 1,
    "max_origin_stride": 16,
    "query_bundle_spans": 16,
    "workers": 8,
    "pending_multiplier": 1,
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
    parser.add_argument("--resume", action="store_true", help="Resume an existing --cache-id without overwriting finalized shards.")
    known, extra = parser.parse_known_args()
    argv = build_argv(DEFAULTS)
    if known.resume:
        argv.append("--resume")
    if known.dry_run:
        argv.append("--dry-run")
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(run_interruptible(argv, cwd=repo_root()))


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


def run_interruptible(command: list[str], *, cwd: Path) -> int:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(command, cwd=str(cwd), creationflags=creationflags)
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\nINTERRUPT: stopping active sample-cache builder subprocess...", flush=True)
        terminate_process(process)
        return 130


def terminate_process(process: subprocess.Popen[Any], *, grace_seconds: float = 10.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            process.terminate()
    else:
        process.terminate()
    deadline = time.monotonic() + grace_seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        print("INTERRUPT: subprocess did not stop gracefully; killing it.", flush=True)
        process.kill()
        process.wait()


if __name__ == "__main__":
    main()
