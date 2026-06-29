from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.ticker_month_cache import DEFAULT_TICKER_MONTH_CACHE_ROOT, jsonable
from research.mlops.rolling_loader.ticker_month_dataset import AsyncTickerMonthBatchLoader, TickerMonthLoaderConfig
from research.mlops.rolling_loader.streaming_training import current_rss_mib


DEFAULT_PROFILE_REPORT_PATH = Path("D:/market-data/prepared/data_provider_profiles/ticker_month_loader_profile.jsonl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the ticker/month SSD cache training loader.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_TICKER_MONTH_CACHE_ROOT)
    parser.add_argument("--cache-id", required=True)
    parser.add_argument("--split", default="train")
    parser.add_argument("--month", action="append", default=[])
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--data-groups", default="events,intraday_labels")
    parser.add_argument("--event-output-mode", choices=("none", "raw_flat", "raw_windows", "encoded_uint8"), default="raw_windows")
    parser.add_argument("--event-columns", default="", help="Comma-separated event columns to emit. Empty means all cached numeric event columns after suppression.")
    parser.add_argument(
        "--suppress-event-columns",
        default="ticker_id,ordinal,timestamp_us",
        help="Comma-separated cached event columns to suppress from raw event outputs.",
    )
    parser.add_argument("--events-per-window", type=int, default=128)
    parser.add_argument("--context-chunks", type=int, default=32)
    parser.add_argument("--context-stride-events", type=int, default=64)
    parser.add_argument("--flat-coverage-events", type=int, default=0)
    parser.add_argument("--loaded-parts-per-group", type=int, default=8)
    parser.add_argument("--read-workers", type=int, default=4)
    parser.add_argument("--materialize-workers", type=int, default=4)
    parser.add_argument("--materialize-chunk-size", type=int, default=0, help="Origins per CPU materialization task. Default 0 uses batch-size.")
    parser.add_argument("--drop-last-batch", action="store_true", help="Drop the final partial ready batch for each loaded group.")
    parser.add_argument("--allow-unordered-materialization", action="store_true", help="Yield completed materialization tasks as they finish. Faster but not repeatable.")
    parser.add_argument("--dataset-id", default="", help="Stable dataset plan id used in hashing/state. Empty creates an automatic id from cache/config.")
    parser.add_argument("--randomize-seed", action="store_true", help="Generate a random run seed and save it in loader state for replay.")
    parser.add_argument("--sample-fraction", type=float, default=1.0, help="Deterministic hash fraction of origins to include.")
    parser.add_argument("--sample-hash-modulus", type=int, default=0, help="Modulo for deterministic hash bucket train/validation splits.")
    parser.add_argument("--sample-hash-buckets", default="", help="Comma-separated hash buckets to include when sample-hash-modulus is set.")
    parser.add_argument("--max-origins-per-epoch", type=int, default=0, help="Stop after this many emitted origins in the epoch. 0 means no cap.")
    parser.add_argument("--load-state-path", type=Path, default=None, help="Resume loader state from this JSON file.")
    parser.add_argument("--save-state-path", type=Path, default=None, help="Write final loader state JSON to this file.")
    parser.add_argument("--include-external-context", action="store_true")
    parser.add_argument("--no-strict-audit", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_PROFILE_REPORT_PATH)
    parser.add_argument("--no-report", action="store_true", help="Disable JSONL report writing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cache_root = Path(args.cache_root) / str(args.cache_id)
    started_at_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    config = TickerMonthLoaderConfig(
        cache_root=cache_root,
        split=args.split,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        months=tuple(str(month) for month in args.month),
        tickers=tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip()),
        batch_size=max(1, int(args.batch_size)),
        seed=int(args.seed),
        data_groups=tuple(item.strip() for item in str(args.data_groups).split(",") if item.strip()),
        event_output_mode=str(args.event_output_mode),
        event_columns=tuple(item.strip() for item in str(args.event_columns).split(",") if item.strip()),
        suppress_event_columns=tuple(item.strip() for item in str(args.suppress_event_columns).split(",") if item.strip()),
        events_per_window=max(1, int(args.events_per_window)),
        context_chunks=max(0, int(args.context_chunks)),
        context_stride_events=max(1, int(args.context_stride_events)),
        flat_coverage_events=max(0, int(args.flat_coverage_events)),
        loaded_parts_per_group=max(1, int(args.loaded_parts_per_group)),
        read_workers=max(1, int(args.read_workers)),
        materialize_workers=max(1, int(args.materialize_workers)),
        materialize_chunk_size=max(0, int(args.materialize_chunk_size)),
        drop_last_batch=bool(args.drop_last_batch),
        preserve_batch_order=not bool(args.allow_unordered_materialization),
        max_batches=max(0, int(args.batches)),
        include_external_context=bool(args.include_external_context),
        strict_audit=not bool(args.no_strict_audit),
        dataset_id=str(args.dataset_id),
        randomize_seed=bool(args.randomize_seed),
        sample_fraction=max(0.0, min(1.0, float(args.sample_fraction))),
        sample_hash_modulus=max(0, int(args.sample_hash_modulus)),
        sample_hash_buckets=tuple(int(item.strip()) for item in str(args.sample_hash_buckets).split(",") if item.strip()),
        max_origins_per_epoch=max(0, int(args.max_origins_per_epoch)),
    )
    print("TICKER MONTH LOADER PROFILE " + str(cache_root), flush=True)
    print(json.dumps(jsonable(asdict(config)), sort_keys=True), flush=True)
    if not bool(args.no_report):
        print("PROFILE_REPORT " + str(args.report_path), flush=True)
    started = time.perf_counter()
    loader = AsyncTickerMonthBatchLoader(config)
    if args.load_state_path is not None:
        with args.load_state_path.open("r", encoding="utf-8") as handle:
            loader.load_state_dict(json.load(handle))
    discovered = len(loader.index.parts)
    print("LOADER_STATE_START " + json.dumps(loader.summary(), sort_keys=True), flush=True)
    batches = 0
    samples = 0
    materialize_seconds = 0.0
    max_rss = current_rss_mib()
    first_shape: dict[str, Any] = {}
    for batch in loader.iter_batches():
        batches += 1
        samples += int(batch.sample_count)
        materialize_seconds += float(batch.profile.get("materialize_seconds", 0.0))
        max_rss = max(max_rss, current_rss_mib())
        if not first_shape:
            first_shape = _shape_summary(batch)
        elapsed = max(time.perf_counter() - started, 1e-9)
        print(
            json.dumps(
                {
                    "batch": batches,
                    "samples": samples,
                    "samples_per_sec": samples / elapsed,
                    "materialize_seconds": materialize_seconds,
                    "rss_mib": max_rss,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if int(args.batches) > 0 and batches >= int(args.batches):
            break
    elapsed = time.perf_counter() - started
    summary = {
        "cache_root": str(cache_root),
        "profile_started_at_utc": started_at_utc,
        "profile_finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile_report_path": "" if bool(args.no_report) else str(args.report_path),
        "discovered_parts": discovered,
        "batches": batches,
        "samples": samples,
        "elapsed_seconds": elapsed,
        "samples_per_sec": samples / max(elapsed, 1e-9),
        "materialize_seconds": materialize_seconds,
        "max_rss_mib": max_rss,
        "first_batch": first_shape,
        "loader_state": loader.summary(),
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    if args.save_state_path is not None:
        args.save_state_path.parent.mkdir(parents=True, exist_ok=True)
        with args.save_state_path.open("w", encoding="utf-8") as handle:
            json.dump(loader.state_dict(), handle, sort_keys=True, indent=2)
    if not bool(args.no_report):
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        with args.report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0


def _shape_summary(batch: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "samples": int(batch.sample_count),
        "event_output_mode": batch.event_output_mode,
        "ticker_shape": list(batch.ticker.shape),
        "origin_ordinal_shape": list(batch.origin_ordinal.shape),
    }
    if batch.raw_event_windows:
        first = next(iter(batch.raw_event_windows.values()))
        out["raw_event_windows_shape"] = list(first.shape)
        out["raw_event_window_columns"] = sorted(batch.raw_event_windows)
    if batch.raw_event_flat:
        first = next(iter(batch.raw_event_flat.values()))
        out["raw_event_flat_shape"] = list(first.shape)
        out["raw_event_flat_columns"] = sorted(batch.raw_event_flat)
    if batch.headers_uint8.size:
        out["headers_uint8_shape"] = list(batch.headers_uint8.shape)
        out["events_uint8_shape"] = list(batch.events_uint8.shape)
    if batch.intraday_labels:
        out["intraday_label_shapes"] = {key: list(value.shape) for key, value in batch.intraday_labels.items()}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
