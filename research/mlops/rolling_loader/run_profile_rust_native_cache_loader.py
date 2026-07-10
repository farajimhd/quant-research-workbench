from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.rolling_loader.daily_index_cache import DEFAULT_DAILY_INDEX_CACHE_ROOT
from research.mlops.rolling_loader.rust_chrono_loader import (
    RustNativeCacheProfileConfig,
    build_rust_library,
    profile_rust_native_cache,
    rust_library_path,
    rust_version,
)


DEFAULT_CACHE_ROOT = DEFAULT_DAILY_INDEX_CACHE_ROOT / "events_daily_index_2019-02"
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/rolling_loader/rust_native_cache_loader_profiles")
DEFAULT_BATCH_SIZE = 1024
DEFAULT_BATCHES = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the native Rust daily-index cache reader. This path opens "
            "the real parquet cache artifacts from Rust and validates/touches "
            "events, origins, sparse context, bars, scanner, and label sources."
        )
    )
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--month", default="2019-02")
    parser.add_argument(
        "--ticker-limit",
        type=int,
        default=0,
        help="Ticker packages to consider. 0 uses the resolved worker count for the one-experiment profile.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--batches", type=int, default=DEFAULT_BATCHES)
    parser.add_argument("--event-stream-len", type=int, default=1024)
    parser.add_argument("--read-workers", type=int, default=0, help="Rust package-reader workers. 0 uses os.cpu_count().")
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = not bool(args.debug)
    if not bool(args.no_build) and not rust_library_path(release=release).exists():
        build_rust_library(release=release)
    run_dir = Path(args.output_root) / time.strftime("rust_native_cache_loader_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / "rust_native_cache_loader_profile.jsonl"
    summary_path = run_dir / "rust_native_cache_loader_summary.json"
    resolved_read_workers = _resolve_read_workers(int(args.read_workers))
    resolved_ticker_limit = _resolve_ticker_limit(int(args.ticker_limit), resolved_read_workers)
    config = RustNativeCacheProfileConfig(
        cache_root=Path(args.cache_root),
        month=str(args.month),
        ticker_limit=resolved_ticker_limit,
        batch_size=int(args.batch_size),
        max_batches=int(args.batches),
        event_stream_len=int(args.event_stream_len),
        read_workers=resolved_read_workers,
        strict=bool(args.strict),
    )
    header = {
        "profile_goal": "single_experiment_batch_size_1024",
        "cache_root": str(config.cache_root),
        "month": str(config.month),
        "ticker_limit_requested": int(args.ticker_limit),
        "ticker_limit": int(config.ticker_limit),
        "batch_size": int(config.batch_size),
        "batches": int(config.max_batches),
        "event_stream_len": int(config.event_stream_len),
        "read_workers_requested": int(args.read_workers),
        "read_workers": int(config.read_workers),
        "strict": bool(config.strict),
        "library": str(rust_library_path(release=release)),
        "version": rust_version(),
    }
    print(f"RUST NATIVE CACHE LOADER PROFILE {run_dir}", flush=True)
    print(json.dumps(header, sort_keys=True), flush=True)
    started = time.perf_counter()
    try:
        stats = profile_rust_native_cache(config, build_if_missing=not bool(args.no_build), release=release)
    except KeyboardInterrupt:
        print("Interrupted; stopping native Rust cache profile.", flush=True)
        return 130
    row = {
        "utc": _now_iso(),
        "wall_seconds": float(time.perf_counter() - started),
        "config": asdict(config) | {"cache_root": str(config.cache_root)},
        "stats": stats.to_dict(),
    }
    _append_jsonl(jsonl_path, row)
    summary = {"header": header, "stats": stats.to_dict(), "status": "ok" if int(stats.status) == 0 else "error"}
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary["stats"], sort_keys=True), flush=True)
    print(
        "NATIVE_THROUGHPUT "
        f"packages={stats.packages_processed:,}/{stats.packages_discovered:,} "
        f"parts={stats.parts_processed:,} samples={stats.samples:,} batches={stats.batches:,} "
        f"elapsed={stats.elapsed_seconds:.3f}s samples/s={stats.samples_per_second:,.1f} "
        f"parquet_files={stats.parquet_files_opened:,} rows_seen={stats.parquet_rows_seen:,} "
        f"event_rows={stats.event_rows:,} origin_rows={stats.origin_rows:,} "
        f"text_selected={stats.text_selected:,} xbrl_selected={stats.xbrl_selected:,} "
        f"corporate_selected={stats.corporate_action_selected:,} scanner_rows={stats.scanner_rows:,} "
        f"invalid={stats.invalid_event_windows:,} ordinal_mismatch={stats.ordinal_mismatches:,} "
        f"schema_errors={stats.schema_errors:,} io_errors={stats.io_errors:,}",
        flush=True,
    )
    return 0


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_read_workers(value: int) -> int:
    if value > 0:
        return value
    return max(1, int(os.cpu_count() or 1))


def _resolve_ticker_limit(value: int, read_workers: int) -> int:
    if value > 0:
        return value
    return max(1, int(read_workers))


if __name__ == "__main__":
    raise SystemExit(main())
