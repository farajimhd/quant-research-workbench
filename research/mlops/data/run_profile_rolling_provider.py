from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.data.rolling import (
    HistoricalClickHouseRollingSource,
    MacroBarFrame,
    RollingMarketSampleEngine,
    synthetic_rows_by_ticker,
    write_profile_jsonl,
)
from research.mlops.env import discover_env_files, load_env_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the production-aligned rolling market data provider.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--macro-bars-table", default="macro_bars_by_time_symbol")
    parser.add_argument("--index-table", default="train_2019_to_2025")
    parser.add_argument("--event-date", default="2025-01-02")
    parser.add_argument("--ticker-limit", type=int, default=64)
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--materialize-batches", type=int, default=2)
    parser.add_argument("--sample-stride-events", type=int, default=1)
    parser.add_argument("--max-ready-samples", type=int, default=0)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--report-path", type=Path, default=Path("D:/market-data/prepared/data_provider_profiles/rolling_provider_profile.jsonl"))
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--synthetic-tickers", type=int, default=16)
    parser.add_argument("--synthetic-events", type=int, default=8000)
    parser.add_argument("--profile-production-gather", action="store_true")
    parser.add_argument("--skip-q-live-contexts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    config = RollingMarketDataConfig(
        database=args.database,
        events_table=args.events_table,
        macro_bars_table=args.macro_bars_table,
        index_table=args.index_table,
        batch_size=int(args.batch_size),
        sample_stride_events=int(args.sample_stride_events),
        max_ready_samples=int(args.max_ready_samples),
        max_threads=int(args.max_threads),
        max_memory_usage=str(args.max_memory_usage),
    )
    print("=" * 100, flush=True)
    print("Rolling market data-provider profiler", flush=True)
    print(f"database={config.database} events_table={config.events_table} macro_bars_table={config.macro_bars_table}", flush=True)
    print(
        f"event_date={args.event_date} ticker_limit={args.ticker_limit} batch_size={config.batch_size} "
        f"context_chunks={len(config.context_lags)} carryover_events={config.carryover_events}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in env_files]}", flush=True)
    print("=" * 100, flush=True)
    if args.synthetic:
        return run_synthetic(args, config)
    return run_clickhouse(args, config)


def run_synthetic(args: argparse.Namespace, config: RollingMarketDataConfig) -> int:
    engine = RollingMarketSampleEngine(config)
    started = time.perf_counter()
    rows_by_ticker = synthetic_rows_by_ticker(tickers=int(args.synthetic_tickers), rows_per_ticker=int(args.synthetic_events))
    fetch_seconds = time.perf_counter() - started
    engine.append_rows_by_ticker(rows_by_ticker)
    engine.load_macro_bars(MacroBarFrame(rows=[]))
    return profile_engine(args, config, engine, rows_returned=sum(rows.size for rows in rows_by_ticker.values()), fetch_seconds=fetch_seconds)


def run_clickhouse(args: argparse.Namespace, config: RollingMarketDataConfig) -> int:
    url = default_clickhouse_url()
    user = default_clickhouse_user()
    password = default_clickhouse_password()
    text_client = ClickHouseHttpClient(url, user, password)
    bytes_client = PersistentClickHouseBytesClient(url, user, password)
    try:
        source = HistoricalClickHouseRollingSource(config=config, text_client=text_client, bytes_client=bytes_client)
        tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
        if not tickers:
            tickers = source.load_tickers_from_index(limit=int(args.ticker_limit))
        print(f"FETCH day={args.event_date} tickers={len(tickers):,}", flush=True)
        day = source.fetch_day(event_date=str(args.event_date), tickers=tickers)
        print(f"FETCH DONE rows={day.rows_returned:,} seconds={day.fetch_seconds:.3f}", flush=True)
        engine = RollingMarketSampleEngine(config)
        engine.append_rows_by_ticker(day.rows_by_ticker)
        print("FETCH macro bars", flush=True)
        macro = source.fetch_macro_bars(start_date=str(args.event_date), end_date=str(args.event_date), tickers=tickers)
        print(f"FETCH macro bars done rows={len(macro.rows):,} seconds={macro.fetch_seconds:.3f}", flush=True)
        engine.load_macro_bars(macro)
        if not args.skip_q_live_contexts and day.rows_by_ticker:
            start_us, end_us = event_time_bounds(day.rows_by_ticker)
            print(f"FETCH q_live contexts start_us={start_us} end_us={end_us}", flush=True)
            started = time.perf_counter()
            contexts = source.fetch_q_live_contexts(start_timestamp_us=start_us, end_timestamp_us=end_us, tickers=tickers)
            seconds = time.perf_counter() - started
            counts = {name: len(rows) for name, rows in contexts.items()}
            engine.load_external_contexts(contexts)
            print(f"FETCH q_live contexts done seconds={seconds:.3f} counts={counts}", flush=True)
        return profile_engine(args, config, engine, rows_returned=day.rows_returned, fetch_seconds=day.fetch_seconds)
    finally:
        bytes_client.close()


def profile_engine(args: argparse.Namespace, config: RollingMarketDataConfig, engine: RollingMarketSampleEngine, *, rows_returned: int, fetch_seconds: float) -> int:
    started = time.perf_counter()
    samples = engine.build_ready_indices(max_samples=int(args.max_ready_samples))
    index_seconds = time.perf_counter() - started
    print(
        f"INDEX samples={len(samples):,} seconds={index_seconds:.3f} "
        f"samples_per_sec={(len(samples) / index_seconds) if index_seconds > 0 else 0.0:.1f}",
        flush=True,
    )
    if not samples:
        raise RuntimeError("No ready rolling samples were built. Increase synthetic-events/date coverage or lower context lags.")

    materialized = []
    for batch_id in range(int(args.materialize_batches)):
        lo = batch_id * int(config.batch_size)
        hi = min(lo + int(config.batch_size), len(samples))
        if hi <= lo:
            break
        batch_samples = samples[lo:hi]
        batch = engine.materialize_training_batch(batch_samples, batch_id=batch_id)
        materialized.append(batch)
        metrics = batch.profile.to_metrics(prefix="rolling_training") if batch.profile is not None else {}
        print(
            f"TRAIN_BATCH [{batch_id + 1}/{args.materialize_batches}] samples={batch.headers_uint8.shape[0]:,} "
            f"chunks={int(batch.context_mask.sum()):,} seconds={metrics.get('rolling_training/total_seconds', 0.0):.3f} "
            f"samples_per_sec={metrics.get('rolling_training/samples_per_second', 0.0):.1f} "
            f"labels={len(batch.labels)} macro={len(batch.macro_features)} global={len(batch.global_features)} "
            f"text={_shape_summary(batch.text_inputs)} xbrl={_shape_summary(batch.xbrl_inputs)} "
            f"external={len(batch.external_context)} shape_headers={tuple(batch.headers_uint8.shape)} "
            f"shape_events={tuple(batch.events_uint8.shape)}",
            flush=True,
        )
        if args.profile_production_gather:
            lookup = _fake_embedding_lookup(batch)
            prod = engine.materialize_production_batch(batch_samples, lookup, batch_id=batch_id)
            prod_metrics = prod.profile.to_metrics(prefix="rolling_prod") if prod.profile is not None else {}
            print(
                f"PROD_BATCH [{batch_id + 1}/{args.materialize_batches}] samples={prod.market_embeddings.shape[0]:,} "
                f"context={prod.market_embeddings.shape[1]:,} seconds={prod_metrics.get('rolling_prod/total_seconds', 0.0):.3f} "
                f"samples_per_sec={prod_metrics.get('rolling_prod/samples_per_second', 0.0):.1f}",
                flush=True,
            )

    payload = {
        "event_date": str(args.event_date),
        "rows_returned": int(rows_returned),
        "fetch_seconds": float(fetch_seconds),
        "index_seconds": float(index_seconds),
        "ready_samples": int(len(samples)),
        "batch_size": int(config.batch_size),
        "context_chunks": int(len(config.context_lags)),
        "carryover_events": int(config.carryover_events),
        "materialized_batches": int(len(materialized)),
        "materialized_samples": int(sum(batch.headers_uint8.shape[0] for batch in materialized)),
        "label_count": int(len(materialized[0].labels)) if materialized else 0,
        "macro_feature_count": int(len(materialized[0].macro_features)) if materialized else 0,
        "global_feature_count": int(len(materialized[0].global_features)) if materialized else 0,
        "external_context_count": int(len(materialized[0].external_context)) if materialized else 0,
        "text_inputs": _json_shape_summary(materialized[0].text_inputs) if materialized else {},
        "xbrl_inputs": _json_shape_summary(materialized[0].xbrl_inputs) if materialized else {},
    }
    if args.report_path is not None:
        write_profile_jsonl(args.report_path, payload)
        print(f"REPORT {args.report_path}", flush=True)
    return 0


def _fake_embedding_lookup(batch) -> dict[tuple[str, int], np.ndarray]:
    lookup: dict[tuple[str, int], np.ndarray] = {}
    rng = np.random.default_rng(17)
    for sample_idx, ticker in enumerate(batch.ticker.tolist()):
        for chunk_idx, origin in enumerate(batch.chunk_origin_ordinal[sample_idx].tolist()):
            if int(origin) == 0:
                continue
            lookup[(str(ticker).upper(), int(origin))] = rng.normal(0.0, 0.01, size=(32,)).astype(np.float32)
    return lookup


def _shape_summary(value) -> str:
    summary = _json_shape_summary(value)
    if not summary:
        return "{}"
    parts = []
    for key, shape in summary.items():
        parts.append(f"{key}:{shape}")
    return "{" + ", ".join(parts[:6]) + ("..." if len(parts) > 6 else "") + "}"


def _json_shape_summary(value) -> dict[str, tuple[int, ...]]:
    out: dict[str, tuple[int, ...]] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                for sub_key, sub_item in item.items():
                    if hasattr(sub_item, "shape"):
                        out[f"{key}.{sub_key}"] = tuple(int(dim) for dim in sub_item.shape)
            elif hasattr(item, "shape"):
                out[str(key)] = tuple(int(dim) for dim in item.shape)
    return out


def event_time_bounds(rows_by_ticker) -> tuple[int, int]:
    mins = []
    maxs = []
    for rows in rows_by_ticker.values():
        if rows.size:
            mins.append(int(rows["sip_timestamp_us"][0]))
            maxs.append(int(rows["sip_timestamp_us"][-1]))
    if not mins:
        return 0, 0
    return min(mins), max(maxs)


if __name__ == "__main__":
    raise SystemExit(main())
