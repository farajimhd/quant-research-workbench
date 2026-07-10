from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.rolling_loader.rust_chrono_loader import (
    RustQueueRuntimeConfig,
    build_rust_library,
    profile_rust_queue_runtime,
    rust_library_path,
    rust_version,
)


DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/rolling_loader/rust_chrono_loader_profiles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the Rust chronological rolling-loader queue/cache runtime.")
    parser.add_argument("--ticker-count", type=int, default=2_000)
    parser.add_argument("--prefetch-ticker-count", type=int, default=2_000)
    parser.add_argument("--origins-per-ticker", type=int, default=512)
    parser.add_argument("--event-stream-len", type=int, default=1_024)
    parser.add_argument("--event-feature-count", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=1_024)
    parser.add_argument("--realtime-read-workers", type=int, default=16)
    parser.add_argument("--prefetch-read-workers", type=int, default=16)
    parser.add_argument("--realtime-process-workers", type=int, default=16)
    parser.add_argument("--prefetch-process-workers", type=int, default=16)
    parser.add_argument("--read-sleep-us", type=int, default=0)
    parser.add_argument("--process-sleep-us", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-build", action="store_true", help="Do not build the Rust library if it is missing.")
    parser.add_argument("--debug", action="store_true", help="Use debug Rust build instead of release.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = not bool(args.debug)
    if not bool(args.no_build) and not rust_library_path(release=release).exists():
        build_rust_library(release=release)
    config = RustQueueRuntimeConfig(
        ticker_count=int(args.ticker_count),
        origins_per_ticker=int(args.origins_per_ticker),
        event_stream_len=int(args.event_stream_len),
        event_feature_count=int(args.event_feature_count),
        batch_size=int(args.batch_size),
        realtime_read_workers=int(args.realtime_read_workers),
        prefetch_read_workers=int(args.prefetch_read_workers),
        realtime_process_workers=int(args.realtime_process_workers),
        prefetch_process_workers=int(args.prefetch_process_workers),
        prefetch_ticker_count=int(args.prefetch_ticker_count),
        read_sleep_us=int(args.read_sleep_us),
        process_sleep_us=int(args.process_sleep_us),
    )
    run_dir = Path(args.output_root) / time.strftime("rust_chrono_loader_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"RUST CHRONO LOADER PROFILE {run_dir}", flush=True)
    print(json.dumps({"config": asdict(config), "library": str(rust_library_path(release=release)), "version": rust_version()}, sort_keys=True), flush=True)
    started = time.perf_counter()
    stats = profile_rust_queue_runtime(config, build_if_missing=not bool(args.no_build), release=release)
    elapsed = time.perf_counter() - started
    payload = {
        "config": asdict(config),
        "stats": stats.to_dict(),
        "python_wall_seconds": float(elapsed),
        "library": str(rust_library_path(release=release)),
        "version": rust_version(),
    }
    (run_dir / "rust_chrono_loader_profile.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(payload["stats"], sort_keys=True), flush=True)
    print(
        "THROUGHPUT "
        f"samples/s={stats.samples_per_second:,.1f} "
        f"batches/s={stats.batches_per_second:,.2f} "
        f"cache_tickers={stats.cache_tickers:,} "
        f"read_steals={stats.read_priority_steals:,} "
        f"process_steals={stats.process_priority_steals:,} "
        f"allocated={stats.allocated_gib:.2f}GiB",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
