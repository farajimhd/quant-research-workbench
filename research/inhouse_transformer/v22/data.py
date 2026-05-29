from __future__ import annotations

import gc
import csv
import gzip
import zlib
import math
import os
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as datetime_time
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

try:
    import torch
    from torch.utils.data import IterableDataset, get_worker_info
except ModuleNotFoundError:
    torch = None

    class IterableDataset:  # type: ignore[no-redef]
        pass

    def get_worker_info() -> Any:  # type: ignore[no-redef]
        return None

from research.inhouse_transformer.v22.targets import encode_binary_magnitude_targets, log_return_bps
from research.inhouse_transformer.v22.config import DataConfig


LOG_RULE = "*" * 96
ALL_TICKERS_SENTINEL = "__ALL_TICKERS__"
NANOSECONDS_PER_SECOND = 1_000_000_000
QUOTE_SIZE_UNIT_SWITCH_DATE = "2025-11-03"
TEMP_TICKER_BUCKET_COUNT = 256

QUOTE_FEATURE_COLUMNS: tuple[str, ...] = (
    "time_offset",
    "delta_time",
    "bid_price",
    "ask_price",
    "mid_price",
    "spread_bps",
    "bid_size",
    "ask_size",
    "quote_imbalance",
    "bid_exchange",
    "ask_exchange",
    "tape",
    "condition_count",
    "condition_first",
    "indicator_count",
    "indicator_first",
    "participant_latency_ms",
    "trf_latency_ms",
)
TRADE_FEATURE_COLUMNS: tuple[str, ...] = (
    "time_offset",
    "delta_time",
    "price",
    "size",
    "exchange",
    "latest_bid",
    "latest_ask",
    "latest_mid",
    "latest_spread_bps",
    "latest_quote_imbalance",
    "price_vs_mid_bps",
    "side_proxy",
    "tape",
    "condition_count",
    "condition_first",
    "correction",
    "trade_id",
    "trf_id",
    "participant_latency_ms",
    "trf_latency_ms",
)
CHUNK_SUMMARY_COLUMNS: tuple[str, ...] = (
    "event_count",
    "quote_count",
    "trade_count",
    "overflow_quote_count",
    "overflow_trade_count",
    "overflow_total_count",
    "overflow_trade_volume",
    "overflow_signed_volume",
    "overflow_mid_min",
    "overflow_mid_max",
    "overflow_spread_min_bps",
    "overflow_spread_max_bps",
    "latest_bid",
    "latest_ask",
    "latest_mid",
    "latest_spread_bps",
    "latest_bid_size",
    "latest_ask_size",
    "latest_quote_imbalance",
    "trade_volume",
    "signed_trade_volume",
    "seconds_since_trade",
    "seconds_since_quote",
    "has_trade",
    "has_quote",
)

QUOTE_PRICE_COLUMNS = {"bid_price", "ask_price", "mid_price"}
TRADE_PRICE_COLUMNS = {"price", "latest_bid", "latest_ask", "latest_mid"}
SUMMARY_PRICE_COLUMNS = {"overflow_mid_min", "overflow_mid_max", "latest_bid", "latest_ask", "latest_mid"}
LOG_COLUMNS = {
    "bid_size",
    "ask_size",
    "size",
    "trade_id",
    "event_count",
    "quote_count",
    "trade_count",
    "overflow_quote_count",
    "overflow_trade_count",
    "overflow_total_count",
    "overflow_trade_volume",
    "trade_volume",
    "latest_bid_size",
    "latest_ask_size",
}


@dataclass(slots=True)
class SessionCoverage:
    sessions: int = 0
    sessions_with_windows: int = 0
    windows: int = 0
    batches: int = 0


def parse_ticker_list(raw: str) -> tuple[str, ...]:
    parts = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    if not parts or (len(parts) == 1 and parts[0] in {"ALL", "*"}):
        return (ALL_TICKERS_SENTINEL,)
    return parts


def uses_all_tickers(tickers: tuple[str, ...]) -> bool:
    return len(tickers) == 1 and tickers[0] == ALL_TICKERS_SENTINEL


def date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return []
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def year_month_range(start_date: str, end_date: str) -> list[str]:
    months: list[str] = []
    for session in date_range(start_date, end_date):
        year_month = session[:7]
        if not months or months[-1] != year_month:
            months.append(year_month)
    return months


def available_sessions(flatfiles_root: Path, start_date: str, end_date: str) -> list[str]:
    sessions = []
    for session in date_range(start_date, end_date):
        if find_flatfile(flatfiles_root, "quotes", session) is not None and find_flatfile(flatfiles_root, "trades", session) is not None:
            sessions.append(session)
    if not sessions:
        raise SystemExit(
            f"No quote/trade flatfile pairs found between {start_date} and {end_date} under {flatfiles_root}."
        )
    return sessions


def find_flatfile(flatfiles_root: Path, kind: str, session: str) -> Path | None:
    roots = {
        "quotes": ("quotes_v1", "quotes"),
        "trades": ("trades_v1", "trades"),
    }[kind]
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


def header_columns(path: Path) -> set[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        first_line = handle.readline()
    if not first_line:
        return set()
    return {column.strip() for column in first_line.rstrip("\r\n").split(",") if column.strip()}


def flatfile_root_candidates(flatfiles_root: Path) -> tuple[Path, ...]:
    candidates = [flatfiles_root]
    if flatfiles_root.name == "us_stock_sip":
        candidates.append(flatfiles_root.with_name("us_stocks_sip"))
    elif flatfiles_root.name == "us_stocks_sip":
        candidates.append(flatfiles_root.with_name("us_stock_sip"))
    else:
        candidates.append(flatfiles_root / "us_stocks_sip")
        candidates.append(flatfiles_root / "us_stock_sip")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def canonical_event_path(config: DataConfig, kind: str, ticker: str, year_month: str) -> Path:
    return config.canonical_root / kind / f"ticker={ticker}" / f"{year_month}.parquet"


def event_chunk_path(config: DataConfig, ticker: str, year_month: str) -> Path:
    return (
        config.cache_root
        / f"chunk_ms={config.chunk_ms}"
        / f"mq={config.max_quote_events}_mt={config.max_trade_events}_m={config.max_total_events}"
        / f"ticker={ticker}"
        / f"{year_month}.parquet"
    )


def cached_event_chunk_path(config: DataConfig, session: str) -> Path:
    return (
        config.cache_root
        / f"chunk_ms={config.chunk_ms}"
        / f"mq={config.max_quote_events}_mt={config.max_trade_events}_m={config.max_total_events}"
        / "sparse_event_chunks"
        / session[:4]
        / session[5:7]
        / f"{session}.parquet"
    )


def load_or_build_session_event_chunks(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    monthly = load_session_from_ticker_month_chunks(config, session, tickers)
    if monthly is not None:
        return monthly
    cache_path = cached_event_chunk_path(config, session)
    if cache_path.exists() and not config.rebuild_cache:
        scan = pl.scan_parquet(str(cache_path))
        if tickers and not uses_all_tickers(tickers):
            scan = scan.filter(pl.col("ticker").is_in(list(tickers)))
        return collect_lazy(scan)
    frame = build_sparse_event_chunks(config, session, tickers)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(cache_path, compression="zstd")
    return frame


def load_session_from_ticker_month_chunks(
    config: DataConfig,
    session: str,
    tickers: tuple[str, ...],
) -> pl.DataFrame | None:
    year_month = session[:7]
    base = (
        config.cache_root
        / f"chunk_ms={config.chunk_ms}"
        / f"mq={config.max_quote_events}_mt={config.max_trade_events}_m={config.max_total_events}"
    )
    if not base.exists():
        return None
    if tickers and not uses_all_tickers(tickers):
        paths = [event_chunk_path(config, ticker, year_month) for ticker in tickers]
        paths = [path for path in paths if path.exists()]
        if not paths:
            return None
        scans = [
            pl.scan_parquet(str(path)).filter(pl.col("session_date") == session)
            for path in paths
        ]
        frame = collect_lazy(pl.concat(scans, how="vertical_relaxed")).sort(["ticker", "chunk_end_ns"])
    else:
        glob_path = str(base / "ticker=*" / f"{year_month}.parquet")
        if not list(base.glob(f"ticker=*/{year_month}.parquet")):
            return None
        frame = collect_lazy(pl.scan_parquet(glob_path).filter(pl.col("session_date") == session)).sort(["ticker", "chunk_end_ns"])
    return frame


def build_sparse_event_chunks(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    quotes = read_quotes(config, session, tickers)
    trades = read_trades(config, session, tickers)
    if quotes.is_empty() and trades.is_empty():
        return pl.DataFrame()
    trades_with_quote = attach_quote_state_to_trades(trades, quotes)
    frames: list[pl.DataFrame] = []
    tickers_seen = sorted(set(quotes.get_column("ticker").to_list() if not quotes.is_empty() else []) | set(trades.get_column("ticker").to_list() if not trades.is_empty() else []))
    quote_ranges = {ticker: frame for ticker, frame in iter_ticker_frames(quotes)}
    trade_ranges = {ticker: frame for ticker, frame in iter_ticker_frames(trades_with_quote)}
    for ticker in tickers_seen:
        ticker_quotes = quote_ranges.get(ticker, pl.DataFrame())
        ticker_trades = trade_ranges.get(ticker, pl.DataFrame())
        ticker_chunks = build_ticker_sparse_chunks(config, session, ticker, ticker_quotes, ticker_trades)
        if ticker_chunks is not None and not ticker_chunks.is_empty():
            frames.append(ticker_chunks)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").sort(["ticker", "chunk_end_ns"])


def read_quotes(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    return scan_normalized_quotes(config, session, tickers).sort(["ticker", "sip_timestamp", "sequence_number"]).pipe(collect_lazy)


def scan_normalized_quotes(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.LazyFrame:
    path = find_flatfile(config.flatfiles_root, "quotes", session)
    if path is None:
        raise FileNotFoundError(f"Missing quotes flatfile for {session} under {config.flatfiles_root}.")
    columns = [
        "ticker",
        "sip_timestamp",
        "sequence_number",
        "participant_timestamp",
        "trf_timestamp",
        "conditions",
        "indicators",
        "tape",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "bid_exchange",
        "ask_exchange",
    ]
    names = header_columns(path)
    scan = pl.scan_csv(str(path), infer_schema_length=0, ignore_errors=True)
    missing = sorted({"ticker", "sip_timestamp", "sequence_number", "bid_price", "ask_price", "bid_size", "ask_size"} - names)
    if missing:
        raise SystemExit(f"Quote flatfile {path} is missing required columns: {missing}")
    scan = scan.select([column for column in columns if column in names])
    lot_multiplier = config.quote_size_lot_multiplier_before_2025_11_03 if session < QUOTE_SIZE_UNIT_SWITCH_DATE else 1
    normalized = (
        scan.filter(session_timestamp_filter_expr(config, session))
        .with_columns(
            pl.col("ticker").cast(pl.String).str.to_uppercase(),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            optional_int_expr("participant_timestamp", names),
            optional_int_expr("trf_timestamp", names),
            optional_int_expr("tape", names),
            pl.col("bid_price").cast(pl.Float32, strict=False),
            pl.col("ask_price").cast(pl.Float32, strict=False),
            (pl.col("bid_size").cast(pl.Float32, strict=False).fill_null(0.0) * float(lot_multiplier)).cast(pl.Float32).alias("bid_size"),
            (pl.col("ask_size").cast(pl.Float32, strict=False).fill_null(0.0) * float(lot_multiplier)).cast(pl.Float32).alias("ask_size"),
            pl.col("bid_exchange").cast(pl.Int32, strict=False).fill_null(0) if "bid_exchange" in names else pl.lit(0).alias("bid_exchange"),
            pl.col("ask_exchange").cast(pl.Int32, strict=False).fill_null(0) if "ask_exchange" in names else pl.lit(0).alias("ask_exchange"),
        )
        .with_columns(
            metadata_list_count_expr("conditions", names).alias("condition_count"),
            metadata_list_first_expr("conditions", names).alias("condition_first"),
            metadata_list_count_expr("indicators", names).alias("indicator_count"),
            metadata_list_first_expr("indicators", names).alias("indicator_first"),
            timestamp_latency_ms_expr("participant_timestamp").alias("participant_latency_ms"),
            timestamp_latency_ms_expr("trf_timestamp").alias("trf_latency_ms"),
        )
        .with_columns(
            pl.lit(session).alias("session_date"),
            pl.lit(session[:7]).alias("year_month"),
            ticker_bucket_expr().alias("ticker_bucket"),
        )
        .filter((pl.col("bid_price") > 0.0) & (pl.col("ask_price") > 0.0) & (pl.col("ask_price") >= pl.col("bid_price")))
        .with_columns(quote_state_exprs())
    )
    if tickers and not uses_all_tickers(tickers):
        normalized = normalized.filter(pl.col("ticker").is_in(list(tickers)))
    return normalized.select(
        [
            "ticker",
            "session_date",
            "year_month",
            "ticker_bucket",
            "sip_timestamp",
            "sequence_number",
            "participant_timestamp",
            "trf_timestamp",
            "bid_price",
            "ask_price",
            "bid_size",
            "ask_size",
            "bid_exchange",
            "ask_exchange",
            "tape",
            "condition_count",
            "condition_first",
            "indicator_count",
            "indicator_first",
            "participant_latency_ms",
            "trf_latency_ms",
            "mid_price",
            "spread_bps",
            "quote_imbalance",
        ]
    )


def read_trades(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    return scan_normalized_trades(config, session, tickers).sort(["ticker", "sip_timestamp", "sequence_number"]).pipe(collect_lazy)


def scan_normalized_trades(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.LazyFrame:
    path = find_flatfile(config.flatfiles_root, "trades", session)
    if path is None:
        raise FileNotFoundError(f"Missing trades flatfile for {session} under {config.flatfiles_root}.")
    columns = [
        "ticker",
        "sip_timestamp",
        "sequence_number",
        "participant_timestamp",
        "trf_timestamp",
        "conditions",
        "correction",
        "id",
        "tape",
        "trf_id",
        "price",
        "size",
        "exchange",
    ]
    names = header_columns(path)
    scan = pl.scan_csv(str(path), infer_schema_length=0, ignore_errors=True)
    missing = sorted({"ticker", "sip_timestamp", "sequence_number", "price", "size"} - names)
    if missing:
        raise SystemExit(f"Trade flatfile {path} is missing required columns: {missing}")
    scan = scan.select([column for column in columns if column in names])
    normalized = (
        scan.filter(session_timestamp_filter_expr(config, session))
        .with_columns(
            pl.col("ticker").cast(pl.String).str.to_uppercase(),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            optional_int_expr("participant_timestamp", names),
            optional_int_expr("trf_timestamp", names),
            optional_int_expr("tape", names),
            optional_int_expr("correction", names),
            optional_int_expr("id", names).alias("trade_id"),
            optional_int_expr("trf_id", names),
            pl.col("price").cast(pl.Float32, strict=False),
            pl.col("size").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("exchange").cast(pl.Int32, strict=False).fill_null(0) if "exchange" in names else pl.lit(0).alias("exchange"),
        )
        .with_columns(
            metadata_list_count_expr("conditions", names).alias("condition_count"),
            metadata_list_first_expr("conditions", names).alias("condition_first"),
            timestamp_latency_ms_expr("participant_timestamp").alias("participant_latency_ms"),
            timestamp_latency_ms_expr("trf_timestamp").alias("trf_latency_ms"),
        )
        .with_columns(
            pl.lit(session).alias("session_date"),
            pl.lit(session[:7]).alias("year_month"),
            ticker_bucket_expr().alias("ticker_bucket"),
        )
        .filter((pl.col("price") > 0.0) & (pl.col("size") > 0.0))
    )
    if tickers and not uses_all_tickers(tickers):
        normalized = normalized.filter(pl.col("ticker").is_in(list(tickers)))
    return normalized.select(
        [
            "ticker",
            "session_date",
            "year_month",
            "ticker_bucket",
            "sip_timestamp",
            "sequence_number",
            "participant_timestamp",
            "trf_timestamp",
            "price",
            "size",
            "exchange",
            "tape",
            "condition_count",
            "condition_first",
            "correction",
            "trade_id",
            "trf_id",
            "participant_latency_ms",
            "trf_latency_ms",
        ]
    )


def temp_canonical_parts_root(config: DataConfig) -> Path:
    return config.cache_root / "_tmp_canonical_parts"


def normalize_session_to_temp_parts(
    config: DataConfig,
    session: str,
    tickers: tuple[str, ...],
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    results: dict[str, Any] = {"session": session, "kinds": {}}
    for kind in ("quotes", "trades"):
        results["kinds"][kind] = normalize_session_kind_to_temp_parts(
            config,
            session,
            kind,
            tickers,
            rebuild=rebuild,
        )
    return results


def normalize_session_kind_to_temp_parts(
    config: DataConfig,
    session: str,
    kind: str,
    tickers: tuple[str, ...],
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    scanner = {
        "quotes": scan_normalized_quotes,
        "trades": scan_normalized_trades,
    }[kind]
    root = temp_canonical_parts_root(config)
    output_root = root / kind / f"session={session}"
    if output_root.exists() and rebuild:
        shutil.rmtree(output_root)
    if output_root.exists() and list(output_root.rglob("*.parquet")):
        rows = count_parquet_rows(output_root.rglob("*.parquet"))
        return {"kind": kind, "session": session, "status": "skipped", "rows": rows, "path": str(output_root)}
    output_root.mkdir(parents=True, exist_ok=True)
    lazy = scanner(config, session, tickers)
    if hasattr(pl, "PartitionBy"):
        writer = "polars_partitioned_sink"
        lazy.sink_parquet(
            pl.PartitionBy(
                output_root,
                key=["year_month", "ticker_bucket"],
                include_key=True,
                max_rows_per_file=1_000_000,
            ),
            compression="zstd",
            mkdir=True,
            maintain_order=False,
        )
    else:
        writer = "pyarrow_streaming_fallback"
        stream_normalized_csv_to_temp_parts(
            config,
            session,
            kind,
            tickers,
            output_root,
        )
    rows = count_parquet_rows(output_root.rglob("*.parquet"))
    return {"kind": kind, "session": session, "status": "ok", "rows": rows, "path": str(output_root), "writer": writer}


def stream_normalized_csv_to_temp_parts(
    config: DataConfig,
    session: str,
    kind: str,
    tickers: tuple[str, ...],
    output_root: Path,
    *,
    flush_rows_per_ticker: int = 100_000,
    flush_total_rows: int = 1_000_000,
    progress_rows: int = 2_000_000,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = find_flatfile(config.flatfiles_root, kind, session)
    if path is None:
        raise FileNotFoundError(f"Missing {kind} flatfile for {session} under {config.flatfiles_root}.")
    selected_tickers = set(tickers) if tickers and not uses_all_tickers(tickers) else None
    buffers: dict[str, list[dict[str, Any]]] = {}
    part_index: dict[str, int] = {}
    rows_seen = 0
    rows_written = 0
    rows_buffered = 0
    started = time.time()
    last_progress = started
    required = {"ticker", "sip_timestamp"}
    if kind == "quotes":
        required |= {"sequence_number", "bid_price", "ask_price", "bid_size", "ask_size"}
    else:
        required |= {"sequence_number", "price", "size"}
    with open_csv_text(path) as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise SystemExit(f"{kind} flatfile {path} is missing required columns: {missing}")
        for row in reader:
            rows_seen += 1
            normalized = normalize_raw_row(config, session, kind, row, selected_tickers)
            if normalized is not None:
                ticker = str(normalized["ticker"])
                buffers.setdefault(ticker, []).append(normalized)
                rows_buffered += 1
                if len(buffers[ticker]) >= flush_rows_per_ticker:
                    rows_written += flush_ticker_buffer(output_root, kind, ticker, buffers, part_index, pa, pq)
                    rows_buffered = sum(len(items) for items in buffers.values())
                if rows_buffered >= flush_total_rows:
                    for flush_ticker in list(buffers):
                        rows_written += flush_ticker_buffer(output_root, kind, flush_ticker, buffers, part_index, pa, pq)
                    rows_buffered = 0
            now = time.time()
            if rows_seen % progress_rows == 0 or now - last_progress >= 30.0:
                print(
                    f"STREAM {kind}:{session} seen={rows_seen:,} written={rows_written:,} "
                    f"buffered={rows_buffered:,} elapsed_minutes={(now - started) / 60.0:.1f}",
                    flush=True,
                )
                last_progress = now
    for flush_ticker in list(buffers):
        rows_written += flush_ticker_buffer(output_root, kind, flush_ticker, buffers, part_index, pa, pq)
    print(
        f"STREAM {kind}:{session} complete seen={rows_seen:,} written={rows_written:,} "
        f"elapsed_minutes={(time.time() - started) / 60.0:.1f}",
        flush=True,
    )


def open_csv_text(path: Path) -> Any:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("rt", encoding="utf-8", newline="")


def normalize_raw_row(
    config: DataConfig,
    session: str,
    kind: str,
    row: dict[str, str],
    selected_tickers: set[str] | None,
) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker or (selected_tickers is not None and ticker not in selected_tickers):
        return None
    sip_timestamp = parse_int(row.get("sip_timestamp"))
    if sip_timestamp is None:
        return None
    if not timestamp_in_session(config, session, sip_timestamp):
        return None
    sequence_number = parse_int(row.get("sequence_number")) or 0
    if kind == "quotes":
        bid_price = parse_float(row.get("bid_price"))
        ask_price = parse_float(row.get("ask_price"))
        if bid_price is None or ask_price is None or bid_price <= 0.0 or ask_price <= 0.0 or ask_price < bid_price:
            return None
        lot_multiplier = config.quote_size_lot_multiplier_before_2025_11_03 if session < QUOTE_SIZE_UNIT_SWITCH_DATE else 1
        bid_size = max(0.0, parse_float(row.get("bid_size")) or 0.0) * float(lot_multiplier)
        ask_size = max(0.0, parse_float(row.get("ask_size")) or 0.0) * float(lot_multiplier)
        mid_price = (bid_price + ask_price) * 0.5
        size_sum = max(1.0, bid_size + ask_size)
        return {
            "ticker": ticker,
            "session_date": session,
            "year_month": session[:7],
            "ticker_bucket": temp_ticker_bucket(ticker),
            "sip_timestamp": sip_timestamp,
            "sequence_number": sequence_number,
            "participant_timestamp": parse_int(row.get("participant_timestamp")) or 0,
            "trf_timestamp": parse_int(row.get("trf_timestamp")) or 0,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "bid_exchange": parse_int(row.get("bid_exchange")) or 0,
            "ask_exchange": parse_int(row.get("ask_exchange")) or 0,
            "tape": parse_int(row.get("tape")) or 0,
            "condition_count": metadata_list_count(row.get("conditions")),
            "condition_first": metadata_list_first(row.get("conditions")),
            "indicator_count": metadata_list_count(row.get("indicators")),
            "indicator_first": metadata_list_first(row.get("indicators")),
            "participant_latency_ms": timestamp_latency_ms(sip_timestamp, row.get("participant_timestamp")),
            "trf_latency_ms": timestamp_latency_ms(sip_timestamp, row.get("trf_timestamp")),
            "mid_price": mid_price,
            "spread_bps": 10000.0 * (ask_price - bid_price) / max(mid_price, 1e-6),
            "quote_imbalance": (bid_size - ask_size) / size_sum,
        }
    price = parse_float(row.get("price"))
    size = parse_float(row.get("size"))
    if price is None or size is None or price <= 0.0 or size <= 0.0:
        return None
    return {
        "ticker": ticker,
        "session_date": session,
        "year_month": session[:7],
        "ticker_bucket": temp_ticker_bucket(ticker),
        "sip_timestamp": sip_timestamp,
        "sequence_number": sequence_number,
        "participant_timestamp": parse_int(row.get("participant_timestamp")) or 0,
        "trf_timestamp": parse_int(row.get("trf_timestamp")) or 0,
        "price": price,
        "size": size,
        "exchange": parse_int(row.get("exchange")) or 0,
        "tape": parse_int(row.get("tape")) or 0,
        "condition_count": metadata_list_count(row.get("conditions")),
        "condition_first": metadata_list_first(row.get("conditions")),
        "correction": parse_int(row.get("correction")) or 0,
        "trade_id": parse_int(row.get("id")) or 0,
        "trf_id": parse_int(row.get("trf_id")) or 0,
        "participant_latency_ms": timestamp_latency_ms(sip_timestamp, row.get("participant_timestamp")),
        "trf_latency_ms": timestamp_latency_ms(sip_timestamp, row.get("trf_timestamp")),
    }


def parse_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def metadata_list_count(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    return float(len([part for part in text.split(",") if part.strip()]))


def metadata_list_first(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text.split(",", 1)[0])
    except ValueError:
        return 0.0


def timestamp_latency_ms(sip_timestamp: int, source_timestamp: Any) -> float:
    source = parse_int(source_timestamp)
    if source is None or source <= 0:
        return 0.0
    return float(min(60_000.0, max(0.0, (sip_timestamp - source) / 1_000_000.0)))


def session_timestamp_filter_expr(config: DataConfig, session: str) -> pl.Expr:
    timestamp = pl.col("sip_timestamp").cast(pl.Int64, strict=False)
    if config.session_filter_mode == "utc_hour":
        day_ns = timestamp % (24 * 3600 * NANOSECONDS_PER_SECOND)
        start_ns = config.session_start_hour_utc * 3600 * NANOSECONDS_PER_SECOND
        end_ns = config.session_end_hour_utc * 3600 * NANOSECONDS_PER_SECOND
        if start_ns <= end_ns:
            return (day_ns >= start_ns) & (day_ns < end_ns)
        return (day_ns >= start_ns) | (day_ns < end_ns)
    start_ns, end_ns = session_market_window_utc_ns(config, session)
    return (timestamp >= start_ns) & (timestamp < end_ns)


def timestamp_in_session(config: DataConfig, session: str, sip_timestamp: int) -> bool:
    if config.session_filter_mode == "utc_hour":
        day_ns = sip_timestamp % (24 * 3600 * NANOSECONDS_PER_SECOND)
        start_ns = config.session_start_hour_utc * 3600 * NANOSECONDS_PER_SECOND
        end_ns = config.session_end_hour_utc * 3600 * NANOSECONDS_PER_SECOND
        if start_ns <= end_ns:
            return start_ns <= day_ns < end_ns
        return day_ns >= start_ns or day_ns < end_ns
    start_ns, end_ns = session_market_window_utc_ns(config, session)
    return start_ns <= sip_timestamp < end_ns


def session_market_window_utc_ns(config: DataConfig, session: str) -> tuple[int, int]:
    timezone = ZoneInfo(config.session_timezone)
    session_day = date.fromisoformat(session)
    start_local = datetime.combine(
        session_day,
        parse_market_time(config.session_start_time_market),
        tzinfo=timezone,
    )
    end_local = datetime.combine(
        session_day,
        parse_market_time(config.session_end_time_market),
        tzinfo=timezone,
    )
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return int(start_local.timestamp() * NANOSECONDS_PER_SECOND), int(end_local.timestamp() * NANOSECONDS_PER_SECOND)


def parse_market_time(value: str) -> datetime_time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return datetime_time(hour=int(hour_text), minute=int(minute_text))
    except Exception as exc:
        raise ValueError(f"Expected HH:MM market time, got {value!r}.") from exc


def flush_ticker_buffer(
    output_root: Path,
    kind: str,
    ticker: str,
    buffers: dict[str, list[dict[str, Any]]],
    part_index: dict[str, int],
    pa: Any,
    pq: Any,
) -> int:
    rows = buffers.get(ticker) or []
    if not rows:
        return 0
    index = part_index.get(ticker, 0)
    part_index[ticker] = index + 1
    year_month = str(rows[0]["year_month"])
    ticker_bucket = int(rows[0]["ticker_bucket"])
    output_dir = output_root / f"year_month={year_month}" / f"ticker_bucket={ticker_bucket}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_temp_filename_part(ticker)}-{index:08d}.parquet"
    columns = quote_canonical_columns() if kind == "quotes" else trade_canonical_columns()
    table = pa.Table.from_pydict({column: [row[column] for row in rows] for column in columns})
    pq.write_table(table, output_path, compression="zstd")
    buffers[ticker] = []
    return len(rows)


def quote_canonical_columns() -> list[str]:
    return [
        "ticker",
        "session_date",
        "year_month",
        "sip_timestamp",
        "sequence_number",
        "participant_timestamp",
        "trf_timestamp",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "bid_exchange",
        "ask_exchange",
        "tape",
        "condition_count",
        "condition_first",
        "indicator_count",
        "indicator_first",
        "participant_latency_ms",
        "trf_latency_ms",
        "mid_price",
        "spread_bps",
        "quote_imbalance",
    ]


def trade_canonical_columns() -> list[str]:
    return [
        "ticker",
        "session_date",
        "year_month",
        "sip_timestamp",
        "sequence_number",
        "participant_timestamp",
        "trf_timestamp",
        "price",
        "size",
        "exchange",
        "tape",
        "condition_count",
        "condition_first",
        "correction",
        "trade_id",
        "trf_id",
        "participant_latency_ms",
        "trf_latency_ms",
    ]


def discover_temp_canonical_groups(config: DataConfig) -> dict[tuple[str, str, str], list[Path]]:
    root = temp_canonical_parts_root(config)
    groups: dict[tuple[str, str, str], list[Path]] = {}
    for path in root.glob("*/session=*/year_month=*/ticker_bucket=*/*.parquet"):
        parts = path.parts
        kind = parts[-5]
        year_month = parts[-3].split("=", 1)[1]
        ticker_bucket = parts[-2].split("=", 1)[1]
        groups.setdefault((kind, year_month, ticker_bucket), []).append(path)
    return groups


def merge_temp_group_to_canonical(
    config: DataConfig,
    *,
    kind: str,
    year_month: str,
    ticker_bucket: str,
    paths: list[Path],
    rebuild: bool = False,
) -> dict[str, Any]:
    rows = count_parquet_rows(paths)
    base_path = config.canonical_root / kind
    base_path.mkdir(parents=True, exist_ok=True)
    frame = scan_parquet_paths(paths).sort(["ticker", "sip_timestamp", "sequence_number"])
    frame = frame.select([column for column in (quote_canonical_columns() if kind == "quotes" else trade_canonical_columns())])
    frame.sink_parquet(
        pl.PartitionBy(
            base_path,
            key="ticker",
            include_key=True,
            approximate_bytes_per_file=None,
            file_path_provider=canonical_file_provider(config, kind, year_month),
        ),
        compression="zstd",
        mkdir=True,
        maintain_order=True,
    )
    return {
        "kind": kind,
        "year_month": year_month,
        "ticker_bucket": ticker_bucket,
        "status": "ok",
        "rows": rows,
        "output_path": str(base_path),
    }


def canonical_file_provider(config: DataConfig, kind: str, year_month: str) -> Any:
    def provider(args: Any) -> Path:
        ticker = str(args.partition_keys.get_column("ticker")[0])
        return canonical_event_path(config, kind, ticker, year_month)

    return provider


def discover_canonical_groups(
    config: DataConfig,
    *,
    start_date: str,
    end_date: str,
    tickers: tuple[str, ...],
) -> list[tuple[str, str]]:
    months = set(year_month_range(start_date, end_date))
    groups: set[tuple[str, str]] = set()
    if tickers and not uses_all_tickers(tickers):
        for ticker in tickers:
            for year_month in months:
                if canonical_event_path(config, "quotes", ticker, year_month).exists() or canonical_event_path(config, "trades", ticker, year_month).exists():
                    groups.add((ticker, year_month))
        return sorted(groups)
    for kind in ("quotes", "trades"):
        base = config.canonical_root / kind
        if not base.exists():
            continue
        for path in base.glob("ticker=*/*.parquet"):
            year_month = path.stem
            if year_month not in months:
                continue
            ticker = path.parent.name.split("=", 1)[1]
            groups.add((ticker, year_month))
    return sorted(groups)


def read_canonical_events(config: DataConfig, kind: str, ticker: str, year_month: str) -> pl.DataFrame:
    path = canonical_event_path(config, kind, ticker, year_month)
    if not path.exists():
        return pl.DataFrame()
    return collect_lazy(pl.scan_parquet(str(path))).sort(["sip_timestamp", "sequence_number"])


def build_event_chunks_from_canonical(
    config: DataConfig,
    *,
    ticker: str,
    year_month: str,
    rebuild: bool = False,
) -> dict[str, Any]:
    key = f"{ticker}:{year_month}"
    worker_started = time.perf_counter()
    timings: list[dict[str, Any]] = []
    output_path = event_chunk_path(config, ticker, year_month)
    if output_path.exists() and not rebuild:
        return {
            "ticker": ticker,
            "year_month": year_month,
            "status": "skipped",
            "rows": count_parquet_rows([output_path]),
            "output_path": str(output_path),
        }
    log_chunk_step(key, "read_quotes_start", worker_started)
    quotes = read_canonical_events(config, "quotes", ticker, year_month)
    log_chunk_step(key, "read_quotes_done", worker_started, timings, rows=quotes.height)
    log_chunk_step(key, "read_trades_start", worker_started)
    trades = read_canonical_events(config, "trades", ticker, year_month)
    log_chunk_step(key, "read_trades_done", worker_started, timings, rows=trades.height)
    if quotes.is_empty() and trades.is_empty():
        return {"ticker": ticker, "year_month": year_month, "status": "empty", "rows": 0, "output_path": str(output_path)}
    if quotes.is_empty():
        if rebuild and output_path.exists():
            output_path.unlink()
        return {
            "ticker": ticker,
            "year_month": year_month,
            "status": "empty_no_quotes",
            "rows": 0,
            "output_path": str(output_path),
        }
    log_chunk_step(key, "attach_quote_state_start", worker_started)
    trades_with_quote = attach_quote_state_to_trades(trades, quotes)
    log_chunk_step(key, "attach_quote_state_done", worker_started, timings, rows=trades_with_quote.height)
    chunks = build_ticker_sparse_chunks_vectorized(
        config,
        ticker,
        quotes,
        trades_with_quote,
        timings=timings,
        log_key=key,
        worker_started=worker_started,
    )
    if chunks is None or chunks.is_empty():
        return {"ticker": ticker, "year_month": year_month, "status": "empty", "rows": 0, "output_path": str(output_path)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    log_chunk_step(key, "write_start", worker_started, rows=chunks.height, output_path=str(output_path))
    chunks.sort("chunk_end_ns").write_parquet(temp_path, compression="zstd")
    os.replace(temp_path, output_path)
    log_chunk_step(key, "write_done", worker_started, timings, rows=chunks.height, output_path=str(output_path))
    return {
        "ticker": ticker,
        "year_month": year_month,
        "status": "ok",
        "rows": chunks.height,
        "output_path": str(output_path),
        "timings": timings,
    }


def build_ticker_sparse_chunks_vectorized(
    config: DataConfig,
    ticker: str,
    quotes: pl.DataFrame,
    trades: pl.DataFrame,
    *,
    timings: list[dict[str, Any]] | None = None,
    log_key: str = "",
    worker_started: float | None = None,
) -> pl.DataFrame | None:
    chunk_ns = config.chunk_ms * 1_000_000
    step_started = worker_started or time.perf_counter()
    key = log_key or ticker
    log_chunk_step(key, "prepare_quote_events_start", step_started)
    quotes_prepared = prepare_quote_events_for_chunks(quotes, chunk_ns)
    log_chunk_step(key, "prepare_quote_events_done", step_started, timings, rows=quotes_prepared.height)
    log_chunk_step(key, "prepare_trade_events_start", step_started)
    trades_prepared = prepare_trade_events_for_chunks(trades, chunk_ns)
    log_chunk_step(key, "prepare_trade_events_done", step_started, timings, rows=trades_prepared.height)
    if quotes_prepared.is_empty() and trades_prepared.is_empty():
        return None

    log_chunk_step(key, "chunk_counts_start", step_started)
    counts = chunk_counts(config, quotes_prepared, trades_prepared)
    log_chunk_step(key, "chunk_counts_done", step_started, timings, rows=counts.height)
    log_chunk_step(key, "quote_aggregation_start", step_started)
    quote_chunks = selected_quote_chunk_values(config, quotes_prepared, counts, chunk_ns)
    log_chunk_step(key, "quote_aggregation_done", step_started, timings, rows=quote_chunks.height)
    log_chunk_step(key, "trade_aggregation_start", step_started)
    trade_chunks = selected_trade_chunk_values(config, trades_prepared, counts, chunk_ns)
    log_chunk_step(key, "trade_aggregation_done", step_started, timings, rows=trade_chunks.height)
    log_chunk_step(key, "event_order_start", step_started)
    order_chunks = selected_event_order_chunks(config, quotes_prepared, trades_prepared, counts)
    log_chunk_step(key, "event_order_done", step_started, timings, rows=order_chunks.height)
    log_chunk_step(key, "summary_start", step_started)
    summary = chunk_summary_vectorized(config, quotes_prepared, trades_prepared, counts)
    log_chunk_step(key, "summary_done", step_started, timings, rows=summary.height)

    log_chunk_step(key, "join_outputs_start", step_started)
    chunks = summary.join(quote_chunks, on=["session_date", "chunk_start_ns"], how="left")
    chunks = chunks.join(trade_chunks, on=["session_date", "chunk_start_ns"], how="left")
    chunks = chunks.join(order_chunks, on=["session_date", "chunk_start_ns"], how="left")
    chunks = chunks.with_columns(
        pl.lit(ticker).alias("ticker"),
        (pl.col("chunk_start_ns") + chunk_ns - 1).alias("chunk_end_ns"),
    )
    target_columns = target_cache_columns(config)
    if target_columns:
        target_grid_rows = estimate_target_cache_grid_rows(chunks, chunk_ns)
        log_chunk_step(key, "target_cache_start", step_started, columns=len(target_columns), dense_grid_rows=target_grid_rows)
        if target_grid_rows > config.max_target_cache_grid_rows_per_ticker_month:
            raise ValueError(
                f"Target cache dense grid too large for {key}: {target_grid_rows:,} rows. "
                f"Check timestamp range/session_date quality in canonical data."
            )
        chunks = add_target_cache_columns(chunks, config)
        log_chunk_step(key, "target_cache_done", step_started, timings, columns=len(target_columns), dense_grid_rows=target_grid_rows, rows=chunks.height)
    output = chunks.select(
        [
            "ticker",
            "session_date",
            "chunk_start_ns",
            "chunk_end_ns",
            "quote_values",
            "trade_values",
            "event_kinds",
            "event_indices",
            *CHUNK_SUMMARY_COLUMNS,
            *target_columns,
        ]
    ).sort(["session_date", "chunk_start_ns"])
    log_chunk_step(key, "join_outputs_done", step_started, timings, rows=output.height)
    return output


def log_chunk_step(
    key: str,
    step: str,
    worker_started: float,
    timings: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> None:
    elapsed = time.perf_counter() - worker_started
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "key": key,
        "step": step,
        "elapsed_seconds": round(elapsed, 3),
        **fields,
    }
    if timings is not None and step.endswith("_done"):
        timings.append(row)
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    suffix = f" {details}" if details else ""
    print(f"{row['ts']} CHUNK_STEP {key} {step} elapsed_seconds={elapsed:.3f}{suffix}", flush=True)


def target_cache_columns(config: DataConfig) -> list[str]:
    columns: list[str] = []
    for horizon in config.target_cache_horizon_chunks:
        columns.extend([f"target_bid_h{horizon}", f"target_ask_h{horizon}", f"target_mid_h{horizon}"])
    return columns


def estimate_target_cache_grid_rows(chunks: pl.DataFrame, chunk_ns: int) -> int:
    if chunks.is_empty():
        return 0
    bounds = chunks.group_by("session_date").agg(
        pl.col("chunk_start_ns").min().alias("min_chunk_start_ns"),
        pl.col("chunk_start_ns").max().alias("max_chunk_start_ns"),
    )
    total = bounds.select(
        (((pl.col("max_chunk_start_ns") - pl.col("min_chunk_start_ns")) // chunk_ns) + 1).sum().alias("grid_rows")
    ).item()
    return int(total or 0)


def add_target_cache_columns(chunks: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    chunk_ns = config.chunk_ms * 1_000_000
    frame = chunks.sort(["session_date", "chunk_start_ns"])
    bounds = frame.group_by("session_date").agg(
        pl.col("chunk_start_ns").min().alias("min_chunk_start_ns"),
        pl.col("chunk_start_ns").max().alias("max_chunk_start_ns"),
    )
    quote_grid = (
        bounds.with_columns(
            pl.int_ranges(
                pl.col("min_chunk_start_ns"),
                pl.col("max_chunk_start_ns") + chunk_ns,
                chunk_ns,
            ).alias("chunk_start_ns")
        )
        .explode("chunk_start_ns")
        .select(["session_date", "chunk_start_ns"])
        .join(frame.select(["session_date", "chunk_start_ns", "latest_bid", "latest_ask"]), on=["session_date", "chunk_start_ns"], how="left")
        .sort(["session_date", "chunk_start_ns"])
        .with_columns(
            pl.col("latest_bid").forward_fill().over("session_date"),
            pl.col("latest_ask").forward_fill().over("session_date"),
        )
    )
    expressions: list[pl.Expr] = []
    for horizon in config.target_cache_horizon_chunks:
        bid_name = f"target_bid_h{horizon}"
        ask_name = f"target_ask_h{horizon}"
        expressions.extend(
            [
                pl.col("latest_bid").shift(-int(horizon)).over("session_date").alias(bid_name),
                pl.col("latest_ask").shift(-int(horizon)).over("session_date").alias(ask_name),
            ]
        )
    bid_ask_columns = [
        name
        for horizon in config.target_cache_horizon_chunks
        for name in (f"target_bid_h{horizon}", f"target_ask_h{horizon}")
    ]
    target_grid = quote_grid.with_columns(expressions).select(["session_date", "chunk_start_ns", *bid_ask_columns])
    frame = frame.join(target_grid, on=["session_date", "chunk_start_ns"], how="left")
    return frame.with_columns(
        [
            ((pl.col(f"target_bid_h{horizon}") + pl.col(f"target_ask_h{horizon}")) * 0.5).alias(f"target_mid_h{horizon}")
            for horizon in config.target_cache_horizon_chunks
        ]
    )


def prepare_quote_events_for_chunks(quotes: pl.DataFrame, chunk_ns: int) -> pl.DataFrame:
    if quotes.is_empty():
        return quotes
    quotes = ensure_columns(
        quotes,
        {
            "tape": 0.0,
            "condition_count": 0.0,
            "condition_first": 0.0,
            "indicator_count": 0.0,
            "indicator_first": 0.0,
            "participant_latency_ms": 0.0,
            "trf_latency_ms": 0.0,
        },
    )
    return (
        quotes.sort(["session_date", "sip_timestamp", "sequence_number"])
        .with_columns(
            ((pl.col("sip_timestamp") // chunk_ns) * chunk_ns).alias("chunk_start_ns"),
        )
        .with_columns(
            pl.int_range(pl.len()).over(["session_date", "chunk_start_ns"]).alias("quote_pos"),
            pl.len().over(["session_date", "chunk_start_ns"]).alias("quote_count"),
        )
    )


def prepare_trade_events_for_chunks(trades: pl.DataFrame, chunk_ns: int) -> pl.DataFrame:
    if trades.is_empty():
        return trades
    trades = ensure_columns(
        trades,
        {
            "tape": 0.0,
            "condition_count": 0.0,
            "condition_first": 0.0,
            "correction": 0.0,
            "trade_id": 0.0,
            "trf_id": 0.0,
            "participant_latency_ms": 0.0,
            "trf_latency_ms": 0.0,
        },
    )
    return (
        trades.sort(["session_date", "sip_timestamp", "sequence_number"])
        .with_columns(
            ((pl.col("sip_timestamp") // chunk_ns) * chunk_ns).alias("chunk_start_ns"),
        )
        .with_columns(
            pl.int_range(pl.len()).over(["session_date", "chunk_start_ns"]).alias("trade_pos"),
            pl.len().over(["session_date", "chunk_start_ns"]).alias("trade_count"),
        )
    )


def ensure_columns(frame: pl.DataFrame, defaults: dict[str, float]) -> pl.DataFrame:
    missing = [pl.lit(value).alias(name) for name, value in defaults.items() if name not in frame.columns]
    if not missing:
        return frame
    return frame.with_columns(missing)


def chunk_counts(config: DataConfig, quotes: pl.DataFrame, trades: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    if not quotes.is_empty():
        frames.append(
            quotes.group_by(["session_date", "chunk_start_ns"]).agg(
                pl.len().cast(pl.Float64).alias("quote_count"),
                pl.col("bid_price").last().alias("latest_bid"),
                pl.col("ask_price").last().alias("latest_ask"),
                pl.col("mid_price").last().alias("latest_mid"),
                pl.col("spread_bps").last().alias("latest_spread_bps"),
                pl.col("bid_size").last().alias("latest_bid_size"),
                pl.col("ask_size").last().alias("latest_ask_size"),
                pl.col("quote_imbalance").last().alias("latest_quote_imbalance"),
            )
        )
    if not trades.is_empty():
        trade_counts = trades.group_by(["session_date", "chunk_start_ns"]).agg(
            pl.len().cast(pl.Float64).alias("trade_count"),
            pl.col("size").sum().alias("trade_volume"),
            (pl.col("size") * pl.col("side_proxy")).sum().alias("signed_trade_volume"),
        )
        frames.append(trade_counts)
    if not frames:
        return pl.DataFrame()
    counts = frames[0]
    for frame in frames[1:]:
        counts = counts.join(frame, on=["session_date", "chunk_start_ns"], how="full", coalesce=True)
    for column in (
        "quote_count",
        "trade_count",
        "trade_volume",
        "signed_trade_volume",
        "latest_bid",
        "latest_ask",
        "latest_mid",
        "latest_spread_bps",
        "latest_bid_size",
        "latest_ask_size",
        "latest_quote_imbalance",
    ):
        if column not in counts.columns:
            counts = counts.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
    return counts.with_columns(
        pl.col("quote_count").fill_null(0.0),
        pl.col("trade_count").fill_null(0.0),
        pl.col("trade_volume").fill_null(0.0),
        pl.col("signed_trade_volume").fill_null(0.0),
    ).with_columns(
        pl.min_horizontal(
            pl.col("trade_count"),
            pl.lit(float(config.max_trade_events)),
            pl.lit(float(config.max_total_events)),
        ).alias("selected_trade_count"),
    ).with_columns(
        pl.min_horizontal(
            pl.col("quote_count"),
            pl.lit(float(config.max_quote_events)),
            pl.max_horizontal(pl.lit(0.0), pl.lit(float(config.max_total_events)) - pl.col("selected_trade_count")),
        ).alias("selected_quote_count"),
    )


def selected_quote_chunk_values(config: DataConfig, quotes: pl.DataFrame, counts: pl.DataFrame, chunk_ns: int) -> pl.DataFrame:
    if quotes.is_empty():
        return empty_chunk_value_frame("quote_values")
    selected = (
        quotes.join(counts.select(["session_date", "chunk_start_ns", "selected_quote_count"]), on=["session_date", "chunk_start_ns"], how="left")
        .filter(pl.col("quote_pos") >= (pl.col("quote_count") - pl.col("selected_quote_count")))
        .sort(["session_date", "chunk_start_ns", "sip_timestamp", "sequence_number"])
        .with_columns(
            pl.col("sip_timestamp").shift(1).over(["session_date", "chunk_start_ns"]).alias("_previous_ts"),
        )
        .with_columns(
            pl.when(pl.col("_previous_ts").is_null())
            .then(pl.col("chunk_start_ns"))
            .otherwise(pl.col("_previous_ts"))
            .alias("_previous_ts")
        )
        .with_columns(
            pl.concat_list(
                [
                    ((pl.col("sip_timestamp") - pl.col("chunk_start_ns")) / float(chunk_ns)).cast(pl.Float32),
                    ((pl.col("sip_timestamp") - pl.col("_previous_ts")) / float(NANOSECONDS_PER_SECOND)).clip(0.0, None).cast(pl.Float32),
                    pl.col("bid_price").cast(pl.Float32),
                    pl.col("ask_price").cast(pl.Float32),
                    pl.col("mid_price").cast(pl.Float32),
                    pl.col("spread_bps").cast(pl.Float32),
                    pl.col("bid_size").cast(pl.Float32),
                    pl.col("ask_size").cast(pl.Float32),
                    pl.col("quote_imbalance").cast(pl.Float32),
                    pl.col("bid_exchange").fill_null(0).cast(pl.Float32),
                    pl.col("ask_exchange").fill_null(0).cast(pl.Float32),
                    pl.col("tape").fill_null(0).cast(pl.Float32),
                    pl.col("condition_count").fill_null(0.0).cast(pl.Float32),
                    pl.col("condition_first").fill_null(0.0).cast(pl.Float32),
                    pl.col("indicator_count").fill_null(0.0).cast(pl.Float32),
                    pl.col("indicator_first").fill_null(0.0).cast(pl.Float32),
                    pl.col("participant_latency_ms").fill_null(0.0).cast(pl.Float32),
                    pl.col("trf_latency_ms").fill_null(0.0).cast(pl.Float32),
                ]
            ).alias("quote_event"),
        )
    )
    return selected.group_by(["session_date", "chunk_start_ns"], maintain_order=True).agg(
        pl.col("quote_event").alias("quote_values")
    )


def selected_trade_chunk_values(config: DataConfig, trades: pl.DataFrame, counts: pl.DataFrame, chunk_ns: int) -> pl.DataFrame:
    if trades.is_empty():
        return empty_chunk_value_frame("trade_values")
    selected = (
        trades.join(counts.select(["session_date", "chunk_start_ns", "selected_trade_count"]), on=["session_date", "chunk_start_ns"], how="left")
        .filter(pl.col("trade_pos") >= (pl.col("trade_count") - pl.col("selected_trade_count")))
        .sort(["session_date", "chunk_start_ns", "sip_timestamp", "sequence_number"])
        .with_columns(
            pl.col("sip_timestamp").shift(1).over(["session_date", "chunk_start_ns"]).alias("_previous_ts"),
        )
        .with_columns(
            pl.when(pl.col("_previous_ts").is_null())
            .then(pl.col("chunk_start_ns"))
            .otherwise(pl.col("_previous_ts"))
            .alias("_previous_ts")
        )
        .with_columns(
            pl.concat_list(
                [
                    ((pl.col("sip_timestamp") - pl.col("chunk_start_ns")) / float(chunk_ns)).cast(pl.Float32),
                    ((pl.col("sip_timestamp") - pl.col("_previous_ts")) / float(NANOSECONDS_PER_SECOND)).clip(0.0, None).cast(pl.Float32),
                    pl.col("price").cast(pl.Float32),
                    pl.col("size").cast(pl.Float32),
                    pl.col("exchange").fill_null(0).cast(pl.Float32),
                    pl.col("latest_bid").fill_null(0.0).cast(pl.Float32),
                    pl.col("latest_ask").fill_null(0.0).cast(pl.Float32),
                    pl.col("latest_mid").fill_null(0.0).cast(pl.Float32),
                    pl.col("latest_spread_bps").fill_null(0.0).cast(pl.Float32),
                    pl.col("latest_quote_imbalance").fill_null(0.0).cast(pl.Float32),
                    pl.col("price_vs_mid_bps").fill_null(0.0).cast(pl.Float32),
                    pl.col("side_proxy").fill_null(0.0).cast(pl.Float32),
                    pl.col("tape").fill_null(0).cast(pl.Float32),
                    pl.col("condition_count").fill_null(0.0).cast(pl.Float32),
                    pl.col("condition_first").fill_null(0.0).cast(pl.Float32),
                    pl.col("correction").fill_null(0.0).cast(pl.Float32),
                    pl.col("trade_id").fill_null(0.0).cast(pl.Float32),
                    pl.col("trf_id").fill_null(0.0).cast(pl.Float32),
                    pl.col("participant_latency_ms").fill_null(0.0).cast(pl.Float32),
                    pl.col("trf_latency_ms").fill_null(0.0).cast(pl.Float32),
                ]
            ).alias("trade_event"),
        )
    )
    return selected.group_by(["session_date", "chunk_start_ns"], maintain_order=True).agg(
        pl.col("trade_event").alias("trade_values")
    )


def selected_event_order_chunks(config: DataConfig, quotes: pl.DataFrame, trades: pl.DataFrame, counts: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    if not quotes.is_empty():
        q = (
            quotes.join(counts.select(["session_date", "chunk_start_ns", "selected_quote_count"]), on=["session_date", "chunk_start_ns"], how="left")
            .filter(pl.col("quote_pos") >= (pl.col("quote_count") - pl.col("selected_quote_count")))
            .with_columns(
                pl.lit(0).alias("event_kind"),
                pl.lit(0).alias("event_priority"),
                (pl.col("quote_pos") - (pl.col("quote_count") - pl.col("selected_quote_count"))).cast(pl.Int64).alias("event_index"),
            )
            .select(["session_date", "chunk_start_ns", "sip_timestamp", "event_priority", "event_kind", "event_index"])
        )
        frames.append(q)
    if not trades.is_empty():
        t = (
            trades.join(counts.select(["session_date", "chunk_start_ns", "selected_trade_count"]), on=["session_date", "chunk_start_ns"], how="left")
            .filter(pl.col("trade_pos") >= (pl.col("trade_count") - pl.col("selected_trade_count")))
            .with_columns(
                pl.lit(1).alias("event_kind"),
                pl.lit(1).alias("event_priority"),
                (pl.col("trade_pos") - (pl.col("trade_count") - pl.col("selected_trade_count"))).cast(pl.Int64).alias("event_index"),
            )
            .select(["session_date", "chunk_start_ns", "sip_timestamp", "event_priority", "event_kind", "event_index"])
        )
        frames.append(t)
    if not frames:
        return pl.DataFrame(
            {
                "session_date": pl.Series([], dtype=pl.Utf8),
                "chunk_start_ns": pl.Series([], dtype=pl.Int64),
                "event_kinds": pl.Series([], dtype=pl.List(pl.Int64)),
                "event_indices": pl.Series([], dtype=pl.List(pl.Int64)),
            }
        )
    selected = pl.concat(frames, how="vertical_relaxed").sort(["session_date", "chunk_start_ns", "sip_timestamp", "event_priority"])
    return selected.group_by(["session_date", "chunk_start_ns"], maintain_order=True).agg(
        pl.col("event_kind").cast(pl.Int64).alias("event_kinds"),
        pl.col("event_index").cast(pl.Int64).alias("event_indices"),
    )


def chunk_summary_vectorized(config: DataConfig, quotes: pl.DataFrame, trades: pl.DataFrame, counts: pl.DataFrame) -> pl.DataFrame:
    overflow_frames: list[pl.DataFrame] = []
    if not quotes.is_empty():
        overflow_quotes = (
            quotes.join(counts.select(["session_date", "chunk_start_ns", "selected_quote_count"]), on=["session_date", "chunk_start_ns"], how="left")
            .filter(pl.col("quote_pos") < (pl.col("quote_count") - pl.col("selected_quote_count")))
            .select(
                "session_date",
                "chunk_start_ns",
                pl.lit(1.0).alias("overflow_quote_count"),
                pl.lit(0.0).alias("overflow_trade_count"),
                pl.lit(0.0).alias("overflow_trade_volume"),
                pl.lit(0.0).alias("overflow_signed_volume"),
                pl.col("mid_price").alias("overflow_mid"),
                pl.col("spread_bps").alias("overflow_spread"),
            )
        )
        overflow_frames.append(overflow_quotes)
    if not trades.is_empty():
        overflow_trades = (
            trades.join(counts.select(["session_date", "chunk_start_ns", "selected_trade_count"]), on=["session_date", "chunk_start_ns"], how="left")
            .filter(pl.col("trade_pos") < (pl.col("trade_count") - pl.col("selected_trade_count")))
            .select(
                "session_date",
                "chunk_start_ns",
                pl.lit(0.0).alias("overflow_quote_count"),
                pl.lit(1.0).alias("overflow_trade_count"),
                pl.col("size").alias("overflow_trade_volume"),
                (pl.col("size") * pl.col("side_proxy")).alias("overflow_signed_volume"),
                pl.col("latest_mid").alias("overflow_mid"),
                pl.col("latest_spread_bps").alias("overflow_spread"),
            )
        )
        overflow_frames.append(overflow_trades)
    if overflow_frames:
        overflow = (
            pl.concat(overflow_frames, how="vertical_relaxed")
            .group_by(["session_date", "chunk_start_ns"])
            .agg(
                pl.col("overflow_quote_count").sum(),
                pl.col("overflow_trade_count").sum(),
                pl.col("overflow_trade_volume").sum(),
                pl.col("overflow_signed_volume").sum(),
                pl.col("overflow_mid").filter(pl.col("overflow_mid") > 0.0).min().fill_null(0.0).alias("overflow_mid_min"),
                pl.col("overflow_mid").filter(pl.col("overflow_mid") > 0.0).max().fill_null(0.0).alias("overflow_mid_max"),
                pl.col("overflow_spread").min().fill_null(0.0).alias("overflow_spread_min_bps"),
                pl.col("overflow_spread").max().fill_null(0.0).alias("overflow_spread_max_bps"),
            )
        )
    else:
        overflow = empty_overflow_summary_frame()
    summary = counts.join(overflow, on=["session_date", "chunk_start_ns"], how="left")
    return summary.with_columns(
        (pl.col("quote_count") + pl.col("trade_count")).alias("event_count"),
        pl.col("overflow_quote_count").fill_null(0.0),
        pl.col("overflow_trade_count").fill_null(0.0),
        pl.col("overflow_trade_volume").fill_null(0.0),
        pl.col("overflow_signed_volume").fill_null(0.0),
        pl.col("overflow_mid_min").fill_null(0.0),
        pl.col("overflow_mid_max").fill_null(0.0),
        pl.col("overflow_spread_min_bps").fill_null(0.0),
        pl.col("overflow_spread_max_bps").fill_null(0.0),
        pl.lit(0.0).alias("seconds_since_trade"),
        pl.lit(0.0).alias("seconds_since_quote"),
        (pl.col("trade_count") > 0.0).cast(pl.Float64).alias("has_trade"),
        (pl.col("quote_count") > 0.0).cast(pl.Float64).alias("has_quote"),
    ).with_columns(
        (pl.col("overflow_quote_count") + pl.col("overflow_trade_count")).alias("overflow_total_count")
    ).select(["session_date", "chunk_start_ns", *CHUNK_SUMMARY_COLUMNS])


def empty_chunk_value_frame(column: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "session_date": pl.Series([], dtype=pl.Utf8),
            "chunk_start_ns": pl.Series([], dtype=pl.Int64),
            column: pl.Series([], dtype=pl.List(pl.List(pl.Float32))),
        }
    )


def empty_overflow_summary_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "session_date": pl.Series([], dtype=pl.Utf8),
            "chunk_start_ns": pl.Series([], dtype=pl.Int64),
            "overflow_quote_count": pl.Series([], dtype=pl.Float64),
            "overflow_trade_count": pl.Series([], dtype=pl.Float64),
            "overflow_trade_volume": pl.Series([], dtype=pl.Float64),
            "overflow_signed_volume": pl.Series([], dtype=pl.Float64),
            "overflow_mid_min": pl.Series([], dtype=pl.Float64),
            "overflow_mid_max": pl.Series([], dtype=pl.Float64),
            "overflow_spread_min_bps": pl.Series([], dtype=pl.Float64),
            "overflow_spread_max_bps": pl.Series([], dtype=pl.Float64),
        }
    )


def count_parquet_rows(paths: Any) -> int:
    path_list = [str(path) for path in paths]
    if not path_list:
        return 0
    return int(scan_parquet_paths(path_list).select(pl.len()).collect().item())


def scan_parquet_paths(paths: Any) -> pl.LazyFrame:
    path_list = [str(path) for path in paths]
    if len(path_list) == 1:
        return pl.scan_parquet(path_list[0])
    return pl.concat([pl.scan_parquet(path) for path in path_list], how="vertical_relaxed")


def write_lazy_parquet(frame: pl.LazyFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.sink_parquet(path, compression="zstd", mkdir=True)
    except (AttributeError, TypeError, ValueError):
        frame.collect().write_parquet(path, compression="zstd")


def optional_int_expr(column: str, names: set[str]) -> pl.Expr:
    if column in names:
        return pl.col(column).cast(pl.Int64, strict=False).fill_null(0)
    return pl.lit(0, dtype=pl.Int64).alias(column)


def metadata_list_count_expr(column: str, names: set[str]) -> pl.Expr:
    if column not in names:
        return pl.lit(0.0)
    text = pl.col(column).cast(pl.String).fill_null("").str.strip_chars()
    return (
        pl.when(text.str.len_chars() > 0)
        .then(text.str.count_matches(",") + 1)
        .otherwise(0)
        .cast(pl.Float32)
    )


def metadata_list_first_expr(column: str, names: set[str]) -> pl.Expr:
    if column not in names:
        return pl.lit(0.0)
    text = pl.col(column).cast(pl.String).fill_null("").str.strip_chars()
    return (
        pl.when(text.str.len_chars() > 0)
        .then(text.str.split(",").list.get(0).cast(pl.Float64, strict=False).fill_null(0.0))
        .otherwise(0.0)
        .cast(pl.Float32)
    )


def timestamp_latency_ms_expr(source_column: str) -> pl.Expr:
    source = pl.col(source_column).cast(pl.Int64, strict=False).fill_null(0)
    sip = pl.col("sip_timestamp").cast(pl.Int64, strict=False).fill_null(0)
    return (
        pl.when(source > 0)
        .then(((sip - source) / 1_000_000.0).clip(0.0, 60_000.0))
        .otherwise(0.0)
        .cast(pl.Float32)
    )


def ticker_bucket_expr(bucket_count: int = TEMP_TICKER_BUCKET_COUNT) -> pl.Expr:
    return (pl.col("ticker").hash(seed=17) % int(bucket_count)).cast(pl.Int32)


def temp_ticker_bucket(ticker: str, bucket_count: int = TEMP_TICKER_BUCKET_COUNT) -> int:
    return int(zlib.crc32(ticker.encode("utf-8")) % int(bucket_count))


def safe_temp_filename_part(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def quote_state_exprs() -> list[pl.Expr]:
    return [
        ((pl.col("bid_price") + pl.col("ask_price")) * 0.5).cast(pl.Float32).alias("mid_price"),
        (10000.0 * (pl.col("ask_price") - pl.col("bid_price")) / ((pl.col("bid_price") + pl.col("ask_price")) * 0.5)).cast(pl.Float32).alias("spread_bps"),
        ((pl.col("bid_size") - pl.col("ask_size")) / (pl.col("bid_size") + pl.col("ask_size")).clip(1.0, None)).cast(pl.Float32).alias("quote_imbalance"),
    ]


def attach_quote_state_to_trades(trades: pl.DataFrame, quotes: pl.DataFrame) -> pl.DataFrame:
    if trades.is_empty():
        return trades
    if quotes.is_empty():
        return trades.with_columns(
            pl.lit(0.0).alias("latest_bid"),
            pl.lit(0.0).alias("latest_ask"),
            pl.lit(0.0).alias("latest_mid"),
            pl.lit(0.0).alias("latest_spread_bps"),
            pl.lit(0.0).alias("latest_quote_imbalance"),
            pl.lit(0.0).alias("side_proxy"),
            pl.lit(0.0).alias("price_vs_mid_bps"),
        )
    quote_state = quotes.select(
        "ticker",
        "sip_timestamp",
        pl.col("bid_price").alias("latest_bid"),
        pl.col("ask_price").alias("latest_ask"),
        pl.col("mid_price").alias("latest_mid"),
        pl.col("spread_bps").alias("latest_spread_bps"),
        pl.col("quote_imbalance").alias("latest_quote_imbalance"),
    ).sort(["ticker", "sip_timestamp"])
    joined = trades.join_asof(quote_state, on="sip_timestamp", by="ticker", strategy="backward")
    return joined.with_columns(
        pl.col("latest_bid").fill_null(0.0),
        pl.col("latest_ask").fill_null(0.0),
        pl.col("latest_mid").fill_null(0.0),
        pl.col("latest_spread_bps").fill_null(0.0),
        pl.col("latest_quote_imbalance").fill_null(0.0),
    ).with_columns(
        pl.when(pl.col("price") >= pl.col("latest_ask"))
        .then(1.0)
        .when(pl.col("price") <= pl.col("latest_bid"))
        .then(-1.0)
        .when(pl.col("price") > pl.col("latest_mid"))
        .then(1.0)
        .when(pl.col("price") < pl.col("latest_mid"))
        .then(-1.0)
        .otherwise(0.0)
        .alias("side_proxy"),
        (10000.0 * (pl.col("price") - pl.col("latest_mid")) / pl.col("latest_mid").clip(1e-6, None))
        .fill_null(0.0)
        .alias("price_vs_mid_bps"),
    )


def build_ticker_sparse_chunks(
    config: DataConfig,
    session: str,
    ticker: str,
    quotes: pl.DataFrame,
    trades: pl.DataFrame,
) -> pl.DataFrame | None:
    quote_events = quote_event_records(config, quotes)
    trade_events = trade_event_records(config, trades)
    if not quote_events and not trade_events:
        return None
    events = sorted(quote_events + trade_events, key=lambda item: (item["chunk_start_ns"], item["sip_timestamp"], item["event_priority"]))
    by_chunk: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        by_chunk.setdefault(int(event["chunk_start_ns"]), []).append(event)
    rows = []
    for chunk_start_ns, chunk_events in by_chunk.items():
        rows.append(chunk_row(config, session, ticker, chunk_start_ns, chunk_events))
    return pl.DataFrame(rows) if rows else None


def quote_event_records(config: DataConfig, quotes: pl.DataFrame) -> list[dict[str, Any]]:
    if quotes.is_empty():
        return []
    chunk_ns = config.chunk_ms * 1_000_000
    result = []
    for row in quotes.iter_rows(named=True):
        ts = int(row["sip_timestamp"])
        result.append(
            {
                "event_kind": 0,
                "event_priority": 0,
                "session_date": str(row.get("session_date") or ""),
                "sip_timestamp": ts,
                "chunk_start_ns": (ts // chunk_ns) * chunk_ns,
                "bid_price": float(row["bid_price"]),
                "ask_price": float(row["ask_price"]),
                "mid_price": float(row["mid_price"]),
                "spread_bps": float(row["spread_bps"]),
                "bid_size": float(row["bid_size"]),
                "ask_size": float(row["ask_size"]),
                "quote_imbalance": float(row["quote_imbalance"]),
                "bid_exchange": float(row.get("bid_exchange") or 0),
                "ask_exchange": float(row.get("ask_exchange") or 0),
                "tape": float(row.get("tape") or 0),
                "condition_count": float(row.get("condition_count") or 0.0),
                "condition_first": float(row.get("condition_first") or 0.0),
                "indicator_count": float(row.get("indicator_count") or 0.0),
                "indicator_first": float(row.get("indicator_first") or 0.0),
                "participant_latency_ms": float(row.get("participant_latency_ms") or 0.0),
                "trf_latency_ms": float(row.get("trf_latency_ms") or 0.0),
            }
        )
    return result


def trade_event_records(config: DataConfig, trades: pl.DataFrame) -> list[dict[str, Any]]:
    if trades.is_empty():
        return []
    chunk_ns = config.chunk_ms * 1_000_000
    result = []
    for row in trades.iter_rows(named=True):
        ts = int(row["sip_timestamp"])
        result.append(
            {
                "event_kind": 1,
                "event_priority": 1,
                "session_date": str(row.get("session_date") or ""),
                "sip_timestamp": ts,
                "chunk_start_ns": (ts // chunk_ns) * chunk_ns,
                "price": float(row["price"]),
                "size": float(row["size"]),
                "exchange": float(row.get("exchange") or 0),
                "latest_bid": float(row.get("latest_bid") or 0.0),
                "latest_ask": float(row.get("latest_ask") or 0.0),
                "latest_mid": float(row.get("latest_mid") or 0.0),
                "latest_spread_bps": float(row.get("latest_spread_bps") or 0.0),
                "latest_quote_imbalance": float(row.get("latest_quote_imbalance") or 0.0),
                "price_vs_mid_bps": float(row.get("price_vs_mid_bps") or 0.0),
                "side_proxy": float(row.get("side_proxy") or 0.0),
                "tape": float(row.get("tape") or 0),
                "condition_count": float(row.get("condition_count") or 0.0),
                "condition_first": float(row.get("condition_first") or 0.0),
                "correction": float(row.get("correction") or 0.0),
                "trade_id": float(row.get("trade_id") or 0.0),
                "trf_id": float(row.get("trf_id") or 0.0),
                "participant_latency_ms": float(row.get("participant_latency_ms") or 0.0),
                "trf_latency_ms": float(row.get("trf_latency_ms") or 0.0),
            }
        )
    return result


def chunk_row(config: DataConfig, session: str, ticker: str, chunk_start_ns: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    chunk_ns = config.chunk_ms * 1_000_000
    quote_events = [event for event in events if event["event_kind"] == 0]
    trade_events = [event for event in events if event["event_kind"] == 1]
    selected_trades = trade_events[-min(config.max_trade_events, config.max_total_events) :]
    remaining_capacity = max(0, config.max_total_events - len(selected_trades))
    selected_quotes = quote_events[-min(config.max_quote_events, remaining_capacity) :]
    selected_events = sorted(selected_quotes + selected_trades, key=lambda item: (item["sip_timestamp"], item["event_priority"]))
    quote_index = {id(event): idx for idx, event in enumerate(selected_quotes)}
    trade_index = {id(event): idx for idx, event in enumerate(selected_trades)}
    selected_event_kinds = []
    selected_event_indices = []
    for event in selected_events:
        if event["event_kind"] == 0 and id(event) in quote_index:
            selected_event_kinds.append(0)
            selected_event_indices.append(quote_index[id(event)])
        elif event["event_kind"] == 1 and id(event) in trade_index:
            selected_event_kinds.append(1)
            selected_event_indices.append(trade_index[id(event)])
    selected_event_kinds = pad_int(selected_event_kinds, config.max_total_events, 2)
    selected_event_indices = pad_int(selected_event_indices, config.max_total_events, 0)
    quote_values = quote_feature_matrix(config, selected_quotes, chunk_start_ns).reshape(-1).tolist()
    trade_values = trade_feature_matrix(config, selected_trades, chunk_start_ns).reshape(-1).tolist()
    summary = chunk_summary(events, selected_events)
    return {
        "ticker": ticker,
        "session_date": session or str(events[-1].get("session_date", "")),
        "chunk_start_ns": chunk_start_ns,
        "chunk_end_ns": chunk_start_ns + chunk_ns - 1,
        "quote_values": quote_values,
        "trade_values": trade_values,
        "event_kinds": selected_event_kinds,
        "event_indices": selected_event_indices,
        **summary,
    }


def enforce_total_cap(events: list[dict[str, Any]], max_total: int, *, reserve_trades: int) -> list[dict[str, Any]]:
    if len(events) <= max_total:
        return events
    trades = [event for event in events if event["event_kind"] == 1]
    recent_trades = trades[-min(reserve_trades, len(trades)) :]
    selected_ids = {id(event) for event in recent_trades}
    remaining = [event for event in events if id(event) not in selected_ids]
    tail = remaining[-max(0, max_total - len(recent_trades)) :]
    return sorted(recent_trades + tail, key=lambda item: (item["sip_timestamp"], item["event_priority"]))


def quote_feature_matrix(config: DataConfig, events: list[dict[str, Any]], chunk_start_ns: int) -> np.ndarray:
    values = np.zeros((config.max_quote_events, len(QUOTE_FEATURE_COLUMNS)), dtype=np.float32)
    previous_ts = chunk_start_ns
    for row_idx, event in enumerate(events[-config.max_quote_events :]):
        ts = int(event["sip_timestamp"])
        values[row_idx] = [
            (ts - chunk_start_ns) / float(config.chunk_ms * 1_000_000),
            max(0.0, (ts - previous_ts) / 1_000_000_000.0),
            event["bid_price"],
            event["ask_price"],
            event["mid_price"],
            event["spread_bps"],
            event["bid_size"],
            event["ask_size"],
            event["quote_imbalance"],
            event["bid_exchange"],
            event["ask_exchange"],
            event["tape"],
            event["condition_count"],
            event["condition_first"],
            event["indicator_count"],
            event["indicator_first"],
            event["participant_latency_ms"],
            event["trf_latency_ms"],
        ]
        previous_ts = ts
    return values


def trade_feature_matrix(config: DataConfig, events: list[dict[str, Any]], chunk_start_ns: int) -> np.ndarray:
    values = np.zeros((config.max_trade_events, len(TRADE_FEATURE_COLUMNS)), dtype=np.float32)
    previous_ts = chunk_start_ns
    for row_idx, event in enumerate(events[-config.max_trade_events :]):
        ts = int(event["sip_timestamp"])
        values[row_idx] = [
            (ts - chunk_start_ns) / float(config.chunk_ms * 1_000_000),
            max(0.0, (ts - previous_ts) / 1_000_000_000.0),
            event["price"],
            event["size"],
            event["exchange"],
            event["latest_bid"],
            event["latest_ask"],
            event["latest_mid"],
            event["latest_spread_bps"],
            event["latest_quote_imbalance"],
            event["price_vs_mid_bps"],
            event["side_proxy"],
            event["tape"],
            event["condition_count"],
            event["condition_first"],
            event["correction"],
            event["trade_id"],
            event["trf_id"],
            event["participant_latency_ms"],
            event["trf_latency_ms"],
        ]
        previous_ts = ts
    return values


def chunk_summary(events: list[dict[str, Any]], selected_events: list[dict[str, Any]]) -> dict[str, Any]:
    quotes = [event for event in events if event["event_kind"] == 0]
    trades = [event for event in events if event["event_kind"] == 1]
    selected_ids = {id(event) for event in selected_events}
    overflow = [event for event in events if id(event) not in selected_ids]
    latest_quote = quotes[-1] if quotes else {}
    trade_volume = sum(float(event.get("size", 0.0)) for event in trades)
    signed_trade_volume = sum(float(event.get("size", 0.0)) * float(event.get("side_proxy", 0.0)) for event in trades)
    overflow_trades = [event for event in overflow if event["event_kind"] == 1]
    overflow_quotes = [event for event in overflow if event["event_kind"] == 0]
    overflow_mids = [float(event.get("mid_price", event.get("latest_mid", 0.0))) for event in overflow if float(event.get("mid_price", event.get("latest_mid", 0.0))) > 0]
    overflow_spreads = [float(event.get("spread_bps", event.get("latest_spread_bps", 0.0))) for event in overflow]
    return {
        "event_count": float(len(events)),
        "quote_count": float(len(quotes)),
        "trade_count": float(len(trades)),
        "overflow_quote_count": float(len(overflow_quotes)),
        "overflow_trade_count": float(len(overflow_trades)),
        "overflow_total_count": float(len(overflow)),
        "overflow_trade_volume": float(sum(event.get("size", 0.0) for event in overflow_trades)),
        "overflow_signed_volume": float(sum(event.get("size", 0.0) * event.get("side_proxy", 0.0) for event in overflow_trades)),
        "overflow_mid_min": float(min(overflow_mids) if overflow_mids else 0.0),
        "overflow_mid_max": float(max(overflow_mids) if overflow_mids else 0.0),
        "overflow_spread_min_bps": float(min(overflow_spreads) if overflow_spreads else 0.0),
        "overflow_spread_max_bps": float(max(overflow_spreads) if overflow_spreads else 0.0),
        "latest_bid": float(latest_quote.get("bid_price", 0.0)),
        "latest_ask": float(latest_quote.get("ask_price", 0.0)),
        "latest_mid": float(latest_quote.get("mid_price", 0.0)),
        "latest_spread_bps": float(latest_quote.get("spread_bps", 0.0)),
        "latest_bid_size": float(latest_quote.get("bid_size", 0.0)),
        "latest_ask_size": float(latest_quote.get("ask_size", 0.0)),
        "latest_quote_imbalance": float(latest_quote.get("quote_imbalance", 0.0)),
        "trade_volume": float(trade_volume),
        "signed_trade_volume": float(signed_trade_volume),
        "seconds_since_trade": 0.0 if trades else 1e6,
        "seconds_since_quote": 0.0 if quotes else 1e6,
        "has_trade": float(bool(trades)),
        "has_quote": float(bool(quotes)),
    }


def pad_int(values: list[int], length: int, fill: int) -> list[int]:
    if len(values) >= length:
        return values[:length]
    return values + [fill] * (length - len(values))


class EventChunkDataset(IterableDataset):
    def __init__(
        self,
        *,
        config: DataConfig,
        sessions: list[str],
        tickers: tuple[str, ...],
        batch_size: int,
        seed: int,
        mode: str,
        epochs: int = 1,
        max_windows: int = 0,
        shuffle: bool = False,
    ) -> None:
        self.config = config
        self.sessions = list(sessions)
        self.tickers = tickers
        self.batch_size = batch_size
        self.seed = seed
        self.mode = mode
        self.epochs = epochs
        self.max_windows = max_windows
        self.shuffle = shuffle

    def __iter__(self) -> Iterator[dict[str, Any]]:
        worker_info = get_worker_info()
        worker_id = worker_info.id if worker_info is not None else 0
        worker_count = worker_info.num_workers if worker_info is not None else 1
        rng = np.random.default_rng(self.seed + worker_id)
        emitted_windows = 0
        for epoch in range(self.epochs):
            sessions = list(self.sessions)
            if self.shuffle:
                rng.shuffle(sessions)
            sessions = sessions[worker_id::worker_count]
            batch = BatchBuilder(config=self.config, batch_size=self.batch_size)
            for session_index, session in enumerate(sessions, start=1):
                print(LOG_RULE, flush=True)
                print(
                    f"*** {self.mode.upper()} SESSION START {session} | epoch {epoch + 1}/{self.epochs} "
                    f"| worker {worker_id + 1}/{worker_count} | session {session_index}/{len(sessions)}",
                    flush=True,
                )
                sparse = load_or_build_session_event_chunks(self.config, session, self.tickers)
                session_windows = 0
                session_batches = 0
                ticker_frames = iter_ticker_frames(sparse, rng=rng, shuffle=self.shuffle and self.config.shuffle_tickers)
                for ticker, ticker_frame in ticker_frames:
                    arrays = ticker_arrays(ticker_frame, self.config)
                    if arrays is None:
                        continue
                    origins = valid_origins(arrays, self.config)
                    if origins.size == 0:
                        continue
                    if self.shuffle:
                        rng.shuffle(origins)
                    if self.config.max_windows_per_ticker_session > 0:
                        origins = origins[: self.config.max_windows_per_ticker_session]
                    for origin in origins:
                        batch.add(arrays, int(origin), ticker=ticker)
                        session_windows += 1
                        emitted_windows += 1
                        if batch.full:
                            yield batch.as_torch()
                            session_batches += 1
                            batch = batch.empty_like()
                        if 0 < self.max_windows <= emitted_windows:
                            if len(batch) > 0:
                                yield batch.as_torch()
                            print(
                                f"*** {self.mode.upper()} SESSION END   {session} | windows={session_windows:,} "
                                f"| batches={session_batches:,} | max_windows_reached",
                                flush=True,
                            )
                            print(LOG_RULE, flush=True)
                            return
                if len(batch) > 0 and self.mode != "train":
                    yield batch.as_torch()
                    session_batches += 1
                    batch = batch.empty_like()
                print(
                    f"*** {self.mode.upper()} SESSION END   {session} | windows={session_windows:,} "
                    f"| batches={session_batches:,}",
                    flush=True,
                )
                print(LOG_RULE, flush=True)
                del sparse
                gc.collect()
            if len(batch) > 0:
                yield batch.as_torch()


class BatchBuilder:
    def __init__(self, *, config: DataConfig, batch_size: int) -> None:
        self.config = config
        context = config.context_chunks
        self.quote_values = np.empty((batch_size, context, config.max_quote_events, len(QUOTE_FEATURE_COLUMNS)), dtype=np.float32)
        self.trade_values = np.empty((batch_size, context, config.max_trade_events, len(TRADE_FEATURE_COLUMNS)), dtype=np.float32)
        self.event_kinds = np.empty((batch_size, context, config.max_total_events), dtype=np.int64)
        self.event_indices = np.empty((batch_size, context, config.max_total_events), dtype=np.int64)
        self.chunk_summary = np.empty((batch_size, context, len(CHUNK_SUMMARY_COLUMNS)), dtype=np.float32)
        horizon_count = config.target_horizon_count
        self.targets = np.empty((batch_size, horizon_count, 1, target_bit_count(config)), dtype=np.float32)
        self.target_bps = np.empty((batch_size, horizon_count, 1), dtype=np.float32)
        self.target_bid = np.empty((batch_size, horizon_count, 1), dtype=np.float32)
        self.target_ask = np.empty((batch_size, horizon_count, 1), dtype=np.float32)
        self.target_mid = np.empty((batch_size, horizon_count, 1), dtype=np.float32)
        self.current_mid = np.empty((batch_size,), dtype=np.float32)
        self.last_close_return_bps = np.empty((batch_size,), dtype=np.float32)
        self.origin_timestamp_ns = np.empty((batch_size,), dtype=np.int64)
        self.tickers: list[str] = [""] * batch_size
        self.count = 0

    @property
    def full(self) -> bool:
        return self.count >= self.quote_values.shape[0]

    def __len__(self) -> int:
        return self.count

    def empty_like(self) -> "BatchBuilder":
        return BatchBuilder(config=self.config, batch_size=self.quote_values.shape[0])

    def add(self, arrays: dict[str, np.ndarray], origin: int, *, ticker: str) -> None:
        config = self.config
        start = origin - config.context_chunks + 1
        end = origin + 1
        future_indices = origin + np.asarray(config.target_horizon_chunks, dtype=np.int64)
        current_mid = float(mid_from_bid_ask(arrays["bid"][origin], arrays["ask"][origin], arrays["mid"][origin]))
        previous_mid = float(mid_from_bid_ask(arrays["bid"][max(0, origin - 1)], arrays["ask"][max(0, origin - 1)], arrays["mid"][max(0, origin - 1)]))
        future_bid = arrays["bid"][future_indices].reshape(-1, 1).astype(np.float32)
        future_ask = arrays["ask"][future_indices].reshape(-1, 1).astype(np.float32)
        future_mid = mid_from_bid_ask(future_bid, future_ask, arrays["mid"][future_indices].reshape(-1, 1)).astype(np.float32)
        target_bps = log_return_bps(future_mid, current_mid).astype(np.float32)

        quote_window = arrays["quote_values"][start:end]
        trade_window = arrays["trade_values"][start:end]
        summary_window = arrays["chunk_summary"][start:end]
        self.quote_values[self.count] = normalize_event_window(
            quote_window,
            QUOTE_FEATURE_COLUMNS,
            QUOTE_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.trade_values[self.count] = normalize_event_window(
            trade_window,
            TRADE_FEATURE_COLUMNS,
            TRADE_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.chunk_summary[self.count] = normalize_event_window(
            summary_window,
            CHUNK_SUMMARY_COLUMNS,
            SUMMARY_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.event_kinds[self.count] = arrays["event_kinds"][start:end]
        self.event_indices[self.count] = arrays["event_indices"][start:end]
        self.targets[self.count] = encode_binary_magnitude_targets(target_bps, bits=config.binary_magnitude_bits)
        self.target_bps[self.count] = target_bps
        self.target_bid[self.count] = future_bid
        self.target_ask[self.count] = future_ask
        self.target_mid[self.count] = future_mid
        self.current_mid[self.count] = current_mid
        self.last_close_return_bps[self.count] = float(log_return_bps(current_mid, previous_mid))
        self.origin_timestamp_ns[self.count] = int(arrays["chunk_end_ns"][origin])
        self.tickers[self.count] = ticker
        self.count += 1

    def as_torch(self) -> dict[str, Any]:
        if torch is None:
            raise RuntimeError("PyTorch is required to materialize training batches.")
        rows = slice(0, self.count)
        return {
            "quote_values": torch.from_numpy(self.quote_values[rows].copy()),
            "trade_values": torch.from_numpy(self.trade_values[rows].copy()),
            "event_kinds": torch.from_numpy(self.event_kinds[rows].copy()),
            "event_indices": torch.from_numpy(self.event_indices[rows].copy()),
            "chunk_summary": torch.from_numpy(self.chunk_summary[rows].copy()),
            "targets": torch.from_numpy(self.targets[rows].copy()),
            "target_bps": torch.from_numpy(self.target_bps[rows].copy()),
            "target_bid": torch.from_numpy(self.target_bid[rows].copy()),
            "target_ask": torch.from_numpy(self.target_ask[rows].copy()),
            "target_mid": torch.from_numpy(self.target_mid[rows].copy()),
            "current_mid": torch.from_numpy(self.current_mid[rows].copy()),
            "last_close_return_bps": torch.from_numpy(self.last_close_return_bps[rows].copy()),
            "origin_timestamp_ns": torch.from_numpy(self.origin_timestamp_ns[rows].copy()),
            "ticker": list(self.tickers[: self.count]),
        }


def iter_ticker_frames(
    frame: pl.DataFrame,
    *,
    rng: np.random.Generator | None = None,
    shuffle: bool = False,
) -> Iterator[tuple[str, pl.DataFrame]]:
    if frame.is_empty():
        return
    ticker_values = frame.get_column("ticker").to_numpy()
    ranges: list[tuple[str, int, int]] = []
    start = 0
    current = str(ticker_values[0])
    for index in range(1, len(ticker_values)):
        value = str(ticker_values[index])
        if value != current:
            ranges.append((current, start, index - start))
            start = index
            current = value
    ranges.append((current, start, len(ticker_values) - start))
    if shuffle and rng is not None and len(ranges) > 1:
        order = np.arange(len(ranges))
        rng.shuffle(order)
        ranges = [ranges[int(index)] for index in order]
    for ticker, start, length in ranges:
        yield ticker, frame.slice(start, length)


def ticker_arrays(frame: pl.DataFrame, config: DataConfig) -> dict[str, np.ndarray] | None:
    dense = dense_ticker_frame(frame, config)
    if dense.height < config.context_chunks + max(config.target_horizon_chunks):
        return None
    quote_values = list_column_to_matrix(dense, "quote_values", config.max_quote_events, len(QUOTE_FEATURE_COLUMNS))
    trade_values = list_column_to_matrix(dense, "trade_values", config.max_trade_events, len(TRADE_FEATURE_COLUMNS))
    event_kinds = list_column_to_int_matrix(dense, "event_kinds", config.max_total_events, fill=2)
    event_indices = list_column_to_int_matrix(dense, "event_indices", config.max_total_events)
    chunk_summary = dense.select(list(CHUNK_SUMMARY_COLUMNS)).to_numpy().astype(np.float32)
    return {
        "chunk_end_ns": dense.get_column("chunk_end_ns").to_numpy().astype(np.int64),
        "mid": dense.get_column("latest_mid").to_numpy().astype(np.float32),
        "bid": dense.get_column("latest_bid").to_numpy().astype(np.float32),
        "ask": dense.get_column("latest_ask").to_numpy().astype(np.float32),
        "quote_values": quote_values,
        "trade_values": trade_values,
        "event_kinds": event_kinds,
        "event_indices": event_indices,
        "chunk_summary": chunk_summary,
    }


def dense_ticker_frame(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    chunk_ns = config.chunk_ms * 1_000_000
    min_start = int(frame.get_column("chunk_start_ns").min())
    max_start = int(frame.get_column("chunk_start_ns").max())
    grid = (
        pl.DataFrame({"_grid": [0]})
        .select(pl.int_ranges(pl.lit(min_start), pl.lit(max_start + chunk_ns), pl.lit(chunk_ns)).alias("chunk_start_ns"))
        .explode("chunk_start_ns")
    )
    joined = grid.join(frame.drop(["ticker", "session_date"], strict=False), on="chunk_start_ns", how="left")
    joined = joined.with_columns((pl.col("chunk_start_ns") + chunk_ns - 1).alias("chunk_end_ns"))
    quote_state_cols = [
        "latest_bid",
        "latest_ask",
        "latest_mid",
        "latest_spread_bps",
        "latest_bid_size",
        "latest_ask_size",
        "latest_quote_imbalance",
    ]
    joined = joined.with_columns([pl.col(column).forward_fill() for column in quote_state_cols])
    joined = joined.filter(pl.col("latest_mid").is_not_null() & (pl.col("latest_mid") > 0.0))
    zero_summary = [
        column
        for column in CHUNK_SUMMARY_COLUMNS
        if column not in quote_state_cols and column not in {"seconds_since_trade", "seconds_since_quote"}
    ]
    joined = joined.with_columns([pl.col(column).fill_null(0.0) for column in zero_summary])
    joined = joined.with_columns(
        pl.col("seconds_since_trade").fill_null(1e6),
        pl.col("seconds_since_quote").fill_null(1e6),
    )
    joined = add_age_features(joined, config)
    return joined.select(
        [
            "chunk_start_ns",
            "chunk_end_ns",
            "quote_values",
            "trade_values",
            "event_kinds",
            "event_indices",
            *CHUNK_SUMMARY_COLUMNS,
        ]
    )


def add_age_features(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    return (
        frame.with_columns(
            pl.when(pl.col("has_trade") > 0.0).then(pl.col("chunk_start_ns")).otherwise(None).forward_fill().alias("_last_trade_chunk_ns"),
            pl.when(pl.col("has_quote") > 0.0).then(pl.col("chunk_start_ns")).otherwise(None).forward_fill().alias("_last_quote_chunk_ns"),
        )
        .with_columns(
            pl.when(pl.col("_last_trade_chunk_ns").is_null())
            .then(1_000_000.0)
            .otherwise(((pl.col("chunk_start_ns") - pl.col("_last_trade_chunk_ns")) / float(NANOSECONDS_PER_SECOND)).clip(0.0, 3600.0))
            .cast(pl.Float32)
            .alias("seconds_since_trade"),
            pl.when(pl.col("_last_quote_chunk_ns").is_null())
            .then(1_000_000.0)
            .otherwise(((pl.col("chunk_start_ns") - pl.col("_last_quote_chunk_ns")) / float(NANOSECONDS_PER_SECOND)).clip(0.0, 3600.0))
            .cast(pl.Float32)
            .alias("seconds_since_quote"),
        )
        .drop(["_last_trade_chunk_ns", "_last_quote_chunk_ns"])
    )


def empty_float_list(length: int) -> list[float]:
    return [0.0] * length


def list_column_to_matrix(frame: pl.DataFrame, column: str, rows: int, cols: int) -> np.ndarray:
    values = np.zeros((frame.height, rows, cols), dtype=np.float32)
    for idx, item in enumerate(frame.get_column(column).to_list()):
        arr = np.asarray(item or [], dtype=np.float32)
        if arr.size:
            if arr.ndim == 1:
                arr = arr.reshape(-1, cols)
            arr = arr.reshape(-1, cols)
            keep = min(rows, arr.shape[0])
            values[idx, :keep] = arr[:keep]
    return values


def list_column_to_int_matrix(frame: pl.DataFrame, column: str, width: int, fill: int = 0) -> np.ndarray:
    values = np.full((frame.height, width), fill, dtype=np.int64)
    for idx, item in enumerate(frame.get_column(column).to_list()):
        arr = np.asarray(item or [], dtype=np.int64)
        if arr.size:
            keep = min(width, arr.size)
            values[idx, :keep] = arr[:keep]
    return values


def valid_origins(arrays: dict[str, np.ndarray], config: DataConfig) -> np.ndarray:
    chunk_end = arrays["chunk_end_ns"]
    mid = arrays["mid"]
    bid = arrays["bid"]
    ask = arrays["ask"]
    future_offsets = np.asarray(config.target_horizon_chunks, dtype=np.int64)
    earliest = config.context_chunks - 1
    latest = len(chunk_end) - int(future_offsets[-1]) - 1
    if latest < earliest:
        return np.empty((0,), dtype=np.int64)
    candidates = np.arange(earliest, latest + 1, max(1, config.origin_stride_chunks), dtype=np.int64)
    future_indices = candidates[:, None] + future_offsets.reshape(1, -1)
    valid_current = (bid[candidates] > 0.0) & (ask[candidates] > 0.0) & (mid[candidates] > 0.0)
    valid_future = np.all((bid[future_indices] > 0.0) & (ask[future_indices] > 0.0), axis=1)
    valid_mid = valid_current & valid_future
    return candidates[valid_mid]


def mid_from_bid_ask(bid: Any, ask: Any, fallback_mid: Any) -> Any:
    bid_array = np.asarray(bid, dtype=np.float32)
    ask_array = np.asarray(ask, dtype=np.float32)
    fallback = np.asarray(fallback_mid, dtype=np.float32)
    quote_mid = (bid_array + ask_array) * 0.5
    return np.where((bid_array > 0.0) & (ask_array > 0.0), quote_mid, fallback)


def normalize_event_window(
    window: np.ndarray,
    columns: tuple[str, ...],
    price_columns: set[str],
    *,
    current_mid: float,
) -> np.ndarray:
    values = np.asarray(window, dtype=np.float32).copy()
    current_mid_safe = max(float(current_mid), 1e-6)
    for index, column in enumerate(columns):
        column_values = values[..., index]
        if column in price_columns:
            safe = np.maximum(column_values, 1e-6)
            values[..., index] = np.where(column_values > 0.0, np.log(safe / current_mid_safe) * 10000.0, 0.0)
        elif column in LOG_COLUMNS or column.endswith("_count") or column.endswith("_volume"):
            values[..., index] = np.log1p(np.maximum(column_values, 0.0))
    flat = values.reshape(-1, values.shape[-1])
    mean = flat.mean(axis=0, keepdims=True)
    std = flat.std(axis=0, keepdims=True)
    std = np.where(std > 1e-6, std, 1.0)
    normalized = (values - mean.reshape((1,) * (values.ndim - 1) + (-1,))) / std.reshape((1,) * (values.ndim - 1) + (-1,))
    return np.nan_to_num(normalized, nan=0.0, posinf=10.0, neginf=-10.0).astype(np.float32)


def target_bit_count(config: DataConfig) -> int:
    if config.target_mode == "binary_magnitude_bps":
        return 1 + int(config.binary_magnitude_bits)
    raise ValueError(f"Unsupported target mode: {config.target_mode}")


def collect_lazy(frame: pl.LazyFrame) -> pl.DataFrame:
    try:
        return frame.collect(engine="streaming")
    except (TypeError, ValueError):
        try:
            return frame.collect(streaming=True)
        except (TypeError, ValueError):
            return frame.collect()


def count_coverage(*, config: DataConfig, sessions: list[str], tickers: tuple[str, ...], batch_size: int) -> SessionCoverage:
    coverage = SessionCoverage(sessions=len(sessions))
    for index, session in enumerate(sessions, start=1):
        sparse = load_or_build_session_event_chunks(config, session, tickers)
        session_windows = 0
        for _, ticker_frame in iter_ticker_frames(sparse):
            arrays = ticker_arrays(ticker_frame, config)
            if arrays is not None:
                session_windows += int(valid_origins(arrays, config).size)
        coverage.windows += session_windows
        coverage.batches += math.ceil(session_windows / batch_size) if session_windows else 0
        if session_windows:
            coverage.sessions_with_windows += 1
        print(
            f"Coverage count {session} ({index}/{len(sessions)}): "
            f"windows={session_windows:,} cumulative_windows={coverage.windows:,}",
            flush=True,
        )
    return coverage
