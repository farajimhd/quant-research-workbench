from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.data.config import TickerBlockDataConfig, TimeBarHorizon
from research.mlops.data.ticker_blocks import (
    TickerCursor,
    TickerEpochScheduler,
    build_event_time_bar_batch,
    build_requests,
    fetch_ticker_blocks_profiled,
    fetch_ticker_date_blocks_profiled,
    load_ticker_cursors_from_index,
    make_synthetic_event_rows,
    profile_batch_summary,
)
from research.mlops.clickhouse import quote_ident, sql_string


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
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--mode", choices=("ordinal", "date", "compare"), default="ordinal")
    parser.add_argument("--date-block-date", default="")
    parser.add_argument("--report-path", type=Path, default=None)
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
    print(f"database={args.database} events_table={args.events_table} index_table={args.index_table} mode={args.mode}", flush=True)
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
    if args.tickers and config.state_path is not None and config.state_path.exists():
        raise RuntimeError("--tickers cannot be combined with an existing --state-path; remove the state file or omit --tickers.")
    if config.state_path is not None and config.state_path.exists():
        scheduler = TickerEpochScheduler.load(config.state_path)
    else:
        cursors = load_ticker_cursors_from_index(text_client, config, limit=0 if args.tickers else int(args.max_tickers))
        cursors = filter_cursors(cursors, args.tickers)
        scheduler = TickerEpochScheduler.from_cursors(cursors, seed=config.seed)
        if config.state_path is not None:
            scheduler.save(config.state_path)
    try:
        for batch_index in range(int(args.batches)):
            selected = scheduler.select_next(int(config.ticker_group_size))
            if not selected:
                raise StopIteration("No active ticker cursors remain.")
            event_date = str(args.date_block_date or "")
            if args.mode in {"date", "compare"} and not event_date:
                event_date = infer_event_date_for_cursor(text_client, config, selected[0])
                print(f"INFERRED date_block_date={event_date} from ticker={selected[0].ticker}", flush=True)
            if args.mode in {"ordinal", "compare"}:
                ordinal_result = run_one_mode(
                    mode="ordinal",
                    selected=selected,
                    config=config,
                    bytes_client=bytes_client,
                    event_date=event_date,
                    batch_index=batch_index,
                )
                print_profile_result(batch_index, int(args.batches), ordinal_result)
                append_report(args.report_path, ordinal_result)
                if args.mode == "ordinal":
                    scheduler.update_after_success(ordinal_result["completed_requests"])
                    if config.state_path is not None:
                        scheduler.save(config.state_path)
            if args.mode in {"date", "compare"}:
                date_result = run_one_mode(
                    mode="date",
                    selected=selected,
                    config=config,
                    bytes_client=bytes_client,
                    event_date=event_date,
                    batch_index=batch_index,
                )
                print_profile_result(batch_index, int(args.batches), date_result)
                append_report(args.report_path, date_result)
    finally:
        bytes_client.close()
    return 0


def filter_cursors(cursors: list[TickerCursor], tickers: str) -> list[TickerCursor]:
    wanted = {item.strip().upper() for item in str(tickers).split(",") if item.strip()}
    if not wanted:
        return cursors
    filtered = [cursor for cursor in cursors if cursor.ticker.upper() in wanted]
    missing = sorted(wanted - {cursor.ticker.upper() for cursor in filtered})
    if missing:
        raise RuntimeError(f"Requested tickers not found in index table: {missing}")
    return filtered


def run_one_mode(
    *,
    mode: str,
    selected: list[TickerCursor],
    config: TickerBlockDataConfig,
    bytes_client: PersistentClickHouseBytesClient,
    event_date: str,
    batch_index: int,
) -> dict[str, object]:
    fetch_started = time.perf_counter()
    if mode == "ordinal":
        requests = build_requests(selected, config)
        fetch_result = fetch_ticker_blocks_profiled(bytes_client, config, requests)
    elif mode == "date":
        fetch_result = fetch_ticker_date_blocks_profiled(
            bytes_client,
            config,
            [cursor.ticker for cursor in selected],
            event_date=event_date,
        )
        requests = fetch_result.requests
    else:
        raise ValueError(f"Unsupported mode: {mode}")
    fetch_wall_seconds = time.perf_counter() - fetch_started
    build_started = time.perf_counter()
    batch = build_event_time_bar_batch(
        rows_by_ticker=fetch_result.rows_by_ticker,
        requests=requests,
        config=config,
        provider_name=f"clickhouse_ticker_block_{mode}",
        batch_id=batch_index,
    )
    build_wall_seconds = time.perf_counter() - build_started
    total_seconds = fetch_wall_seconds + build_wall_seconds
    samples = int(batch.header_uint8.shape[0])
    return {
        "mode": mode,
        "event_date": event_date,
        "batch_index": int(batch_index),
        "selected_tickers": [cursor.ticker for cursor in selected],
        "request_count": len(requests),
        "requests": requests,
        "completed_requests": [request for request in requests if request.ticker in fetch_result.rows_by_ticker],
        "rows_returned": int(fetch_result.rows_returned),
        "samples": samples,
        "fetch_seconds": float(fetch_result.fetch_seconds),
        "fetch_wall_seconds": float(fetch_wall_seconds),
        "build_seconds": float(build_wall_seconds),
        "total_seconds": float(total_seconds),
        "samples_per_second": float(samples / max(total_seconds, 1e-9)),
        "local_samples_per_second": float(samples / max(build_wall_seconds, 1e-9)),
        "reject_counts": dict(batch.reject_counts),
        "labels": {key: tuple(value.shape) for key, value in batch.labels.items()},
        "summary": profile_batch_summary(batch),
    }


def infer_event_date_for_cursor(client: ClickHouseHttpClient, config: TickerBlockDataConfig, cursor: TickerCursor) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    query = f"""
SELECT toString(event_date)
FROM {table}
PREWHERE ticker = {sql_string(cursor.ticker)}
WHERE ordinal >= {int(cursor.next_origin_ordinal)}
ORDER BY ordinal
LIMIT 1
FORMAT TSV
"""
    rows = client.execute(query).strip().splitlines()
    if not rows:
        raise RuntimeError(f"Could not infer event_date for ticker={cursor.ticker} ordinal={cursor.next_origin_ordinal}")
    return rows[0].strip()


def print_profile_result(batch_index: int, batches: int, result: dict[str, object]) -> None:
    print(
        f"BATCH [{batch_index + 1}/{batches}] mode={result['mode']} event_date={result['event_date']} "
        f"samples={int(result['samples']):,} rows={int(result['rows_returned']):,} "
        f"fetch={float(result['fetch_seconds']):.3f}s build={float(result['build_seconds']):.3f}s "
        f"total={float(result['total_seconds']):.3f}s samples_per_sec={float(result['samples_per_second']):.1f} "
        f"local_samples_per_sec={float(result['local_samples_per_second']):.1f} rejects={result['reject_counts']}",
        flush=True,
    )
    labels = result.get("labels", {})
    if isinstance(labels, dict):
        preview = []
        for key in sorted(labels)[:8]:
            preview.append(f"{key}:{labels[key]}")
        print("  labels=" + ", ".join(preview), flush=True)


def append_report(path: Path | None, result: dict[str, object]) -> None:
    if path is None:
        return
    serializable = dict(result)
    serializable["requests"] = [
        {
            "ticker": request.ticker,
            "low_ordinal": int(request.low_ordinal),
            "high_ordinal": int(request.high_ordinal),
            "origin_start_ordinal": int(request.origin_start_ordinal),
            "origin_end_ordinal": int(request.origin_end_ordinal),
            "expected_rows": int(request.expected_rows),
        }
        for request in result.get("requests", [])
    ]
    serializable["completed_requests"] = [
        {
            "ticker": request.ticker,
            "low_ordinal": int(request.low_ordinal),
            "high_ordinal": int(request.high_ordinal),
            "origin_start_ordinal": int(request.origin_start_ordinal),
            "origin_end_ordinal": int(request.origin_end_ordinal),
            "expected_rows": int(request.expected_rows),
        }
        for request in result.get("completed_requests", [])
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(serializable, sort_keys=True) + "\n")


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
