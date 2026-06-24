from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.data.config import TickerBlockDataConfig, TimeBarHorizon
from research.mlops.data.ticker_blocks import (
    ClickHouseTickerBlockBatchProvider,
    TickerCursor,
    TickerEpochScheduler,
    build_event_time_bar_batch,
    build_requests,
    make_synthetic_event_rows,
    profile_batch_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile chronological multi-ticker block data preparation.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--index-table", default="train_2019_to_2025")
    parser.add_argument("--ticker-group-size", type=int, default=16)
    parser.add_argument("--events-per-ticker-block", type=int, default=20_000)
    parser.add_argument("--future-tail-events", type=int, default=4096)
    parser.add_argument("--sample-stride-events", type=int, default=16)
    parser.add_argument("--max-samples-per-ticker", type=int, default=2048)
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--polars-assembly", action="store_true")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--synthetic-tickers", type=int, default=8)
    parser.add_argument("--synthetic-events", type=int, default=20_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    print("=" * 100, flush=True)
    print("Ticker block data-provider profiler", flush=True)
    print(f"database={args.database} events_table={args.events_table} index_table={args.index_table}", flush=True)
    print(
        f"ticker_group_size={args.ticker_group_size} events_per_ticker_block={args.events_per_ticker_block} "
        f"future_tail_events={args.future_tail_events} sample_stride_events={args.sample_stride_events}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in env_files]}", flush=True)
    print("=" * 100, flush=True)
    config = TickerBlockDataConfig(
        database=args.database,
        events_table=args.events_table,
        index_table=args.index_table,
        ticker_group_size=int(args.ticker_group_size),
        events_per_ticker_block=int(args.events_per_ticker_block),
        future_tail_events=int(args.future_tail_events),
        sample_stride_events=int(args.sample_stride_events),
        max_samples_per_ticker=int(args.max_samples_per_ticker),
        max_threads=int(args.max_threads),
        max_memory_usage=str(args.max_memory_usage),
        state_path=args.state_path,
        assemble_polars_table=bool(args.polars_assembly),
        horizons=(
            TimeBarHorizon("100ms", 100_000),
            TimeBarHorizon("1s", 1_000_000),
            TimeBarHorizon("5s", 5_000_000),
            TimeBarHorizon("60s", 60_000_000),
        ),
    )
    if args.synthetic:
        return run_synthetic(args, config)
    return run_clickhouse(args, config)


def run_clickhouse(args: argparse.Namespace, config: TickerBlockDataConfig) -> int:
    url = default_clickhouse_url()
    user = default_clickhouse_user()
    password = default_clickhouse_password()
    text_client = ClickHouseHttpClient(url, user, password)
    bytes_client = PersistentClickHouseBytesClient(url, user, password)
    provider = ClickHouseTickerBlockBatchProvider.from_clickhouse(
        config=config,
        text_client=text_client,
        bytes_client=bytes_client,
        max_tickers=int(args.max_tickers),
    )
    try:
        for batch_index in range(int(args.batches)):
            batch = provider.next_batch()
            print(f"BATCH [{batch_index + 1}/{args.batches}] {profile_batch_summary(batch)}", flush=True)
            print_label_preview(batch.labels)
    finally:
        bytes_client.close()
    return 0


def run_synthetic(args: argparse.Namespace, config: TickerBlockDataConfig) -> int:
    cursors = [
        TickerCursor(
            ticker=f"SYN{idx:04d}",
            first_ordinal=0,
            next_origin_ordinal=config.events_per_chunk - 1,
            last_ordinal=int(args.synthetic_events) - 1 - int(config.future_tail_events),
            event_count=int(args.synthetic_events),
        )
        for idx in range(int(args.synthetic_tickers))
    ]
    scheduler = TickerEpochScheduler.from_cursors(cursors, seed=17)
    for batch_index in range(int(args.batches)):
        selected = scheduler.select_next(int(config.ticker_group_size))
        requests = build_requests(selected, config)
        rows_by_ticker = {request.ticker: make_synthetic_event_rows(request.expected_rows, request.low_ordinal) for request in requests}
        batch = build_event_time_bar_batch(
            rows_by_ticker=rows_by_ticker,
            requests=requests,
            config=config,
            provider_name="synthetic_ticker_block",
            batch_id=batch_index,
        )
        scheduler.update_after_success(requests)
        print(f"BATCH [{batch_index + 1}/{args.batches}] {profile_batch_summary(batch)}", flush=True)
        print_label_preview(batch.labels)
    return 0

def print_label_preview(labels: dict[str, np.ndarray]) -> None:
    if not labels:
        print("  labels=none", flush=True)
        return
    preview = []
    for key in sorted(labels)[:8]:
        value = labels[key]
        preview.append(f"{key}:{tuple(value.shape)}")
    print("  labels=" + ", ".join(preview), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
