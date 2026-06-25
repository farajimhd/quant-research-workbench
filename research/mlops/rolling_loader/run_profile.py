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

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    clickhouse_env_status_keys,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.env import load_env_files, secret_status
from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.initialize import InitializedRollingReplay, initialize_clickhouse_replay
from research.mlops.rolling_loader.loader import RollingContextLoader
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler, format_profile_table, write_profile_jsonl
from research.mlops.rolling_loader.sources import (
    ClickHouseExternalContextConfig,
    ClickHouseExternalContextSource,
    ClickHouseReplayConfig,
    ClickHouseRollingSource,
    replay_items_for_block,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the stateful rolling loader with the exact loader class.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--sec-context-database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--index-table", default="train_2019_to_2025")
    parser.add_argument("--news-token-table", default="news_text_tokens")
    parser.add_argument("--sec-filing-text-token-table", default="sec_filing_text_tokens")
    parser.add_argument("--sec-xbrl-context-table", default="sec_xbrl_context")
    parser.add_argument("--macro-bars-table", default="macro_bars_by_time_symbol")
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--tickers", type=int, default=64)
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
    parser.add_argument(
        "--start-timestamp-us",
        type=int,
        default=0,
        help="Replay start timestamp. When set, all high- and low-frequency caches are initialized as-of this timestamp.",
    )
    parser.add_argument("--news-lookback-days", type=int, default=30)
    parser.add_argument("--sec-lookback-days", type=int, default=365)
    parser.add_argument("--xbrl-lookback-days", type=int, default=730)
    parser.add_argument("--macro-lookback-days", type=int, default=400)
    parser.add_argument("--materialize-external-payloads", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("D:/market-data/prepared/data_provider_profiles/rolling_loader_profile.jsonl"),
    )
    return parser.parse_args()


def build_profile_source(
    *,
    args: argparse.Namespace,
    loader: RollingContextLoader,
    loader_config: RollingLoaderConfig,
    profiler: RollingLoaderProfiler,
) -> InitializedRollingReplay:
    text_client = ClickHouseHttpClient(args.clickhouse_url or default_clickhouse_url(), args.user or default_clickhouse_user(), args.password or default_clickhouse_password())
    bytes_client = PersistentClickHouseBytesClient(args.clickhouse_url or default_clickhouse_url(), args.user or default_clickhouse_user(), args.password or default_clickhouse_password())
    source = ClickHouseRollingSource(
        config=ClickHouseReplayConfig(
            database=str(args.database),
            events_table=str(args.events_table),
            index_table=str(args.index_table),
            max_threads=int(args.max_threads),
            max_memory_usage=str(args.max_memory_usage),
        ),
        text_client=text_client,
        bytes_client=bytes_client,
    )
    context_source = ClickHouseExternalContextSource(
        config=ClickHouseExternalContextConfig(
            database=str(args.database),
            sec_context_database=str(args.sec_context_database),
            news_token_table=str(args.news_token_table),
            sec_filing_text_token_table=str(args.sec_filing_text_token_table),
            sec_xbrl_context_table=str(args.sec_xbrl_context_table),
            macro_bars_table=str(args.macro_bars_table),
            news_lookback_days=int(args.news_lookback_days),
            sec_lookback_days=int(args.sec_lookback_days),
            xbrl_lookback_days=int(args.xbrl_lookback_days),
            macro_lookback_days=int(args.macro_lookback_days),
            ticker_news_items=int(loader_config.ticker_news_cache_size),
            global_news_items=int(loader_config.global_news_cache_size),
            sec_filing_items=int(loader_config.sec_filing_cache_size),
            xbrl_items=int(loader_config.xbrl_cache_size),
            news_token_chunks=int(loader_config.news_token_chunks),
            sec_token_chunks=int(loader_config.sec_token_chunks),
            text_max_tokens=int(loader_config.text_max_tokens),
        ),
        text_client=text_client,
    )
    state = initialize_clickhouse_replay(
        loader=loader,
        loader_config=loader_config,
        source=source,
        context_source=context_source,
        profiler=profiler,
        ticker_limit=int(args.tickers),
        events_per_ticker_block=int(args.events_per_ticker_block),
        start_timestamp_us=int(args.start_timestamp_us),
    )
    return InitializedRollingReplay(
        source=state.source,
        context_source=state.context_source,
        cursors=state.cursors,
        index_rows=state.index_rows,
        initialized_tickers=state.initialized_tickers,
        warm_tickers=state.warm_tickers,
        warm_rows=state.warm_rows,
        initial_context_updates=state.initial_context_updates,
        start_timestamp_us=state.start_timestamp_us,
        source_summary={
            **state.source_summary,
            "source": "clickhouse",
            "database": str(args.database),
            "sec_context_database": str(args.sec_context_database),
            "events_table": str(args.events_table),
            "index_table": str(args.index_table),
            "news_token_table": str(args.news_token_table),
            "sec_filing_text_token_table": str(args.sec_filing_text_token_table),
            "sec_xbrl_context_table": str(args.sec_xbrl_context_table),
            "macro_bars_table": str(args.macro_bars_table),
            "clickhouse_url": args.clickhouse_url or default_clickhouse_url(),
        },
    )


def main() -> int:
    args = parse_args()
    loaded_env_files = load_env_files(discover_clickhouse_env_files() if args.env_file is None else discover_clickhouse_env_files() + [args.env_file])
    args.clickhouse_url = args.clickhouse_url or default_clickhouse_url()
    args.user = args.user or default_clickhouse_user()
    args.password = args.password or default_clickhouse_password()
    loader_config = RollingLoaderConfig(
        batch_size=int(args.batch_size),
        short_context_chunks=int(args.context_chunks),
        chunk_stride_events=1,
        context_chunk_stride_events=int(args.context_chunk_stride_events),
        long_context_lags=(),
        sample_stride_events=int(args.sample_stride_events),
        profile_report_path=args.report_path,
    )
    profiler = RollingLoaderProfiler(enabled=True)
    loader = RollingContextLoader(loader_config, profiler=profiler)
    started = time.perf_counter()
    print("=" * 100)
    print("Stateful rolling loader profiler")
    print(
        f"source=clickhouse database={args.database} events_table={args.events_table} index_table={args.index_table} "
        f"tickers={int(args.tickers)} "
        f"batch_size={loader_config.batch_size} context_chunks={loader_config.context_chunks} "
        f"chunk_size={loader_config.events_per_chunk} origin_chunk_stride={loader_config.chunk_stride_events} "
        f"context_chunk_stride={loader_config.context_chunk_stride_events} "
        f"coverage_events={loader_config.context_coverage_events} overlap_events={loader_config.adjacent_chunk_overlap_events} "
        f"materialize_external={bool(args.materialize_external_payloads)}"
    )
    print(f"clickhouse_url={args.clickhouse_url} max_threads={args.max_threads} max_memory_usage={args.max_memory_usage}")
    print(f"report={args.report_path}")
    print(f"secret_status={secret_status(clickhouse_env_status_keys())}")
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}")
    print("=" * 100)
    source_state = build_profile_source(
        args=args,
        loader=loader,
        loader_config=loader_config,
        profiler=profiler,
    )
    source = source_state.source
    context_source = source_state.context_source
    cursors = source_state.cursors
    print(f"SOURCE READY {json.dumps(source_state.source_summary, sort_keys=True)}", flush=True)
    completed_batches = 0
    event_count = 0
    last_batch_bytes = 0
    exhausted = False
    try:
        while completed_batches < int(args.batches) and not exhausted:
            with profiler.stage("source_fetch_event_block", items=len(cursors)):
                block = source.fetch_next_by_ordinal(cursors=cursors, rows_per_ticker=int(args.events_per_ticker_block))
            if block.row_count == 0:
                exhausted = True
                break
            cursors.update(block.latest_ordinals())
            with profiler.stage("low_frequency_update_fetch", items=block.row_count):
                updates = context_source.fetch_context_updates(
                    tickers=block.tickers,
                    start_exclusive_us=int(block.min_timestamp_us or 0) - 1,
                    end_inclusive_us=int(block.max_timestamp_us or 0),
                )
            with profiler.stage("block_replay_events", items=block.row_count, bytes_count=int(block.rows.nbytes)):
                for item in replay_items_for_block(block, updates):
                    if item.context is not None:
                        update = item.context
                        with profiler.stage("low_frequency_update_apply", items=1, bytes_count=update.payload.nbytes):
                            loader.push_external(
                                kind=update.kind,
                                ticker=update.ticker,
                                timestamp_us=update.timestamp_us,
                                payload=update.payload,
                                global_item=update.global_item,
                            )
                        continue
                    if item.event is None:
                        continue
                    event_count += 1
                    loader.push_event(item.event.ticker, item.event.row)
                    while len(loader.ready_samples) >= loader_config.batch_size and completed_batches < int(args.batches):
                        samples = loader.drain_ready_samples(loader_config.batch_size)
                        batch = loader.materialize_training_batch(
                            samples,
                            materialize_external_payloads=bool(args.materialize_external_payloads),
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
                            "source": source_state.source_summary,
                            "config": {
                                "tickers": int(args.tickers),
                                "events_per_ticker_block": int(args.events_per_ticker_block),
                                "batch_size": loader_config.batch_size,
                                "context_chunks": loader_config.context_chunks,
                                "origin_chunk_stride_events": loader_config.chunk_stride_events,
                                "context_chunk_stride_events": loader_config.context_chunk_stride_events,
                                "context_coverage_events": loader_config.context_coverage_events,
                                "materialize_external_payloads": bool(args.materialize_external_payloads),
                            },
                            "profile": profiler.snapshot(),
                        }
                        write_profile_jsonl(args.report_path, payload)
                        print(
                            f"BATCH [{completed_batches}/{int(args.batches)}] "
                            f"events={event_count:,} block_rows={block.row_count:,} batch_mib={payload['last_batch_mib']:.2f} "
                            f"elapsed={time.perf_counter() - started:.1f}s"
                        )
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()
    final_payload = {
        "kind": "rolling_loader_profile_final",
        "completed_batches": completed_batches,
        "events_replayed": event_count,
        "exhausted": exhausted,
        "last_batch_mib": last_batch_bytes / (1024 * 1024),
        "cache": loader.cache_summary(),
        "source": source_state.source_summary,
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
