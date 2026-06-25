from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.config import RollingLoaderConfig, SyntheticRollingLoaderConfig
from research.mlops.rolling_loader.loader import RollingContextLoader
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler, format_profile_table, write_profile_jsonl
from research.mlops.rolling_loader.synthetic import iter_synthetic_events, synthetic_external_updates, synthetic_rows_by_ticker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the stateful rolling loader with the exact loader class.")
    parser.add_argument("--tickers", type=int, default=64)
    parser.add_argument("--rows-per-ticker", type=int, default=8000)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--sample-stride-events", type=int, default=1)
    parser.add_argument("--external-every-events", type=int, default=512)
    parser.add_argument("--materialize-external-payloads", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("D:/market-data/prepared/data_provider_profiles/rolling_loader_profile.jsonl"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loader_config = RollingLoaderConfig(
        batch_size=int(args.batch_size),
        sample_stride_events=int(args.sample_stride_events),
        profile_report_path=args.report_path,
    )
    synthetic_config = SyntheticRollingLoaderConfig(
        tickers=int(args.tickers),
        rows_per_ticker=int(args.rows_per_ticker),
        external_every_events=int(args.external_every_events),
        batches=int(args.batches),
        materialize_external_payloads=bool(args.materialize_external_payloads),
        loader=loader_config,
    )
    profiler = RollingLoaderProfiler(enabled=True)
    loader = RollingContextLoader(loader_config, profiler=profiler)
    started = time.perf_counter()
    print("=" * 100)
    print("Stateful rolling loader profiler")
    print(
        f"tickers={synthetic_config.tickers} rows_per_ticker={synthetic_config.rows_per_ticker} "
        f"batch_size={loader_config.batch_size} context_chunks={loader_config.context_chunks} "
        f"max_lag={loader_config.max_context_lag} materialize_external={synthetic_config.materialize_external_payloads}"
    )
    print(f"report={args.report_path}")
    print("=" * 100)
    rows_by_ticker = synthetic_rows_by_ticker(synthetic_config)
    warm_count = min(loader_config.warmup_events_per_ticker, synthetic_config.rows_per_ticker // 2)
    warm_rows = {ticker: rows[:warm_count] for ticker, rows in rows_by_ticker.items()}
    loader.warm_load_events(warm_rows)
    replay_rows = {ticker: rows[warm_count:] for ticker, rows in rows_by_ticker.items()}
    completed_batches = 0
    event_count = 0
    last_batch_bytes = 0
    for event in iter_synthetic_events(replay_rows):
        event_count += 1
        for update in synthetic_external_updates(
            ticker=event.ticker,
            row=event.row,
            event_index=event_count,
            synthetic_config=synthetic_config,
        ):
            loader.push_external(
                kind=update.kind,
                ticker=update.ticker,
                timestamp_us=update.timestamp_us,
                payload=update.payload,
                global_item=update.global_item,
            )
        loader.push_event(event.ticker, event.row)
        if len(loader.ready_samples) >= loader_config.batch_size:
            samples = loader.drain_ready_samples(loader_config.batch_size)
            batch = loader.materialize_training_batch(
                samples,
                materialize_external_payloads=synthetic_config.materialize_external_payloads,
            )
            completed_batches += 1
            last_batch_bytes = batch.nbytes
            payload = {
                "kind": "rolling_loader_profile_batch",
                "batch_index": completed_batches,
                "samples": len(samples),
                "events_replayed": event_count,
                "last_batch_mib": last_batch_bytes / (1024 * 1024),
                "cache": loader.cache_summary(),
                "config": {
                    "tickers": synthetic_config.tickers,
                    "rows_per_ticker": synthetic_config.rows_per_ticker,
                    "batch_size": loader_config.batch_size,
                    "context_chunks": loader_config.context_chunks,
                    "materialize_external_payloads": synthetic_config.materialize_external_payloads,
                },
                "profile": profiler.snapshot(),
            }
            write_profile_jsonl(args.report_path, payload)
            print(
                f"BATCH [{completed_batches}/{synthetic_config.batches}] "
                f"events={event_count:,} batch_mib={payload['last_batch_mib']:.2f} "
                f"elapsed={time.perf_counter() - started:.1f}s"
            )
            if completed_batches >= synthetic_config.batches:
                break
    final_payload = {
        "kind": "rolling_loader_profile_final",
        "completed_batches": completed_batches,
        "events_replayed": event_count,
        "last_batch_mib": last_batch_bytes / (1024 * 1024),
        "cache": loader.cache_summary(),
        "profile": profiler.snapshot(),
    }
    write_profile_jsonl(args.report_path, final_payload)
    print("-" * 100)
    print(format_profile_table(final_payload["profile"]))
    print("-" * 100)
    print(json.dumps({"completed_batches": completed_batches, "events_replayed": event_count, "report": str(args.report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
