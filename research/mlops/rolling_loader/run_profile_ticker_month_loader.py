from __future__ import annotations

import argparse
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
    parser.add_argument("--include-external-context", action="store_true")
    parser.add_argument("--no-strict-audit", action="store_true")
    parser.add_argument("--report-path", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cache_root = Path(args.cache_root) / str(args.cache_id)
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
        max_batches=max(0, int(args.batches)),
        include_external_context=bool(args.include_external_context),
        strict_audit=not bool(args.no_strict_audit),
    )
    print("TICKER MONTH LOADER PROFILE " + str(cache_root), flush=True)
    print(json.dumps(jsonable(asdict(config)), sort_keys=True), flush=True)
    started = time.perf_counter()
    loader = AsyncTickerMonthBatchLoader(config)
    discovered = len(loader.index.parts)
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
        "discovered_parts": discovered,
        "batches": batches,
        "samples": samples,
        "elapsed_seconds": elapsed,
        "samples_per_sec": samples / max(elapsed, 1e-9),
        "materialize_seconds": materialize_seconds,
        "max_rss_mib": max_rss,
        "first_batch": first_shape,
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    if args.report_path is not None:
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
