from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import polars as pl

from research.mlops.rolling_loader.rust_chrono_loader import (
    RustRealCachePart,
    RustRealCacheRuntimeConfig,
    build_rust_library,
    profile_rust_real_cache_parts,
    rust_library_path,
    rust_version,
)


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/rolling_loader/rust_real_cache_loader_profiles")
EVENT_FEATURE_COLUMNS = (
    "event_meta",
    "timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the Rust rolling event cache with real daily-index parquet data.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--month", default="2019-02")
    parser.add_argument("--ticker", action="append", default=[], help="Ticker to include. Repeatable. Default discovers tickers.")
    parser.add_argument("--ticker-limit", type=int, default=64)
    parser.add_argument("--parts-per-ticker", type=int, default=1)
    parser.add_argument("--max-parts", type=int, default=64)
    parser.add_argument("--origin-limit-per-part", type=int, default=0, help="0 means all origins in each selected part.")
    parser.add_argument("--read-workers", type=int, default=8)
    parser.add_argument("--event-stream-len", type=int, default=1_024)
    parser.add_argument("--batch-size", type=int, default=1_024)
    parser.add_argument("--realtime-process-workers", type=int, default=32)
    parser.add_argument("--prefetch-process-workers", type=int, default=16)
    parser.add_argument("--prefetch-fraction", type=float, default=0.33)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = not bool(args.debug)
    if not bool(args.no_build) and not rust_library_path(release=release).exists():
        build_rust_library(release=release)
    run_dir = Path(args.output_root) / time.strftime("rust_real_cache_loader_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"RUST REAL CACHE LOADER PROFILE {run_dir}", flush=True)
    discover_started = time.perf_counter()
    specs = discover_event_part_specs(
        cache_root=Path(args.cache_root),
        month=str(args.month),
        tickers=[str(t).upper() for t in args.ticker],
        ticker_limit=int(args.ticker_limit),
        parts_per_ticker=int(args.parts_per_ticker),
        max_parts=int(args.max_parts),
    )
    discover_seconds = time.perf_counter() - discover_started
    if not specs:
        raise RuntimeError(f"No event/origin parts discovered under {args.cache_root} for month={args.month}.")
    prefetch_start = max(0, int(round(len(specs) * (1.0 - float(args.prefetch_fraction)))))
    for index, spec in enumerate(specs):
        spec["priority"] = 1 if index >= prefetch_start else 0
    header = {
        "cache_root": str(args.cache_root),
        "month": str(args.month),
        "selected_parts": len(specs),
        "selected_tickers": len({spec["ticker"] for spec in specs}),
        "feature_columns": EVENT_FEATURE_COLUMNS,
        "event_stream_len": int(args.event_stream_len),
        "batch_size": int(args.batch_size),
        "read_workers": int(args.read_workers),
        "realtime_process_workers": int(args.realtime_process_workers),
        "prefetch_process_workers": int(args.prefetch_process_workers),
        "prefetch_fraction": float(args.prefetch_fraction),
        "library": str(rust_library_path(release=release)),
        "version": rust_version(),
    }
    print(json.dumps(header, sort_keys=True), flush=True)
    read_started = time.perf_counter()
    parts, read_rows = read_real_parts(
        specs,
        cache_root=Path(args.cache_root),
        origin_limit_per_part=int(args.origin_limit_per_part),
        read_workers=int(args.read_workers),
    )
    read_seconds = time.perf_counter() - read_started
    profile_config = RustRealCacheRuntimeConfig(
        part_count=len(parts),
        event_stream_len=int(args.event_stream_len),
        batch_size=int(args.batch_size),
        realtime_process_workers=int(args.realtime_process_workers),
        prefetch_process_workers=int(args.prefetch_process_workers),
    )
    rust_started = time.perf_counter()
    stats = profile_rust_real_cache_parts(parts, profile_config, build_if_missing=not bool(args.no_build), release=release)
    rust_wall_seconds = time.perf_counter() - rust_started
    payload = {
        "header": header,
        "config": asdict(profile_config),
        "discover_seconds": float(discover_seconds),
        "read_pack_seconds": float(read_seconds),
        "rust_wall_seconds": float(rust_wall_seconds),
        "read_rows": read_rows,
        "stats": stats.to_dict(),
        "parts": [part_summary(spec) for spec in specs],
    }
    (run_dir / "rust_real_cache_loader_profile.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(payload["stats"], sort_keys=True), flush=True)
    print(
        "REAL_THROUGHPUT "
        f"parts={stats.parts:,} "
        f"event_rows={stats.event_rows:,} "
        f"origins={stats.origins_seen:,} "
        f"samples={stats.samples:,} "
        f"valid={stats.valid_origin_fraction:.4f} "
        f"read_pack={read_seconds:.3f}s "
        f"rust={stats.elapsed_seconds:.3f}s "
        f"samples/s={stats.samples_per_second:,.1f} "
        f"batches/s={stats.batches_per_second:,.2f} "
        f"input={stats.input_gib:.2f}GiB "
        f"invalid={stats.invalid_origins:,} "
        f"mismatch={stats.ordinal_mismatches:,}",
        flush=True,
    )
    return 0


def discover_event_part_specs(
    *,
    cache_root: Path,
    month: str,
    tickers: list[str],
    ticker_limit: int,
    parts_per_ticker: int,
    max_parts: int,
) -> list[dict[str, Any]]:
    month_dir = cache_root / f"month={month}"
    if not month_dir.exists():
        raise FileNotFoundError(f"Missing cache month directory: {month_dir}")
    ticker_dirs = [month_dir / f"ticker={ticker}" for ticker in tickers] if tickers else sorted(month_dir.glob("ticker=*"))
    if ticker_limit > 0:
        ticker_dirs = ticker_dirs[: int(ticker_limit)]
    specs: list[dict[str, Any]] = []
    for ticker_dir in ticker_dirs:
        manifest_path = ticker_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        parts = [
            part
            for part in manifest.get("parts", [])
            if str(part.get("modality") or "").lower() == "events"
            and int(part.get("event_rows") or 0) > 0
            and int(part.get("origin_rows") or 0) > 0
            and part.get("event_path")
            and part.get("origin_path")
        ]
        parts.sort(key=lambda part: (int(part.get("timestamp_min_us") or 0), int(part.get("part_id") or 0)))
        if parts_per_ticker > 0:
            parts = parts[: int(parts_per_ticker)]
        for part in parts:
            specs.append(
                {
                    "ticker": str(part.get("ticker") or ticker_dir.name.removeprefix("ticker=")),
                    "ticker_id": 0,
                    "part_id": int(part.get("part_id") or 0),
                    "event_path": str(part.get("event_path")),
                    "origin_path": str(part.get("origin_path")),
                    "event_rows_manifest": int(part.get("event_rows") or 0),
                    "origin_rows_manifest": int(part.get("origin_rows") or 0),
                    "ordinal_min": int(part.get("ordinal_min") or 0),
                    "ordinal_max": int(part.get("ordinal_max") or 0),
                    "timestamp_min_us": int(part.get("timestamp_min_us") or 0),
                    "timestamp_max_us": int(part.get("timestamp_max_us") or 0),
                }
            )
            if max_parts > 0 and len(specs) >= int(max_parts):
                return specs
    return specs


def read_real_parts(
    specs: list[dict[str, Any]],
    *,
    cache_root: Path,
    origin_limit_per_part: int,
    read_workers: int,
) -> tuple[list[RustRealCachePart], dict[str, int]]:
    parts: list[RustRealCachePart] = []
    read_rows = {"event_rows": 0, "origin_rows": 0}
    with ThreadPoolExecutor(max_workers=max(1, int(read_workers))) as pool:
        futures = [pool.submit(read_one_part, spec, cache_root, int(origin_limit_per_part)) for spec in specs]
        for index, future in enumerate(as_completed(futures), start=1):
            part, row_counts = future.result()
            parts.append(part)
            read_rows["event_rows"] += int(row_counts["event_rows"])
            read_rows["origin_rows"] += int(row_counts["origin_rows"])
            if index == 1 or index % 8 == 0 or index == len(futures):
                print(
                    f"READ {index:,}/{len(futures):,} "
                    f"event_rows={read_rows['event_rows']:,} origin_rows={read_rows['origin_rows']:,}",
                    flush=True,
                )
    return parts, read_rows


def read_one_part(spec: dict[str, Any], cache_root: Path, origin_limit_per_part: int) -> tuple[RustRealCachePart, dict[str, int]]:
    event_path = cache_root / str(spec["event_path"])
    origin_path = cache_root / str(spec["origin_path"])
    event_columns = ["ticker_id", "ordinal", *EVENT_FEATURE_COLUMNS]
    events = pl.read_parquet(event_path, columns=event_columns)
    origins = pl.read_parquet(origin_path, columns=["origin_ordinal", "event_row_offset"])
    if origin_limit_per_part > 0 and origins.height > origin_limit_per_part:
        origins = origins.head(int(origin_limit_per_part))
    feature_frame = events.select([pl.col(column).cast(pl.Float32).alias(column) for column in EVENT_FEATURE_COLUMNS])
    features = np.ascontiguousarray(feature_frame.to_numpy(), dtype=np.float32)
    ordinals = np.ascontiguousarray(events.get_column("ordinal").to_numpy(), dtype=np.uint64)
    origin_offsets = np.ascontiguousarray(origins.get_column("event_row_offset").to_numpy(), dtype=np.int64)
    origin_ordinals = np.ascontiguousarray(origins.get_column("origin_ordinal").to_numpy(), dtype=np.uint64)
    ticker_id = int(events.get_column("ticker_id")[0]) if events.height else 0
    part = RustRealCachePart(
        ticker_id=ticker_id,
        ordinals=ordinals,
        features=features,
        origin_offsets=origin_offsets,
        origin_ordinals=origin_ordinals,
        priority=int(spec.get("priority") or 0),
        label=f"{spec.get('ticker')}|{spec.get('part_id')}",
    )
    return part, {"event_rows": int(events.height), "origin_rows": int(origins.height)}


def part_summary(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": spec.get("ticker"),
        "part_id": spec.get("part_id"),
        "priority": "prefetch" if int(spec.get("priority") or 0) else "realtime",
        "event_rows_manifest": spec.get("event_rows_manifest"),
        "origin_rows_manifest": spec.get("origin_rows_manifest"),
        "ordinal_min": spec.get("ordinal_min"),
        "ordinal_max": spec.get("ordinal_max"),
        "timestamp_min_us": spec.get("timestamp_min_us"),
        "timestamp_max_us": spec.get("timestamp_max_us"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
