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
from research.mlops.rolling_loader.sources import SyntheticOrdinalBlockSource
from research.mlops.rolling_loader.synthetic import synthetic_external_updates_for_block, synthetic_rows_by_ticker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the stateful rolling loader with the exact loader class.")
    parser.add_argument("--tickers", type=int, default=64)
    parser.add_argument("--rows-per-ticker", type=int, default=8000)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--events-per-ticker-block", type=int, default=64)
    parser.add_argument("--context-chunks", type=int, default=32)
    parser.add_argument(
        "--context-chunk-stride-events",
        "--chunk-stride-events",
        dest="context_chunk_stride_events",
        type=int,
        default=64,
        help="Event spacing between context chunks. Sample origins still use --sample-stride-events.",
    )
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
        short_context_chunks=int(args.context_chunks),
        chunk_stride_events=1,
        context_chunk_stride_events=int(args.context_chunk_stride_events),
        long_context_lags=(),
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
        f"chunk_size={loader_config.events_per_chunk} origin_chunk_stride={loader_config.chunk_stride_events} "
        f"context_chunk_stride={loader_config.context_chunk_stride_events} "
        f"coverage_events={loader_config.context_coverage_events} overlap_events={loader_config.adjacent_chunk_overlap_events} "
        f"materialize_external={synthetic_config.materialize_external_payloads}"
    )
    print(f"report={args.report_path}")
    print("=" * 100)
    rows_by_ticker = synthetic_rows_by_ticker(synthetic_config)
    warm_count = min(loader_config.warmup_events_per_ticker, synthetic_config.rows_per_ticker // 2)
    source = SyntheticOrdinalBlockSource(rows_by_ticker)
    loader.warm_load_events(source.warm_rows(count=warm_count))
    cursors = source.initial_cursors(warm_count=warm_count)
    completed_batches = 0
    event_count = 0
    last_batch_bytes = 0
    exhausted = False
    while completed_batches < synthetic_config.batches and not exhausted:
        with profiler.stage("source_fetch_event_block", items=len(cursors)):
            block = source.fetch_next_by_ordinal(cursors=cursors, rows_per_ticker=int(args.events_per_ticker_block))
        if block.row_count == 0:
            exhausted = True
            break
        cursors.update(block.latest_ordinals())
        with profiler.stage("low_frequency_update_fetch", items=block.row_count):
            updates = synthetic_external_updates_for_block(block=block, synthetic_config=synthetic_config)
        with profiler.stage("low_frequency_update_apply", items=len(updates), bytes_count=sum(update.payload.nbytes for update in updates)):
            for update in updates:
                loader.push_external(
                    kind=update.kind,
                    ticker=update.ticker,
                    timestamp_us=update.timestamp_us,
                    payload=update.payload,
                    global_item=update.global_item,
                )
        with profiler.stage("block_replay_events", items=block.row_count, bytes_count=int(block.rows.nbytes)):
            for event in block.iter_chronological():
                event_count += 1
                loader.push_event(event.ticker, event.row)
                while len(loader.ready_samples) >= loader_config.batch_size and completed_batches < synthetic_config.batches:
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
                            "events_per_ticker_block": int(args.events_per_ticker_block),
                            "batch_size": loader_config.batch_size,
                            "context_chunks": loader_config.context_chunks,
                            "origin_chunk_stride_events": loader_config.chunk_stride_events,
                            "context_chunk_stride_events": loader_config.context_chunk_stride_events,
                            "context_coverage_events": loader_config.context_coverage_events,
                            "materialize_external_payloads": synthetic_config.materialize_external_payloads,
                        },
                        "profile": profiler.snapshot(),
                    }
                    write_profile_jsonl(args.report_path, payload)
                    print(
                        f"BATCH [{completed_batches}/{synthetic_config.batches}] "
                        f"events={event_count:,} block_rows={block.row_count:,} batch_mib={payload['last_batch_mib']:.2f} "
                        f"elapsed={time.perf_counter() - started:.1f}s"
                    )
    final_payload = {
        "kind": "rolling_loader_profile_final",
        "completed_batches": completed_batches,
        "events_replayed": event_count,
        "exhausted": exhausted,
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
