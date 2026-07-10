from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.rolling_loader.daily_index_cache import DEFAULT_DAILY_INDEX_CACHE_ROOT, EVENT_PAYLOAD_COLUMNS, EVENT_TIME_FEATURE_COLUMNS
from research.mlops.rolling_loader.daily_index_dataset import DailyIndexLoaderConfig, DailyIndexTrainingBatch, DEFAULT_INTRADAY_LABEL_HORIZONS
from research.mlops.rolling_loader.rust_chrono_loader import RustTensorAssemblyConfig, assemble_tensors_with_rust, build_rust_library, rust_library_path, rust_version


DEFAULT_CACHE_ROOT = DEFAULT_DAILY_INDEX_CACHE_ROOT / "events_daily_index_2019-02"
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/rolling_loader/rust_full_batch_assembly_profiles")
DEFAULT_PROFILE_DATA_GROUPS = (
    "events",
    "intraday_labels",
    "corporate_action_labels",
    "intraday_bars",
    "scanner_context",
    "daily_bars",
    "global_daily_bars",
    "ticker_news_embeddings",
    "market_news_embeddings",
    "sec_filing_embeddings",
    "xbrl",
    "corporate_actions",
)
DEFAULT_PROFILE_EVENT_COLUMNS = tuple(
    column
    for column in (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS)
    if column not in {"ticker_id", "ticker", "ordinal", "timestamp_us"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile Rust assembly of complete daily-index training batches.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--training-days", default="")
    parser.add_argument("--data-groups", default=",".join(DEFAULT_PROFILE_DATA_GROUPS))
    parser.add_argument("--batch-sizes", default="1024,2048")
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--warmup-batches", type=int, default=1)
    parser.add_argument("--read-workers", type=int, default=8)
    parser.add_argument("--materialize-workers", type=int, default=16)
    parser.add_argument("--rust-realtime-workers", type=int, default=32)
    parser.add_argument("--rust-prefetch-workers", type=int, default=16)
    parser.add_argument("--time-window-seconds", type=float, default=60.0)
    parser.add_argument("--frontier-max-origins-per-window", type=int, default=0)
    parser.add_argument("--ticker-cache-capacity", type=int, default=15_000)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=1024)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = not bool(args.debug)
    if not bool(args.no_build) and not rust_library_path(release=release).exists():
        build_rust_library(release=release)
    run_dir = Path(args.output_root) / time.strftime("rust_full_batch_assembly_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / "rust_full_batch_assembly_profile.jsonl"
    summary_path = run_dir / "rust_full_batch_assembly_summary.json"
    batch_sizes = tuple(int(value) for value in _split_csv(args.batch_sizes))
    header = {
        "cache_root": str(args.cache_root),
        "months": list(_split_csv(args.months)),
        "tickers": list(_split_csv(args.tickers)),
        "training_days": list(_split_csv(args.training_days)),
        "data_groups": list(_split_csv(args.data_groups)),
        "batch_sizes": list(batch_sizes),
        "batches": int(args.batches),
        "warmup_batches": int(args.warmup_batches),
        "rust_realtime_workers": int(args.rust_realtime_workers),
        "rust_prefetch_workers": int(args.rust_prefetch_workers),
        "library": str(rust_library_path(release=release)),
        "version": rust_version(),
    }
    print(f"RUST FULL BATCH ASSEMBLY PROFILE {run_dir}", flush=True)
    print(json.dumps(header, sort_keys=True), flush=True)
    all_rows: list[dict[str, Any]] = []
    try:
        for batch_size in batch_sizes:
            rows = profile_batch_size(args, batch_size=batch_size, jsonl_path=jsonl_path)
            all_rows.extend(rows)
    except KeyboardInterrupt:
        print("Interrupted; stopping profiler gracefully.", flush=True)
        return 130
    summary = summarize(header, all_rows)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, sort_keys=True), flush=True)
    return 0


def profile_batch_size(args: argparse.Namespace, *, batch_size: int, jsonl_path: Path) -> list[dict[str, Any]]:
    from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader

    config = DailyIndexLoaderConfig(
        cache_root=Path(args.cache_root),
        months=_split_csv(args.months),
        tickers=_split_csv(args.tickers),
        days=_split_csv(args.training_days),
        batch_size=int(batch_size),
        data_groups=_split_csv(args.data_groups),
        event_columns=DEFAULT_PROFILE_EVENT_COLUMNS,
        intraday_label_horizons=DEFAULT_INTRADAY_LABEL_HORIZONS,
        read_workers=int(args.read_workers),
        materialize_workers=int(args.materialize_workers),
        chronological_replay=True,
        time_window_seconds=float(args.time_window_seconds),
        frontier_max_origins_per_window=int(args.frontier_max_origins_per_window),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        shuffle_parts=False,
        shuffle_within_loaded_group=False,
        strict_audit=True,
        preserve_batch_order=True,
    )
    loader = AsyncDailyIndexBatchLoader(config)
    rows: list[dict[str, Any]] = []
    iterator = loader.iter_batches()
    rust_config = RustTensorAssemblyConfig(
        realtime_workers=int(args.rust_realtime_workers),
        prefetch_workers=int(args.rust_prefetch_workers),
    )
    started = time.perf_counter()
    try:
        for batch_index in range(1, int(args.warmup_batches) + int(args.batches) + 1):
            phase = "warmup" if batch_index <= int(args.warmup_batches) else "measure"
            next_started = time.perf_counter()
            batch = next(iterator)
            next_seconds = time.perf_counter() - next_started
            tree = _batch_tensor_tree(batch)
            rust_started = time.perf_counter()
            result = assemble_tensors_with_rust(tree, rust_config, verify=bool(args.verify))
            rust_wall_seconds = time.perf_counter() - rust_started
            stats = result.stats.to_dict()
            row = {
                "utc": _now_iso(),
                "phase": phase,
                "batch_size": int(batch_size),
                "batch": int(batch_index),
                "samples": int(batch.sample_count),
                "next_batch_seconds": float(next_seconds),
                "rust_wall_seconds": float(rust_wall_seconds),
                "tensor_count": int(stats["tensors"]),
                "bytes_copied": int(stats["bytes_copied"]),
                "gib_copied": float(stats["gib_copied"]),
                "rust_gib_per_second": float(stats["gib_per_second"]),
                "rust_elapsed_seconds": float(stats["elapsed_seconds"]),
                "rust_worker_seconds": float(stats["worker_seconds"]),
                "rust_priority_steals": int(stats["priority_steals"]),
                "rust_invalid_specs": int(stats["invalid_specs"]),
                "source_part_keys": int(len(set(str(value) for value in np.asarray(batch.source_part_key).astype(str, copy=False)))),
                "unique_tickers": int(len(set(str(value) for value in np.asarray(batch.ticker).astype(str, copy=False)))),
                "elapsed_seconds": float(time.perf_counter() - started),
            }
            row.update({f"profile/{key}": value for key, value in batch.profile.items() if isinstance(value, (int, float, bool))})
            _append_jsonl(jsonl_path, row)
            rows.append(row)
            print(
                "BATCH "
                f"bs={batch_size} phase={phase} batch={batch_index} samples={batch.sample_count:,} "
                f"loader={next_seconds:.3f}s rust={rust_wall_seconds:.3f}s "
                f"copy={stats['gib_copied']:.3f}GiB rate={stats['gib_per_second']:.2f}GiB/s "
                f"tensors={stats['tensors']}",
                flush=True,
            )
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()
    return rows


def _batch_tensor_tree(batch: DailyIndexTrainingBatch) -> dict[str, Any]:
    return {
        "identity": {
            "origin_ordinal": np.asarray(batch.origin_ordinal),
            "origin_timestamp_us": np.asarray(batch.origin_timestamp_us),
        },
        "x": {
            "raw_event_stream": np.asarray(batch.raw_event_stream),
            "raw_event_mask": np.asarray(batch.raw_event_mask),
            "text_inputs": batch.text_inputs,
            "xbrl_inputs": batch.xbrl_inputs,
            "corporate_action_inputs": batch.corporate_action_inputs,
            "bar_inputs": batch.bar_inputs,
            "scanner_inputs": batch.scanner_inputs,
            "input_availability": batch.input_availability,
        },
        "y": {
            "future_bar_values": batch.future_bar_values,
            "future_bar_masks": batch.future_bar_masks,
            "intraday_labels": batch.intraday_labels,
            "corporate_action_labels": batch.corporate_action_labels,
            "future_intraday_bars": np.asarray(batch.future_intraday_bars),
            "future_intraday_bar_mask": np.asarray(batch.future_intraday_bar_mask),
        },
    }


def summarize(header: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row.get("phase") == "measure"]
    by_batch_size: dict[str, Any] = {}
    for batch_size in sorted({int(row["batch_size"]) for row in measured}):
        group = [row for row in measured if int(row["batch_size"]) == batch_size]
        by_batch_size[str(batch_size)] = {
            "batches": len(group),
            "samples": sum(int(row["samples"]) for row in group),
            "avg_loader_seconds": _mean(row["next_batch_seconds"] for row in group),
            "avg_rust_wall_seconds": _mean(row["rust_wall_seconds"] for row in group),
            "avg_gib_copied": _mean(row["gib_copied"] for row in group),
            "avg_rust_gib_per_second": _mean(row["rust_gib_per_second"] for row in group),
            "max_rust_invalid_specs": max((int(row["rust_invalid_specs"]) for row in group), default=0),
        }
    return {
        "header": dict(header),
        "rows": len(rows),
        "measured_rows": len(measured),
        "by_batch_size": by_batch_size,
    }


def _mean(values: Any) -> float:
    items = [float(value) for value in values]
    return float(sum(items) / len(items)) if items else 0.0


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
