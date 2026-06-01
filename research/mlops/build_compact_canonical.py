from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


ALL_TICKERS_SENTINEL = "__ALL_TICKERS__"
NANOSECONDS_PER_SECOND = 1_000_000_000
TEMP_TICKER_BUCKET_COUNT = 256
QUOTE_SIZE_UNIT_SWITCH_DATE = "2025-11-03"

QUOTE_CANONICAL_COLUMNS = [
    "ticker",
    "session_date",
    "year_month",
    "sip_timestamp",
    "sequence_number",
    "bid_price",
    "ask_price",
    "bid_size",
    "ask_size",
    "bid_exchange",
    "ask_exchange",
    "tape",
    "condition_count",
    "condition_1",
    "condition_2",
    "condition_3",
    "condition_4",
]

TRADE_CANONICAL_COLUMNS = [
    "ticker",
    "session_date",
    "year_month",
    "sip_timestamp",
    "sequence_number",
    "price",
    "size",
    "exchange",
    "tape",
    "condition_count",
    "condition_1",
    "condition_2",
    "condition_3",
    "condition_4",
    "correction",
]


@dataclass(slots=True)
class CompactCanonicalConfig:
    flatfiles_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip")
    canonical_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_compact_v1")
    temp_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/_tmp_compact_canonical_parts")
    issue_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_compact_v1_issues")
    start_date: str = "2025-11-01"
    end_date: str = "2025-12-05"
    tickers: tuple[str, ...] = (ALL_TICKERS_SENTINEL,)
    session_timezone: str = "America/New_York"
    session_start_time_market: str = "04:00"
    session_end_time_market: str = "20:00"
    quote_size_lot_multiplier_before_2025_11_03: int = 100
    max_rows_per_temp_file: int = 1_000_000
    rebuild: bool = False


def parse_args() -> argparse.Namespace:
    defaults = CompactCanonicalConfig()
    parser = argparse.ArgumentParser(
        description="Build compact canonical quote/trade parquet files for byte-level EventsChunk loaders."
    )
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--canonical-root", default=str(defaults.canonical_root))
    parser.add_argument("--temp-root", default=str(defaults.temp_root))
    parser.add_argument("--issue-root", default=str(defaults.issue_root))
    parser.add_argument("--start-date", default=defaults.start_date)
    parser.add_argument("--end-date", default=defaults.end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--processes", type=int, default=max(1, min(16, os.cpu_count() or 4)))
    parser.add_argument("--normalize-processes", type=int, default=0)
    parser.add_argument("--merge-processes", type=int, default=0)
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--max-pending", type=int, default=0)
    parser.add_argument("--max-tasks-per-worker", type=int, default=0)
    parser.add_argument("--session-timezone", default=defaults.session_timezone)
    parser.add_argument("--session-start-time-market", default=defaults.session_start_time_market)
    parser.add_argument("--session-end-time-market", default=defaults.session_end_time_market)
    parser.add_argument("--quote-size-lot-multiplier-before-2025-11-03", type=int, default=100)
    parser.add_argument("--max-rows-per-temp-file", type=int, default=defaults.max_rows_per_temp_file)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--manifest-name", default="compact_canonical_manifest.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    config = CompactCanonicalConfig(
        flatfiles_root=Path(args.flatfiles_root),
        canonical_root=Path(args.canonical_root),
        temp_root=Path(args.temp_root),
        issue_root=Path(args.issue_root),
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=parse_ticker_list(args.tickers),
        session_timezone=args.session_timezone,
        session_start_time_market=args.session_start_time_market,
        session_end_time_market=args.session_end_time_market,
        quote_size_lot_multiplier_before_2025_11_03=args.quote_size_lot_multiplier_before_2025_11_03,
        max_rows_per_temp_file=args.max_rows_per_temp_file,
        rebuild=args.rebuild,
    )
    sessions = available_sessions(config.flatfiles_root, config.start_date, config.end_date)
    normalize_processes = args.normalize_processes if args.normalize_processes > 0 else args.processes
    merge_processes = args.merge_processes if args.merge_processes > 0 else args.processes
    normalize_pending = args.max_pending if args.max_pending > 0 else normalize_processes * 2
    merge_pending = args.max_pending if args.max_pending > 0 else merge_processes * 2
    manifest_path = config.canonical_root.parent / args.manifest_name

    print("=" * 96, flush=True)
    print("Compact canonical preprocessing", flush=True)
    print(f"flatfiles_root={config.flatfiles_root}", flush=True)
    print(f"canonical_root={config.canonical_root}", flush=True)
    print(f"temp_root={config.temp_root}", flush=True)
    print(f"issue_root={config.issue_root}", flush=True)
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions):,}", flush=True)
    print(f"tickers={','.join(config.tickers)}", flush=True)
    print(f"normalize_processes={normalize_processes} merge_processes={merge_processes}", flush=True)
    print(f"polars_threads_per_process={args.polars_threads_per_process}", flush=True)
    print("=" * 96, flush=True)

    if args.dry_run:
        for session in sessions[:5]:
            print(f"{session}: quotes={find_flatfile(config.flatfiles_root, 'quotes', session)}")
            print(f"{session}: trades={find_flatfile(config.flatfiles_root, 'trades', session)}")
        return

    started = time.time()
    if args.merge_only and args.normalize_only:
        raise SystemExit("--merge-only and --normalize-only cannot be used together.")
    if args.merge_only and config.rebuild:
        remove_path(config.canonical_root)
    elif config.rebuild:
        remove_path(config.temp_root)
        remove_path(config.canonical_root)
        remove_path(config.issue_root)
    config.temp_root.mkdir(parents=True, exist_ok=True)
    config.canonical_root.mkdir(parents=True, exist_ok=True)
    config.issue_root.mkdir(parents=True, exist_ok=True)

    payload = config_to_payload(config)
    failed = 0
    if not args.merge_only:
        normalize_items = [{"session": session, "kind": kind} for session in sessions for kind in ("quotes", "trades")]
        failed = run_parallel(
            label="normalize",
            items=normalize_items,
            submit=lambda executor, item: executor.submit(normalize_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=normalize_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=normalize_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)
    else:
        print("Skipping normalize phase because --merge-only was set.", flush=True)

    if args.normalize_only:
        print("Skipping merge phase because --normalize-only was set.", flush=True)
        print("=" * 96, flush=True)
        print(f"Compact canonical normalization complete in {(time.time() - started) / 60.0:.1f} minutes.", flush=True)
        print(f"Manifest: {manifest_path}", flush=True)
        print("=" * 96, flush=True)
        return

    groups = discover_temp_groups(config.temp_root)
    print(f"Discovered {len(groups):,} temp groups for canonical merge.", flush=True)
    merge_items = [
        {"kind": kind, "year_month": year_month, "ticker_bucket": ticker_bucket, "paths": [str(path) for path in paths]}
        for (kind, year_month, ticker_bucket), paths in sorted(groups.items())
    ]
    failed += run_parallel(
        label="merge",
        items=merge_items,
        submit=lambda executor, item: executor.submit(merge_worker, item, payload, args.polars_threads_per_process),
        manifest_path=manifest_path,
        processes=merge_processes,
        started=started,
        fail_fast=args.fail_fast,
        heartbeat_seconds=args.heartbeat_seconds,
        max_pending=merge_pending,
        max_tasks_per_worker=args.max_tasks_per_worker,
    )
    if failed:
        raise SystemExit(1)
    if not args.keep_temp:
        remove_path(config.temp_root)
    print("=" * 96, flush=True)
    print(f"Compact canonical preprocessing complete in {(time.time() - started) / 60.0:.1f} minutes.", flush=True)
    print(f"Manifest: {manifest_path}", flush=True)
    print("=" * 96, flush=True)


def normalize_worker(item: dict[str, str], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    session = item["session"]
    kind = item["kind"]
    key = f"{kind}:{session}"
    try:
        print(f"START normalize {key}", flush=True)
        result = normalize_session_kind(config, session, kind)
        elapsed = time.time() - started
        print(f"FINISH normalize {key} files={result['files']} issues={result['issue_rows']} elapsed={elapsed:.1f}s", flush=True)
        return result_row("normalize", key, "ok", result["rows"], elapsed, result)
    except BaseException:
        return failed_row("normalize", key, time.time() - started)


def merge_worker(item: dict[str, Any], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    key = f"{item['kind']}:bucket={item['ticker_bucket']}:{item['year_month']}"
    try:
        print(f"START merge {key} parts={len(item['paths'])}", flush=True)
        result = merge_temp_group_to_canonical(
            config,
            kind=item["kind"],
            year_month=item["year_month"],
            paths=[Path(path) for path in item["paths"]],
        )
        elapsed = time.time() - started
        print(f"FINISH merge {key} tickers={result['ticker_files']} rows={result['rows']:,} elapsed={elapsed:.1f}s", flush=True)
        return result_row("merge", key, "ok", result["rows"], elapsed, result)
    except BaseException:
        return failed_row("merge", key, time.time() - started)


def normalize_session_kind(config: CompactCanonicalConfig, session: str, kind: str) -> dict[str, Any]:
    path = find_flatfile(config.flatfiles_root, kind, session)
    if path is None:
        raise FileNotFoundError(f"Missing {kind} flatfile for {session} under {config.flatfiles_root}")
    names = header_columns(path)
    output_root = config.temp_root / kind / f"session={session}"
    if config.rebuild:
        remove_path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    required = {"ticker", "sip_timestamp", "sequence_number"}
    required |= {"bid_price", "ask_price", "bid_size", "ask_size"} if kind == "quotes" else {"price", "size"}
    missing = sorted(required - names)
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    scan = pl.scan_csv(str(path), infer_schema_length=0, ignore_errors=True).with_row_index("raw_row_number", offset=2)
    selected = sorted((raw_columns(kind) & names) | required)
    frame = scan.select([pl.col("raw_row_number"), *[pl.col(column) for column in selected]])
    issue_frame = build_issue_frame(frame, names, config, session, kind, path)
    issue_result = write_issue_csv(issue_frame, config, session, kind)
    normalized = normalize_quote_frame(frame, names, config, session) if kind == "quotes" else normalize_trade_frame(frame, names, config, session)
    if not uses_all_tickers(config.tickers):
        normalized = normalized.filter(pl.col("ticker").is_in(list(config.tickers)))
    normalized = normalized.select(["ticker_bucket", *canonical_columns(kind)])
    partition = pl.PartitionBy(
        output_root,
        key=["year_month", "ticker_bucket"],
        include_key=True,
        max_rows_per_file=config.max_rows_per_temp_file,
    )
    normalized.sink_parquet(partition, compression="zstd", mkdir=True, maintain_order=True)
    files = list(output_root.rglob("*.parquet"))
    return {
        "session": session,
        "kind": kind,
        "source": str(path),
        "output_root": str(output_root),
        "files": len(files),
        "rows": -1,
        **issue_result,
    }


def normalize_quote_frame(frame: pl.LazyFrame, names: set[str], config: CompactCanonicalConfig, session: str) -> pl.LazyFrame:
    return prepare_quote_frame(frame, names, config, session).filter(quote_canonical_valid_expr())


def prepare_quote_frame(frame: pl.LazyFrame, names: set[str], config: CompactCanonicalConfig, session: str) -> pl.LazyFrame:
    multiplier = config.quote_size_lot_multiplier_before_2025_11_03 if session < QUOTE_SIZE_UNIT_SWITCH_DATE else 1
    return (
        frame.with_columns(
            normalized_ticker_expr(),
            pl.lit(session).alias("session_date"),
            pl.lit(session[:7]).alias("year_month"),
            pl.col("ticker").cast(pl.String).alias("raw_ticker"),
            pl.col("sip_timestamp").cast(pl.String).alias("raw_sip_timestamp"),
            pl.col("sequence_number").cast(pl.String).alias("raw_sequence_number"),
            pl.col("bid_price").cast(pl.String).alias("raw_bid_price"),
            pl.col("ask_price").cast(pl.String).alias("raw_ask_price"),
            pl.col("bid_size").cast(pl.String).alias("raw_bid_size"),
            pl.col("ask_size").cast(pl.String).alias("raw_ask_size"),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            pl.col("bid_price").cast(pl.Float64, strict=False),
            pl.col("ask_price").cast(pl.Float64, strict=False),
            (pl.col("bid_size").cast(pl.Float64, strict=False).fill_null(0.0) * float(multiplier)).alias("bid_size"),
            (pl.col("ask_size").cast(pl.Float64, strict=False).fill_null(0.0) * float(multiplier)).alias("ask_size"),
            optional_int_expr("bid_exchange", names, dtype=pl.Int32),
            optional_int_expr("ask_exchange", names, dtype=pl.Int32),
            optional_int_expr("tape", names, dtype=pl.Int32),
            *condition_slot_exprs("conditions", names),
        )
        .with_columns(session_timestamp_filter_expr(config, session).alias("is_in_session"))
        .with_columns(ticker_bucket_expr())
    )


def normalize_trade_frame(frame: pl.LazyFrame, names: set[str], config: CompactCanonicalConfig, session: str) -> pl.LazyFrame:
    return prepare_trade_frame(frame, names, config, session).filter(trade_canonical_valid_expr())


def prepare_trade_frame(frame: pl.LazyFrame, names: set[str], config: CompactCanonicalConfig, session: str) -> pl.LazyFrame:
    return (
        frame.with_columns(
            normalized_ticker_expr(),
            pl.lit(session).alias("session_date"),
            pl.lit(session[:7]).alias("year_month"),
            pl.col("ticker").cast(pl.String).alias("raw_ticker"),
            pl.col("sip_timestamp").cast(pl.String).alias("raw_sip_timestamp"),
            pl.col("sequence_number").cast(pl.String).alias("raw_sequence_number"),
            pl.col("price").cast(pl.String).alias("raw_price"),
            pl.col("size").cast(pl.String).alias("raw_size"),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            pl.col("price").cast(pl.Float64, strict=False),
            pl.col("size").cast(pl.Float64, strict=False).fill_null(0.0),
            optional_int_expr("exchange", names, dtype=pl.Int32),
            optional_int_expr("tape", names, dtype=pl.Int32),
            optional_int_expr("correction", names, dtype=pl.Int32),
            *condition_slot_exprs("conditions", names),
        )
        .with_columns(session_timestamp_filter_expr(config, session).alias("is_in_session"))
        .with_columns(ticker_bucket_expr())
    )


def build_issue_frame(frame: pl.LazyFrame, names: set[str], config: CompactCanonicalConfig, session: str, kind: str, source: Path) -> pl.LazyFrame:
    if kind == "quotes":
        prepared = prepare_quote_frame(frame, names, config, session)
        return build_quote_issue_frame(prepared, source)
    prepared = prepare_trade_frame(frame, names, config, session)
    return build_trade_issue_frame(prepared, source)


def build_quote_issue_frame(prepared: pl.LazyFrame, source: Path) -> pl.LazyFrame:
    issue_filter = (
        pl.col("sip_timestamp").is_null()
        | (pl.col("is_in_session") & (~quote_canonical_valid_expr() | quote_size_issue_expr()))
    )
    return (
        prepared.with_columns(
            pl.lit("quotes").alias("kind"),
            pl.lit(str(source)).alias("source_path"),
            quote_issue_reason_expr(),
            quote_resolution_hint_expr(),
            (~quote_canonical_valid_expr()).fill_null(True).alias("dropped_by_canonical"),
        )
        .filter(issue_filter)
        .select(
            [
                "kind",
                "session_date",
                "source_path",
                "raw_row_number",
                "dropped_by_canonical",
                "issue_reason",
                "resolution_hint",
                "raw_ticker",
                "ticker",
                "raw_sip_timestamp",
                "sip_timestamp",
                "is_in_session",
                "raw_sequence_number",
                "sequence_number",
                "raw_bid_price",
                "bid_price",
                "raw_ask_price",
                "ask_price",
                "raw_bid_size",
                "bid_size",
                "raw_ask_size",
                "ask_size",
                "bid_exchange",
                "ask_exchange",
                "tape",
                "condition_count",
                "condition_1",
                "condition_2",
                "condition_3",
                "condition_4",
            ]
        )
    )


def build_trade_issue_frame(prepared: pl.LazyFrame, source: Path) -> pl.LazyFrame:
    issue_filter = pl.col("sip_timestamp").is_null() | (pl.col("is_in_session") & ~trade_canonical_valid_expr())
    return (
        prepared.with_columns(
            pl.lit("trades").alias("kind"),
            pl.lit(str(source)).alias("source_path"),
            trade_issue_reason_expr(),
            trade_resolution_hint_expr(),
            (~trade_canonical_valid_expr()).fill_null(True).alias("dropped_by_canonical"),
        )
        .filter(issue_filter)
        .select(
            [
                "kind",
                "session_date",
                "source_path",
                "raw_row_number",
                "dropped_by_canonical",
                "issue_reason",
                "resolution_hint",
                "raw_ticker",
                "ticker",
                "raw_sip_timestamp",
                "sip_timestamp",
                "is_in_session",
                "raw_sequence_number",
                "sequence_number",
                "raw_price",
                "price",
                "raw_size",
                "size",
                "exchange",
                "tape",
                "condition_count",
                "condition_1",
                "condition_2",
                "condition_3",
                "condition_4",
                "correction",
            ]
        )
    )


def write_issue_csv(frame: pl.LazyFrame, config: CompactCanonicalConfig, session: str, kind: str) -> dict[str, Any]:
    output_path = config.issue_root / kind / f"session={session}" / "issues.csv"
    if config.rebuild:
        remove_path(output_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.sink_csv(output_path, mkdir=True, maintain_order=True)
    issue_count = count_csv_data_rows(output_path)
    if issue_count <= 0:
        remove_path(output_path)
        return {"issue_rows": 0, "issue_path": ""}
    return {"issue_rows": issue_count, "issue_path": str(output_path)}


def count_csv_data_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def quote_canonical_valid_expr() -> pl.Expr:
    return (
        pl.col("is_in_session")
        & (pl.col("ticker") != "")
        & (pl.col("bid_price") > 0.0)
        & (pl.col("ask_price") > 0.0)
        & (pl.col("ask_price") >= pl.col("bid_price"))
    )


def trade_canonical_valid_expr() -> pl.Expr:
    return (
        pl.col("is_in_session")
        & (pl.col("ticker") != "")
        & (pl.col("price") > 0.0)
        & (pl.col("size") > 0.0)
    )


def quote_size_issue_expr() -> pl.Expr:
    bid_size_raw = pl.col("raw_bid_size").cast(pl.Float64, strict=False)
    ask_size_raw = pl.col("raw_ask_size").cast(pl.Float64, strict=False)
    return bid_size_raw.is_null() | ask_size_raw.is_null() | (bid_size_raw <= 0.0) | (ask_size_raw <= 0.0)


def quote_issue_reason_expr() -> pl.Expr:
    bid_size_raw = pl.col("raw_bid_size").cast(pl.Float64, strict=False)
    ask_size_raw = pl.col("raw_ask_size").cast(pl.Float64, strict=False)
    return reason_expr(
        [
            (pl.col("sip_timestamp").is_null(), "invalid_sip_timestamp"),
            (pl.col("is_in_session") & (pl.col("ticker") == ""), "invalid_ticker"),
            (pl.col("is_in_session") & pl.col("bid_price").is_null(), "missing_or_invalid_bid_price"),
            (pl.col("is_in_session") & pl.col("ask_price").is_null(), "missing_or_invalid_ask_price"),
            (pl.col("is_in_session") & (pl.col("bid_price") <= 0.0) & (pl.col("ask_price") > 0.0), "one_sided_ask_quote"),
            (pl.col("is_in_session") & (pl.col("ask_price") <= 0.0) & (pl.col("bid_price") > 0.0), "one_sided_bid_quote"),
            (pl.col("is_in_session") & (pl.col("bid_price") <= 0.0) & (pl.col("ask_price") <= 0.0), "empty_quote"),
            (
                pl.col("is_in_session")
                & pl.col("bid_price").is_not_null()
                & pl.col("ask_price").is_not_null()
                & (pl.col("bid_price") > 0.0)
                & (pl.col("ask_price") > 0.0)
                & (pl.col("ask_price") < pl.col("bid_price")),
                "ask_below_bid",
            ),
            (pl.col("is_in_session") & (pl.col("bid_price") > 0.0) & bid_size_raw.is_null(), "missing_or_invalid_bid_size"),
            (pl.col("is_in_session") & (pl.col("ask_price") > 0.0) & ask_size_raw.is_null(), "missing_or_invalid_ask_size"),
            (pl.col("is_in_session") & (pl.col("bid_price") > 0.0) & (bid_size_raw <= 0.0), "non_positive_bid_size"),
            (pl.col("is_in_session") & (pl.col("ask_price") > 0.0) & (ask_size_raw <= 0.0), "non_positive_ask_size"),
        ]
    )


def quote_resolution_hint_expr() -> pl.Expr:
    return (
        pl.when(pl.col("sip_timestamp").is_null())
        .then(pl.lit("skip_row_invalid_time"))
        .when(pl.col("is_in_session") & (pl.col("ticker") == ""))
        .then(pl.lit("skip_row_invalid_ticker"))
        .when(pl.col("is_in_session") & (pl.col("bid_price") <= 0.0) & (pl.col("ask_price") <= 0.0))
        .then(pl.lit("skip_empty_quote_no_nbbo"))
        .when(pl.col("is_in_session") & (pl.col("ask_price") <= 0.0) & (pl.col("bid_price") > 0.0))
        .then(pl.lit("skip_one_sided_bid_quote_or_forward_fill_if_model_supports_l1_state"))
        .when(pl.col("is_in_session") & (pl.col("bid_price") <= 0.0) & (pl.col("ask_price") > 0.0))
        .then(pl.lit("skip_one_sided_ask_quote_or_forward_fill_if_model_supports_l1_state"))
        .when(pl.col("is_in_session") & (pl.col("bid_price") > 0.0) & (pl.col("ask_price") > 0.0) & (pl.col("ask_price") < pl.col("bid_price")))
        .then(pl.lit("skip_crossed_quote_for_current_bid_ask_spread_encoding"))
        .when(pl.col("is_in_session") & quote_size_issue_expr())
        .then(pl.lit("keep_only_after_confirming_size_zero_is_valid_for_this_quote_side"))
        .otherwise(pl.lit("review"))
        .alias("resolution_hint")
    )


def trade_issue_reason_expr() -> pl.Expr:
    return reason_expr(
        [
            (pl.col("sip_timestamp").is_null(), "invalid_sip_timestamp"),
            (pl.col("is_in_session") & (pl.col("ticker") == ""), "invalid_ticker"),
            (pl.col("is_in_session") & pl.col("price").is_null(), "missing_or_invalid_price"),
            (pl.col("is_in_session") & (pl.col("price") <= 0.0), "non_positive_price"),
            (pl.col("is_in_session") & pl.col("size").is_null(), "missing_or_invalid_size"),
            (pl.col("is_in_session") & (pl.col("size") <= 0.0), "non_positive_size"),
        ]
    )


def trade_resolution_hint_expr() -> pl.Expr:
    return (
        pl.when(pl.col("sip_timestamp").is_null())
        .then(pl.lit("skip_row_invalid_time"))
        .when(pl.col("is_in_session") & (pl.col("ticker") == ""))
        .then(pl.lit("skip_row_invalid_ticker"))
        .when(pl.col("is_in_session") & (pl.col("price") <= 0.0))
        .then(pl.lit("skip_trade_without_positive_price"))
        .when(pl.col("is_in_session") & (pl.col("size") <= 0.0))
        .then(pl.lit("skip_trade_without_positive_size"))
        .otherwise(pl.lit("review"))
        .alias("resolution_hint")
    )


def reason_expr(reason_pairs: list[tuple[pl.Expr, str]]) -> pl.Expr:
    return pl.concat_str(
        [
            pl.when(condition).then(pl.lit(reason + "|")).otherwise(pl.lit(""))
            for condition, reason in reason_pairs
        ]
    ).str.strip_chars_end("|").alias("issue_reason")


def merge_temp_group_to_canonical(
    config: CompactCanonicalConfig,
    *,
    kind: str,
    year_month: str,
    paths: list[Path],
) -> dict[str, Any]:
    columns = canonical_columns(kind)
    frame = scan_parquet_paths(paths).select(columns)
    counts = frame.group_by("ticker").agg(pl.len().alias("rows")).collect()
    rows = int(counts["rows"].sum()) if counts.height else 0
    output_base = config.canonical_root / kind
    output_base.mkdir(parents=True, exist_ok=True)
    for row in counts.sort("ticker").iter_rows(named=True):
        ticker = str(row["ticker"])
        output_path = canonical_ticker_path(config, kind, ticker, year_month)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        (
            frame.filter(pl.col("ticker") == ticker)
            .sort(["sip_timestamp", "sequence_number"])
            .sink_parquet(output_path, compression="zstd", mkdir=True, maintain_order=True)
        )
    return {
        "kind": kind,
        "year_month": year_month,
        "rows": rows,
        "ticker_files": int(counts.height),
        "output_root": str(output_base),
    }


def canonical_ticker_path(config: CompactCanonicalConfig, kind: str, ticker: str, year_month: str) -> Path:
    return config.canonical_root / kind / f"ticker={safe_path_part(ticker)}" / f"{year_month}.parquet"


def canonical_file_provider(config: CompactCanonicalConfig, kind: str, year_month: str) -> Callable[[Any], Path]:
    def provider(args: Any) -> Path:
        ticker = str(args.partition_keys.get_column("ticker")[0])
        path = canonical_ticker_path(config, kind, ticker, year_month)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return provider


def raw_columns(kind: str) -> set[str]:
    if kind == "quotes":
        return {
            "ticker",
            "sip_timestamp",
            "sequence_number",
            "conditions",
            "tape",
            "bid_price",
            "ask_price",
            "bid_size",
            "ask_size",
            "bid_exchange",
            "ask_exchange",
        }
    return {
        "ticker",
        "sip_timestamp",
        "sequence_number",
        "conditions",
        "correction",
        "tape",
        "price",
        "size",
        "exchange",
    }


def canonical_columns(kind: str) -> list[str]:
    return QUOTE_CANONICAL_COLUMNS if kind == "quotes" else TRADE_CANONICAL_COLUMNS


def condition_slot_exprs(column: str, names: set[str]) -> list[pl.Expr]:
    if column not in names:
        return [
            pl.lit(0, dtype=pl.Int32).alias("condition_count"),
            pl.lit(0, dtype=pl.Int32).alias("condition_1"),
            pl.lit(0, dtype=pl.Int32).alias("condition_2"),
            pl.lit(0, dtype=pl.Int32).alias("condition_3"),
            pl.lit(0, dtype=pl.Int32).alias("condition_4"),
        ]
    text = pl.col(column).cast(pl.String).fill_null("").str.strip_chars()
    parts = text.str.split(",")
    return [
        pl.when(text.str.len_chars() > 0).then(text.str.count_matches(",") + 1).otherwise(0).cast(pl.Int32).alias("condition_count"),
        *[
            parts.list.get(index, null_on_oob=True).cast(pl.Int32, strict=False).fill_null(0).alias(f"condition_{index + 1}")
            for index in range(4)
        ],
    ]


def optional_int_expr(column: str, names: set[str], *, dtype: pl.DataType = pl.Int64) -> pl.Expr:
    if column in names:
        return pl.col(column).cast(dtype, strict=False).fill_null(0).alias(column)
    return pl.lit(0, dtype=dtype).alias(column)


def normalized_ticker_expr() -> pl.Expr:
    return pl.col("ticker").cast(pl.String).str.to_uppercase().str.strip_chars().fill_null("").alias("ticker")


def ticker_bucket_expr(bucket_count: int = TEMP_TICKER_BUCKET_COUNT) -> pl.Expr:
    return (pl.col("ticker").hash(seed=17) % int(bucket_count)).cast(pl.Int32).alias("ticker_bucket")


def session_timestamp_filter_expr(config: CompactCanonicalConfig, session: str) -> pl.Expr:
    timestamp = pl.col("sip_timestamp").cast(pl.Int64, strict=False)
    start_ns, end_ns = session_market_window_utc_ns(config, session)
    return (timestamp >= start_ns) & (timestamp < end_ns)


def session_market_window_utc_ns(config: CompactCanonicalConfig, session: str) -> tuple[int, int]:
    timezone = ZoneInfo(config.session_timezone)
    session_day = date.fromisoformat(session)
    start_local = datetime.combine(session_day, parse_market_time(config.session_start_time_market), tzinfo=timezone)
    end_local = datetime.combine(session_day, parse_market_time(config.session_end_time_market), tzinfo=timezone)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return int(start_local.timestamp() * NANOSECONDS_PER_SECOND), int(end_local.timestamp() * NANOSECONDS_PER_SECOND)


def parse_market_time(value: str) -> datetime_time:
    hour_text, minute_text = value.split(":", 1)
    return datetime_time(hour=int(hour_text), minute=int(minute_text))


def find_flatfile(flatfiles_root: Path, kind: str, session: str) -> Path | None:
    roots = {"quotes": ("quotes_v1", "quotes"), "trades": ("trades_v1", "trades")}[kind]
    year, month, _ = session.split("-")
    filenames = (f"{session}.csv.gz", f"{session}.csv", f"{session}.gz")
    for candidate_root in flatfile_root_candidates(flatfiles_root):
        for root_name in roots:
            base = candidate_root / root_name
            for filename in filenames:
                for candidate in (base / year / month / filename, base / year / filename, base / filename):
                    if candidate.exists():
                        return candidate
    for candidate_root in flatfile_root_candidates(flatfiles_root):
        for root_name in roots:
            base = candidate_root / root_name
            if base.exists():
                matches = sorted(base.rglob(f"*{session}*.csv*"))
                if matches:
                    return matches[0]
    return None


def flatfile_root_candidates(flatfiles_root: Path) -> tuple[Path, ...]:
    candidates = [flatfiles_root]
    if flatfiles_root.name == "us_stock_sip":
        candidates.append(flatfiles_root.with_name("us_stocks_sip"))
    elif flatfiles_root.name == "us_stocks_sip":
        candidates.append(flatfiles_root.with_name("us_stock_sip"))
    else:
        candidates.extend([flatfiles_root / "us_stocks_sip", flatfiles_root / "us_stock_sip"])
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def available_sessions(flatfiles_root: Path, start_date: str, end_date: str) -> list[str]:
    sessions = []
    for session in date_range(start_date, end_date):
        if find_flatfile(flatfiles_root, "quotes", session) is not None and find_flatfile(flatfiles_root, "trades", session) is not None:
            sessions.append(session)
    if not sessions:
        raise SystemExit(f"No quote/trade flatfile pairs found between {start_date} and {end_date} under {flatfiles_root}")
    return sessions


def date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    out: list[str] = []
    while start <= end:
        out.append(start.isoformat())
        start += timedelta(days=1)
    return out


def parse_ticker_list(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    if not values or (len(values) == 1 and values[0] in {"ALL", "*"}):
        return (ALL_TICKERS_SENTINEL,)
    return values


def uses_all_tickers(tickers: tuple[str, ...]) -> bool:
    return len(tickers) == 1 and tickers[0] == ALL_TICKERS_SENTINEL


def header_columns(path: Path) -> set[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        first = handle.readline().strip()
    return {column.strip() for column in first.split(",") if column.strip()}


def scan_parquet_paths(paths: Iterable[Path]) -> pl.LazyFrame:
    path_list = [str(path) for path in paths]
    if len(path_list) == 1:
        return pl.scan_parquet(path_list[0])
    return pl.concat([pl.scan_parquet(path) for path in path_list], how="vertical_relaxed")


def discover_temp_groups(temp_root: Path) -> dict[tuple[str, str, str], list[Path]]:
    groups: dict[tuple[str, str, str], list[Path]] = {}
    for path in temp_root.glob("*/session=*/year_month=*/ticker_bucket=*/*.parquet"):
        parts = path.parts
        kind = parts[-5]
        year_month = parts[-3].split("=", 1)[1]
        ticker_bucket = parts[-2].split("=", 1)[1]
        groups.setdefault((kind, year_month, ticker_bucket), []).append(path)
    return groups


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
            done, pending = concurrent.futures.wait(
                pending,
                timeout=max(1.0, heartbeat_seconds),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
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
        if "kind" in item and "ticker_bucket" in item:
            return f"{item['kind']}:bucket={item['ticker_bucket']}:{item['year_month']}"
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
    return {
        "phase": phase,
        "key": key,
        "status": status,
        "rows": rows,
        "details": details,
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def failed_row(phase: str, key: str, elapsed: float) -> dict[str, Any]:
    return {
        "phase": phase,
        "key": key,
        "status": "failed",
        "rows": 0,
        "error": traceback.format_exc(),
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def format_progress(result: dict[str, Any], completed: int, total: int, elapsed: float) -> str:
    return (
        f"[{completed:,}/{total:,}] {result.get('phase')} {result.get('key')} "
        f"{result.get('status')} rows={result.get('rows')} "
        f"item_seconds={float(result.get('elapsed_seconds', 0.0)):.1f} elapsed_minutes={elapsed / 60.0:.1f}"
    )


def config_to_payload(config: CompactCanonicalConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("flatfiles_root", "canonical_root", "temp_root", "issue_root"):
        payload[key] = str(payload[key])
    return payload


def config_from_payload(payload: dict[str, Any]) -> CompactCanonicalConfig:
    values = dict(payload)
    for key in ("flatfiles_root", "canonical_root", "temp_root", "issue_root"):
        values[key] = Path(values[key])
    values["tickers"] = tuple(values["tickers"])
    return CompactCanonicalConfig(**values)


def remove_path(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def safe_path_part(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


if __name__ == "__main__":
    main()
