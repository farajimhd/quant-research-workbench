from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.build_compact_canonical import (  # noqa: E402
    CompactCanonicalConfig,
    available_sessions,
    find_flatfile,
    header_columns,
    normalize_quote_frame,
    normalize_trade_frame,
    parse_ticker_list,
    raw_columns,
    uses_all_tickers,
)
from research.mlops.compact_events import DEFAULT_REFERENCE_DIR  # noqa: E402


ALL_TICKERS_SENTINEL = "__ALL_TICKERS__"
QUOTE_EVENT_TYPE = 0
TRADE_EVENT_TYPE = 1
ENCODING_VERSION = "ticker_compact_events_v1"
QUOTE_SIZE_UNIT_SWITCH_DATE = "2025-11-03"
DEFAULT_FLATFILES_ROOT = Path("D:/market-data/flatfiles/us_stocks_sip")
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/us_stocks_sip/ticker_compact_events_v1")
DEFAULT_TEMP_ROOT = DEFAULT_OUTPUT_ROOT / "_tmp_fragments"
DEFAULT_STATE_ROOT = DEFAULT_OUTPUT_ROOT / "_state"
DEFAULT_INDEX_ROOT = DEFAULT_OUTPUT_ROOT / "_index"

COMPACT_EVENT_COLUMNS = [
    "ticker",
    "session_date",
    "year_month",
    "sip_timestamp",
    "sequence_number",
    "event_type",
    "price_main_1e4",
    "price_aux_1e4",
    "size_1_bucket",
    "size_2_bucket",
    "small_size_1",
    "small_size_2",
    "exchange_1_id",
    "exchange_2_id",
    "tape_id",
    "condition_1_id",
    "condition_2_id",
    "condition_3_id",
    "condition_4_id",
    "correction_code",
    "bucket_id",
]


@dataclass(slots=True)
class TickerEventStoreConfig:
    flatfiles_root: Path = DEFAULT_FLATFILES_ROOT
    output_root: Path = DEFAULT_OUTPUT_ROOT
    temp_root: Path = DEFAULT_TEMP_ROOT
    state_root: Path = DEFAULT_STATE_ROOT
    index_root: Path = DEFAULT_INDEX_ROOT
    reference_dir: Path = DEFAULT_REFERENCE_DIR
    start_date: str = "2025-01-01"
    end_date: str = "2025-12-31"
    tickers: tuple[str, ...] = (ALL_TICKERS_SENTINEL,)
    bucket_count: int = 1024
    max_rows_per_fragment_file: int = 2_000_000
    session_timezone: str = "America/New_York"
    session_start_time_market: str = "04:00"
    session_end_time_market: str = "20:00"
    quote_size_lot_multiplier_before_2025_11_03: int = 100
    rebuild: bool = False


def parse_args() -> argparse.Namespace:
    defaults = TickerEventStoreConfig()
    parser = argparse.ArgumentParser(description="Build ticker/time sorted compact event rows from raw SIP quote/trade flatfiles.")
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--output-root", default=str(defaults.output_root))
    parser.add_argument("--temp-root", default="")
    parser.add_argument("--state-root", default="")
    parser.add_argument("--index-root", default="")
    parser.add_argument("--reference-dir", default=str(defaults.reference_dir))
    parser.add_argument("--start-date", default=defaults.start_date)
    parser.add_argument("--end-date", default=defaults.end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--bucket-count", type=int, default=defaults.bucket_count)
    parser.add_argument("--max-rows-per-fragment-file", type=int, default=defaults.max_rows_per_fragment_file)
    parser.add_argument("--processes", type=int, default=max(1, min(32, os.cpu_count() or 4)))
    parser.add_argument("--derive-processes", type=int, default=0)
    parser.add_argument("--compact-processes", type=int, default=0)
    parser.add_argument("--index-processes", type=int, default=0)
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--max-pending", type=int, default=0)
    parser.add_argument("--max-tasks-per-worker", type=int, default=0)
    parser.add_argument("--stage", choices=("all", "derive", "compact", "index"), default="all")
    parser.add_argument("--session-timezone", default=defaults.session_timezone)
    parser.add_argument("--session-start-time-market", default=defaults.session_start_time_market)
    parser.add_argument("--session-end-time-market", default=defaults.session_end_time_market)
    parser.add_argument("--quote-size-lot-multiplier-before-2025-11-03", type=int, default=100)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--manifest-name", default="ticker_event_store_manifest.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    output_root = Path(args.output_root)
    config = TickerEventStoreConfig(
        flatfiles_root=Path(args.flatfiles_root),
        output_root=output_root,
        temp_root=Path(args.temp_root) if args.temp_root else output_root / "_tmp_fragments",
        state_root=Path(args.state_root) if args.state_root else output_root / "_state",
        index_root=Path(args.index_root) if args.index_root else output_root / "_index",
        reference_dir=Path(args.reference_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=parse_ticker_list(args.tickers),
        bucket_count=max(1, int(args.bucket_count)),
        max_rows_per_fragment_file=max(1, int(args.max_rows_per_fragment_file)),
        session_timezone=args.session_timezone,
        session_start_time_market=args.session_start_time_market,
        session_end_time_market=args.session_end_time_market,
        quote_size_lot_multiplier_before_2025_11_03=args.quote_size_lot_multiplier_before_2025_11_03,
        rebuild=bool(args.rebuild),
    )
    sessions = available_sessions(config.flatfiles_root, config.start_date, config.end_date)
    derive_processes = args.derive_processes if args.derive_processes > 0 else args.processes
    compact_processes = args.compact_processes if args.compact_processes > 0 else args.processes
    index_processes = args.index_processes if args.index_processes > 0 else max(1, min(args.processes, 16))
    derive_pending = args.max_pending if args.max_pending > 0 else derive_processes * 2
    compact_pending = args.max_pending if args.max_pending > 0 else compact_processes * 2
    index_pending = args.max_pending if args.max_pending > 0 else index_processes * 2
    manifest_path = config.output_root / args.manifest_name
    payload = config_to_payload(config)
    started = time.time()

    print("=" * 96, flush=True)
    print("Ticker compact event store build", flush=True)
    print(f"flatfiles_root={config.flatfiles_root}", flush=True)
    print(f"output_root={config.output_root}", flush=True)
    print(f"temp_root={config.temp_root}", flush=True)
    print(f"state_root={config.state_root}", flush=True)
    print(f"index_root={config.index_root}", flush=True)
    print(f"reference_dir={config.reference_dir}", flush=True)
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions):,}", flush=True)
    print(f"bucket_count={config.bucket_count:,} tickers={','.join(config.tickers)}", flush=True)
    print(f"derive_processes={derive_processes} compact_processes={compact_processes} index_processes={index_processes}", flush=True)
    print(f"polars_threads_per_process={args.polars_threads_per_process}", flush=True)
    print("=" * 96, flush=True)

    if args.dry_run:
        for session in sessions[:5]:
            print(f"{session}: quotes={find_flatfile(config.flatfiles_root, 'quotes', session)}", flush=True)
            print(f"{session}: trades={find_flatfile(config.flatfiles_root, 'trades', session)}", flush=True)
        return

    if config.rebuild:
        if args.stage in {"all", "derive"}:
            remove_path(config.temp_root)
            remove_path(config.state_root / "derive")
        if args.stage == "all":
            remove_path(config.output_root / "events")
            remove_path(config.index_root)
            remove_path(config.state_root / "compact")
            remove_path(config.state_root / "index")
        elif args.stage == "compact":
            remove_path(config.output_root / "events")
            remove_path(config.index_root)
            remove_path(config.state_root / "compact")
            remove_path(config.state_root / "index")
        elif args.stage == "index":
            remove_path(config.index_root)
            remove_path(config.state_root / "index")
    for path in (config.output_root, config.temp_root, config.state_root, config.index_root):
        path.mkdir(parents=True, exist_ok=True)

    failed = 0
    if args.stage in {"all", "derive"}:
        derive_items = [{"session": session, "kind": kind} for session in sessions for kind in ("quotes", "trades")]
        failed += run_parallel(
            label="derive",
            items=derive_items,
            submit=lambda executor, item: executor.submit(derive_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=derive_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=derive_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)
    if args.stage in {"all", "compact"}:
        compact_items = discover_compact_items(config)
        failed += run_parallel(
            label="compact",
            items=compact_items,
            submit=lambda executor, item: executor.submit(compact_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=compact_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=compact_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)
    if args.stage in {"all", "index"}:
        index_items = discover_final_event_files(config)
        failed += run_parallel(
            label="index",
            items=index_items,
            submit=lambda executor, item: executor.submit(index_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=index_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=index_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)
        write_availability_index(config)
        write_dataset_schema(config)
    if args.stage == "all" and not args.keep_temp:
        remove_path(config.temp_root)

    print("=" * 96, flush=True)
    print(f"Ticker compact event store build complete in {(time.time() - started) / 60.0:.1f} minutes.", flush=True)
    print(f"Manifest: {manifest_path}", flush=True)
    print("=" * 96, flush=True)


def derive_worker(item: dict[str, str], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    session = item["session"]
    kind = item["kind"]
    key = f"{kind}:{session}"
    try:
        result = derive_session_kind(config, session=session, kind=kind)
        elapsed = time.time() - started
        print(
            f"FINISH derive {key} rows={result['rows']:,} files={result['files']:,} "
            f"elapsed={elapsed:.1f}s output={result['output_root']}",
            flush=True,
        )
        return result_row("derive", key, result["status"], result["rows"], elapsed, result)
    except BaseException:
        return failed_row("derive", key, time.time() - started)


def compact_worker(item: dict[str, Any], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    year_month = str(item["year_month"])
    bucket_id = int(item["bucket_id"])
    key = f"{year_month}:bucket={bucket_id:04d}"
    try:
        result = compact_bucket_month(config, year_month=year_month, bucket_id=bucket_id, paths=[Path(path) for path in item["paths"]])
        elapsed = time.time() - started
        print(f"FINISH compact {key} rows={result['rows']:,} elapsed={elapsed:.1f}s", flush=True)
        return result_row("compact", key, result["status"], result["rows"], elapsed, result)
    except BaseException:
        return failed_row("compact", key, time.time() - started)


def index_worker(item: dict[str, Any], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    path = Path(item["path"])
    key = f"{item['year_month']}:bucket={int(item['bucket_id']):04d}"
    try:
        result = build_index_part(config, path=path, year_month=str(item["year_month"]), bucket_id=int(item["bucket_id"]))
        elapsed = time.time() - started
        print(f"FINISH index {key} rows={result['rows']:,} tickers={result['tickers']:,} elapsed={elapsed:.1f}s", flush=True)
        return result_row("index", key, result["status"], result["rows"], elapsed, result)
    except BaseException:
        return failed_row("index", key, time.time() - started)


def derive_session_kind(config: TickerEventStoreConfig, *, session: str, kind: str) -> dict[str, Any]:
    input_path = find_flatfile(config.flatfiles_root, kind, session)
    if input_path is None:
        raise FileNotFoundError(f"Missing {kind} flatfile for {session} under {config.flatfiles_root}")
    output_root = config.temp_root / f"session={session}" / f"kind={kind}"
    state_path = success_path(config, "derive", f"{kind}_{session}")
    fingerprint = work_fingerprint(config, f"derive:{kind}:{session}", [input_path])
    if should_skip_success(state_path, fingerprint):
        return {"status": "skipped", "session": session, "kind": kind, "rows": success_rows(state_path), "files": success_files(state_path), "output_root": str(output_root)}
    cleanup_work_outputs(output_root, state_path)
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"START derive {kind}:{session} source={input_path}", flush=True)

    canonical_config = to_canonical_config(config)
    names = header_columns(input_path)
    required = {"ticker", "sip_timestamp", "sequence_number"}
    required |= {"bid_price", "ask_price", "bid_size", "ask_size"} if kind == "quotes" else {"price", "size"}
    missing = sorted(required - names)
    if missing:
        raise ValueError(f"{input_path} is missing required columns: {missing}")
    scan = pl.scan_csv(str(input_path), infer_schema_length=0, ignore_errors=True).with_row_index("raw_row_number", offset=2)
    selected = sorted((raw_columns(kind) & names) | required)
    raw = scan.select([pl.col("raw_row_number"), *[pl.col(column) for column in selected]])
    normalized = normalize_quote_frame(raw, names, canonical_config, session) if kind == "quotes" else normalize_trade_frame(raw, names, canonical_config, session)
    if not uses_all_tickers(config.tickers):
        normalized = normalized.filter(pl.col("ticker").is_in(list(config.tickers)))
    references = load_reference_maps(config.reference_dir)
    compact = compact_quote_frame(normalized, config, references) if kind == "quotes" else compact_trade_frame(normalized, config, references)
    compact = compact.select(COMPACT_EVENT_COLUMNS)
    partition = pl.PartitionBy(
        output_root,
        key=["year_month", "bucket_id"],
        include_key=True,
        max_rows_per_file=config.max_rows_per_fragment_file,
    )
    compact.sink_parquet(partition, compression="zstd", mkdir=True, maintain_order=False)
    files = list(output_root.rglob("*.parquet"))
    rows = sum_parquet_rows(files)
    write_success(
        state_path,
        {
            "stage": "derive",
            "work_id": f"derive:{kind}:{session}",
            "fingerprint": fingerprint,
            "input_files": [file_fingerprint(input_path)],
            "output_root": str(output_root),
            "row_count": rows,
            "file_count": len(files),
        },
    )
    return {"status": "ok", "session": session, "kind": kind, "rows": rows, "files": len(files), "output_root": str(output_root)}


def compact_quote_frame(frame: pl.LazyFrame, config: TickerEventStoreConfig, references: dict[str, dict[int, int]]) -> pl.LazyFrame:
    ask = pl.col("ask_price")
    bid = pl.col("bid_price")
    bid_size = pl.col("bid_size")
    ask_size = pl.col("ask_size")
    return frame.with_columns(
        pl.lit(QUOTE_EVENT_TYPE, dtype=pl.UInt8).alias("event_type"),
        price_1e4_expr(ask).alias("price_main_1e4"),
        price_1e4_expr(ask - bid).alias("price_aux_1e4"),
        size_bucket_expr(bid_size).alias("size_1_bucket"),
        size_bucket_expr(ask_size).alias("size_2_bucket"),
        small_size_expr(bid_size).alias("small_size_1"),
        small_size_expr(ask_size).alias("small_size_2"),
        dense_id_expr("bid_exchange", references["exchange"]).alias("exchange_1_id"),
        dense_id_expr("ask_exchange", references["exchange"]).alias("exchange_2_id"),
        dense_id_expr("tape", references["tape"]).alias("tape_id"),
        dense_id_expr("condition_1", references["condition"]).alias("condition_1_id"),
        dense_id_expr("condition_2", references["condition"]).alias("condition_2_id"),
        dense_id_expr("condition_3", references["condition"]).alias("condition_3_id"),
        dense_id_expr("condition_4", references["condition"]).alias("condition_4_id"),
        pl.lit(0, dtype=pl.UInt8).alias("correction_code"),
        bucket_expr(config.bucket_count),
    )


def compact_trade_frame(frame: pl.LazyFrame, config: TickerEventStoreConfig, references: dict[str, dict[int, int]]) -> pl.LazyFrame:
    price = pl.col("price")
    size = pl.col("size")
    return frame.with_columns(
        pl.lit(TRADE_EVENT_TYPE, dtype=pl.UInt8).alias("event_type"),
        price_1e4_expr(price).alias("price_main_1e4"),
        pl.lit(0, dtype=pl.Int64).alias("price_aux_1e4"),
        size_bucket_expr(size).alias("size_1_bucket"),
        pl.lit(0, dtype=pl.UInt8).alias("size_2_bucket"),
        small_size_expr(size).alias("small_size_1"),
        pl.lit(0, dtype=pl.UInt8).alias("small_size_2"),
        dense_id_expr("exchange", references["exchange"]).alias("exchange_1_id"),
        pl.lit(0, dtype=pl.UInt8).alias("exchange_2_id"),
        dense_id_expr("tape", references["tape"]).alias("tape_id"),
        dense_id_expr("condition_1", references["condition"]).alias("condition_1_id"),
        dense_id_expr("condition_2", references["condition"]).alias("condition_2_id"),
        dense_id_expr("condition_3", references["condition"]).alias("condition_3_id"),
        dense_id_expr("condition_4", references["condition"]).alias("condition_4_id"),
        correction_expr("correction").alias("correction_code"),
        bucket_expr(config.bucket_count),
    )


def price_1e4_expr(expr: pl.Expr) -> pl.Expr:
    return (expr.cast(pl.Float64) * 10_000.0).round().cast(pl.Int64)


def size_bucket_expr(expr: pl.Expr, *, scale: int = 16) -> pl.Expr:
    return ((1.0 + expr.cast(pl.Float64).clip(0.0, None) / 100.0).log() / math.log(2.0) * scale).round().clip(0, 255).cast(pl.UInt8)


def small_size_expr(expr: pl.Expr) -> pl.Expr:
    return ((expr.cast(pl.Float64) > 0.0) & (expr.cast(pl.Float64) < 100.0)).cast(pl.UInt8)


def dense_id_expr(column: str, mapping: dict[int, int]) -> pl.Expr:
    return pl.col(column).cast(pl.Int32, strict=False).replace_strict(mapping, default=0).cast(pl.UInt8)


def correction_expr(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Int32, strict=False).fill_null(0)
    return pl.when((value >= 0) & (value <= 14)).then(value).otherwise(15).cast(pl.UInt8)


def bucket_expr(bucket_count: int) -> pl.Expr:
    return (pl.col("ticker").hash(seed=17) % int(bucket_count)).cast(pl.UInt32).alias("bucket_id")


def compact_bucket_month(config: TickerEventStoreConfig, *, year_month: str, bucket_id: int, paths: list[Path]) -> dict[str, Any]:
    output_path = final_event_path(config, year_month, bucket_id)
    state_path = success_path(config, "compact", f"{year_month}_bucket_{bucket_id:04d}")
    fingerprint = work_fingerprint(config, f"compact:{year_month}:bucket={bucket_id:04d}", paths)
    if should_skip_success(state_path, fingerprint):
        return {"status": "skipped", "year_month": year_month, "bucket_id": bucket_id, "rows": success_rows(state_path), "output_path": str(output_path)}
    cleanup_work_outputs(output_path, state_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"START compact {year_month}:bucket={bucket_id:04d} files={len(paths):,}", flush=True)
    temp_output = output_path.with_name(output_path.name + ".tmp")
    if temp_output.exists():
        temp_output.unlink()
    (
        pl.scan_parquet([str(path) for path in paths])
        .select(COMPACT_EVENT_COLUMNS)
        .sort(["ticker", "sip_timestamp", "sequence_number", "event_type"])
        .sink_parquet(temp_output, compression="zstd", mkdir=True, maintain_order=True)
    )
    os.replace(temp_output, output_path)
    rows = parquet_row_count(output_path)
    write_success(
        state_path,
        {
            "stage": "compact",
            "work_id": f"compact:{year_month}:bucket={bucket_id:04d}",
            "fingerprint": fingerprint,
            "input_file_count": len(paths),
            "row_count": rows,
            "file_count": 1,
            "output_path": str(output_path),
        },
    )
    return {"status": "ok", "year_month": year_month, "bucket_id": bucket_id, "rows": rows, "output_path": str(output_path)}


def build_index_part(config: TickerEventStoreConfig, *, path: Path, year_month: str, bucket_id: int) -> dict[str, Any]:
    output_path = config.index_root / "parts" / f"year_month={year_month}" / f"bucket={bucket_id:04d}.parquet"
    state_path = success_path(config, "index", f"{year_month}_bucket_{bucket_id:04d}")
    fingerprint = work_fingerprint(config, f"index:{year_month}:bucket={bucket_id:04d}", [path])
    if should_skip_success(state_path, fingerprint):
        return {"status": "skipped", "year_month": year_month, "bucket_id": bucket_id, "rows": success_rows(state_path), "tickers": success_rows(state_path), "output_path": str(output_path)}
    cleanup_work_outputs(output_path, state_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.scan_parquet(str(path))
        .group_by("ticker")
        .agg(
            pl.len().alias("row_count"),
            pl.col("sip_timestamp").min().alias("min_timestamp_ns"),
            pl.col("sip_timestamp").max().alias("max_timestamp_ns"),
            pl.col("session_date").n_unique().alias("session_count"),
            (pl.col("event_type") == QUOTE_EVENT_TYPE).sum().alias("quote_count"),
            (pl.col("event_type") == TRADE_EVENT_TYPE).sum().alias("trade_count"),
        )
        .with_columns(
            pl.lit(year_month).alias("year_month"),
            pl.lit(bucket_id, dtype=pl.UInt32).alias("bucket_id"),
            pl.lit(str(path)).alias("file_path"),
        )
        .select(
            "ticker",
            "bucket_id",
            "year_month",
            "row_count",
            "quote_count",
            "trade_count",
            "session_count",
            "min_timestamp_ns",
            "max_timestamp_ns",
            "file_path",
        )
        .sort("ticker")
        .collect()
    )
    frame.write_parquet(output_path, compression="zstd")
    rows = int(frame["row_count"].sum()) if frame.height else 0
    write_success(
        state_path,
        {
            "stage": "index",
            "work_id": f"index:{year_month}:bucket={bucket_id:04d}",
            "fingerprint": fingerprint,
            "row_count": frame.height,
            "event_rows": rows,
            "output_path": str(output_path),
        },
    )
    return {"status": "ok", "year_month": year_month, "bucket_id": bucket_id, "rows": rows, "tickers": frame.height, "output_path": str(output_path)}


def write_availability_index(config: TickerEventStoreConfig) -> None:
    parts = sorted((config.index_root / "parts").glob("year_month=*/*.parquet"))
    output_path = config.index_root / "availability.parquet"
    if not parts:
        pl.DataFrame().write_parquet(output_path)
        return
    temp = output_path.with_name(output_path.name + ".tmp")
    (
        pl.scan_parquet([str(path) for path in parts])
        .sort(["ticker", "year_month"])
        .sink_parquet(temp, compression="zstd", mkdir=True, maintain_order=True)
    )
    os.replace(temp, output_path)
    print(f"Wrote availability index: {output_path}", flush=True)


def write_dataset_schema(config: TickerEventStoreConfig) -> None:
    payload = {
        "encoding_version": ENCODING_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "schema": {name: str(dtype) for name, dtype in compact_schema().items()},
        "sort_order": ["ticker", "sip_timestamp", "sequence_number", "event_type"],
        "partitioning": ["year_month", "bucket_id"],
        "bucket_count": config.bucket_count,
        "bucket_hash_seed": 17,
        "price_scale": "price_1e4 = round(price * 10000)",
        "output_root": str(config.output_root),
    }
    (config.index_root / "schema.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def discover_compact_items(config: TickerEventStoreConfig) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[Path]] = {}
    for path in sorted(config.temp_root.glob("session=*/kind=*/year_month=*/bucket_id=*/*.parquet")):
        year_month = ""
        bucket_id = -1
        for part in path.parts:
            if part.startswith("year_month="):
                year_month = part.split("=", 1)[1]
            elif part.startswith("bucket_id="):
                bucket_id = int(part.split("=", 1)[1])
        if year_month and bucket_id >= 0:
            grouped.setdefault((year_month, bucket_id), []).append(path)
    items = [
        {"year_month": year_month, "bucket_id": bucket_id, "paths": [str(path) for path in paths]}
        for (year_month, bucket_id), paths in sorted(grouped.items())
    ]
    print(f"Discovered {len(items):,} compact bucket-month items.", flush=True)
    return items


def discover_final_event_files(config: TickerEventStoreConfig) -> list[dict[str, Any]]:
    items = []
    for path in sorted((config.output_root / "events").glob("year_month=*/bucket=*/events.parquet")):
        year_month = ""
        bucket_id = -1
        for part in path.parts:
            if part.startswith("year_month="):
                year_month = part.split("=", 1)[1]
            elif part.startswith("bucket="):
                bucket_id = int(part.split("=", 1)[1])
        if year_month and bucket_id >= 0:
            items.append({"year_month": year_month, "bucket_id": bucket_id, "path": str(path)})
    print(f"Discovered {len(items):,} final event files for index.", flush=True)
    return items


def final_event_path(config: TickerEventStoreConfig, year_month: str, bucket_id: int) -> Path:
    return config.output_root / "events" / f"year_month={year_month}" / f"bucket={bucket_id:04d}" / "events.parquet"


def load_reference_maps(reference_dir: Path) -> dict[str, dict[int, int]]:
    return {
        "exchange": load_dense_id_map(reference_dir / "stock_exchanges.json"),
        "condition": load_dense_id_map(reference_dir / "stock_conditions.json"),
        "tape": load_dense_id_map(reference_dir / "stock_tapes.json"),
    }


def load_dense_id_map(path: Path) -> dict[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, int] = {}
    for row in payload.get("results", []):
        if row.get("id") is not None and row.get("dense_id") is not None:
            out[int(row["id"])] = int(row["dense_id"])
    return out


def to_canonical_config(config: TickerEventStoreConfig) -> CompactCanonicalConfig:
    return CompactCanonicalConfig(
        flatfiles_root=config.flatfiles_root,
        start_date=config.start_date,
        end_date=config.end_date,
        tickers=config.tickers,
        session_timezone=config.session_timezone,
        session_start_time_market=config.session_start_time_market,
        session_end_time_market=config.session_end_time_market,
        quote_size_lot_multiplier_before_2025_11_03=config.quote_size_lot_multiplier_before_2025_11_03,
        rebuild=config.rebuild,
    )


def compact_schema() -> dict[str, pl.DataType]:
    return {
        "ticker": pl.String,
        "session_date": pl.String,
        "year_month": pl.String,
        "sip_timestamp": pl.Int64,
        "sequence_number": pl.Int64,
        "event_type": pl.UInt8,
        "price_main_1e4": pl.Int64,
        "price_aux_1e4": pl.Int64,
        "size_1_bucket": pl.UInt8,
        "size_2_bucket": pl.UInt8,
        "small_size_1": pl.UInt8,
        "small_size_2": pl.UInt8,
        "exchange_1_id": pl.UInt8,
        "exchange_2_id": pl.UInt8,
        "tape_id": pl.UInt8,
        "condition_1_id": pl.UInt8,
        "condition_2_id": pl.UInt8,
        "condition_3_id": pl.UInt8,
        "condition_4_id": pl.UInt8,
        "correction_code": pl.UInt8,
        "bucket_id": pl.UInt32,
    }


def success_path(config: TickerEventStoreConfig, stage: str, name: str) -> Path:
    return config.state_root / stage / f"{safe_name(name)}.SUCCESS.json"


def should_skip_success(path: Path, fingerprint: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "ok" and payload.get("fingerprint") == fingerprint


def success_rows(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("row_count", 0))
    except Exception:
        return 0


def success_files(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("file_count", 0))
    except Exception:
        return 0


def write_success(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = {"status": "ok", "encoding_version": ENCODING_VERSION, "created_at": datetime.now().isoformat(timespec="seconds"), **payload}
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(output, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temp, path)


def cleanup_work_outputs(output_path: Path, state_path: Path) -> None:
    if state_path.exists():
        state_path.unlink()
    remove_path(output_path)


def work_fingerprint(config: TickerEventStoreConfig, work_id: str, inputs: Iterable[Path]) -> str:
    payload = {
        "work_id": work_id,
        "encoding_version": ENCODING_VERSION,
        "config": config_payload_for_hash(config),
        "inputs": [file_fingerprint(path) for path in sorted(inputs)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def config_payload_for_hash(config: TickerEventStoreConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("flatfiles_root", "output_root", "temp_root", "state_root", "index_root", "reference_dir"):
        payload[key] = str(payload[key])
    return payload


def config_to_payload(config: TickerEventStoreConfig) -> dict[str, Any]:
    return config_payload_for_hash(config)


def config_from_payload(payload: dict[str, Any]) -> TickerEventStoreConfig:
    values = dict(payload)
    for key in ("flatfiles_root", "output_root", "temp_root", "state_root", "index_root", "reference_dir"):
        values[key] = Path(values[key])
    values["tickers"] = tuple(values["tickers"])
    return TickerEventStoreConfig(**values)


def sum_parquet_rows(paths: list[Path]) -> int:
    return sum(parquet_row_count(path) for path in paths)


def parquet_row_count(path: Path) -> int:
    import pyarrow.parquet as pq

    return int(pq.ParquetFile(path).metadata.num_rows)


def run_parallel(
    *,
    label: str,
    items: list[Any],
    submit: Callable[[concurrent.futures.ProcessPoolExecutor, Any], concurrent.futures.Future[Any]],
    manifest_path: Path,
    processes: int,
    started: float,
    fail_fast: bool,
    heartbeat_seconds: float,
    max_pending: int,
    max_tasks_per_worker: int,
) -> int:
    if not items:
        print(f"{label}: no work items", flush=True)
        return 0
    failed = 0
    completed = 0
    submitted = 0
    pending_limit = max(1, max_pending)
    submitted_at: dict[Any, float] = {}
    future_labels: dict[Any, str] = {}
    item_iter = iter(enumerate(items, start=1))

    def submit_next(executor: concurrent.futures.ProcessPoolExecutor, pending: set[Any]) -> bool:
        nonlocal submitted
        try:
            index, item = next(item_iter)
        except StopIteration:
            return False
        future = submit(executor, item)
        pending.add(future)
        submitted += 1
        submitted_at[future] = time.time()
        future_labels[future] = item_label(item)
        print(f"[{index:,}/{len(items):,}] SUBMIT {label} {future_labels[future]}", flush=True)
        return True

    executor_kwargs: dict[str, Any] = {"max_workers": max(1, processes)}
    if max_tasks_per_worker > 0:
        executor_kwargs["max_tasks_per_child"] = max_tasks_per_worker
    with concurrent.futures.ProcessPoolExecutor(**executor_kwargs) as executor:
        pending: set[Any] = set()
        while len(pending) < min(pending_limit, len(items)) and submit_next(executor, pending):
            pass
        next_heartbeat = time.time() + max(1.0, heartbeat_seconds)
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=max(1.0, heartbeat_seconds), return_when=concurrent.futures.FIRST_COMPLETED)
            now = time.time()
            if not done:
                print_heartbeat(label, pending, future_labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
                continue
            for future in done:
                completed += 1
                item = future_labels[future]
                print(f"FINISH {label} {item} after {time.time() - submitted_at[future]:.1f}s", flush=True)
                try:
                    result = future.result()
                except BaseException:
                    result = failed_row(label, item, time.time() - started)
                append_jsonl(manifest_path, result)
                if result.get("status") == "failed":
                    failed += 1
                    print(result.get("error", ""), flush=True)
                    if fail_fast:
                        raise SystemExit(f"{label} failed for {item}")
                print(format_progress(result, completed, len(items), time.time() - started), flush=True)
                while len(pending) < pending_limit and submitted < len(items):
                    if not submit_next(executor, pending):
                        break
            if now >= next_heartbeat and pending:
                print_heartbeat(label, pending, future_labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
    return failed


def item_label(item: Any) -> str:
    if isinstance(item, dict):
        if "session" in item and "kind" in item:
            return f"{item['kind']}:{item['session']}"
        if "year_month" in item and "bucket_id" in item:
            return f"{item['year_month']}:bucket={int(item['bucket_id']):04d}"
    return str(item)


def print_heartbeat(label: str, pending: set[Any], future_labels: dict[Any, str], submitted_at: dict[Any, float], completed: int, total: int, started: float) -> None:
    now = time.time()
    longest = sorted(((now - submitted_at[future], future_labels[future]) for future in pending), reverse=True)[:5]
    formatted = ", ".join(f"{name}={seconds:.0f}s" for seconds, name in longest)
    print(
        f"HEARTBEAT {label}: completed={completed:,}/{total:,} running={len(pending):,} "
        f"elapsed_minutes={(now - started) / 60.0:.1f} longest=[{formatted}]",
        flush=True,
    )


def result_row(phase: str, key: str, status: str, rows: int, elapsed: float, details: dict[str, Any]) -> dict[str, Any]:
    return {"phase": phase, "key": key, "status": status, "rows": rows, "details": details, "elapsed_seconds": elapsed, "finished_at": datetime.now().isoformat(timespec="seconds")}


def failed_row(phase: str, key: str, elapsed: float) -> dict[str, Any]:
    return {"phase": phase, "key": key, "status": "failed", "rows": 0, "error": traceback.format_exc(), "elapsed_seconds": elapsed, "finished_at": datetime.now().isoformat(timespec="seconds")}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def format_progress(result: dict[str, Any], completed: int, total: int, elapsed: float) -> str:
    return f"[{completed:,}/{total:,}] {result.get('phase')} {result.get('key')} {result.get('status')} rows={result.get('rows')} item_seconds={float(result.get('elapsed_seconds', 0.0)):.1f} elapsed_minutes={elapsed / 60.0:.1f}"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value)


def remove_path(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == "__main__":
    main()
