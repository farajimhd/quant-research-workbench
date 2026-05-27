from __future__ import annotations

import gc
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

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

from research.inhouse_transformer.v14.data import (
    binary_magnitude_logits_to_distribution_stats,
    encode_binary_magnitude_targets,
    log_return_bps,
)
from research.inhouse_transformer.v21.config import DataConfig


LOG_RULE = "*" * 96
ALL_TICKERS_SENTINEL = "__ALL_TICKERS__"
NANOSECONDS_PER_SECOND = 1_000_000_000
QUOTE_SIZE_UNIT_SWITCH_DATE = "2025-11-03"

ONE_SECOND_FEATURE_COLUMNS: tuple[str, ...] = (
    "bid_first",
    "bid_last",
    "ask_first",
    "ask_last",
    "mid_first",
    "mid_last",
    "mid_min",
    "mid_max",
    "spread_first_bps",
    "spread_last_bps",
    "spread_min_bps",
    "spread_max_bps",
    "bid_size_first",
    "bid_size_last",
    "bid_size_min",
    "bid_size_max",
    "ask_size_first",
    "ask_size_last",
    "ask_size_min",
    "ask_size_max",
    "quote_imbalance_first",
    "quote_imbalance_last",
    "quote_imbalance_min",
    "quote_imbalance_max",
    "quote_update_count",
    "trade_count",
    "trade_volume",
    "dollar_volume",
    "signed_trade_volume",
    "trade_imbalance",
    "trade_volume_at_ask",
    "trade_volume_at_bid",
    "trade_volume_inside_spread",
    "trade_volume_above_mid",
    "trade_volume_below_mid",
    "trade_price_first",
    "trade_price_last",
    "trade_price_min",
    "trade_price_max",
    "trade_price_vs_mid_last_bps",
    "trade_price_vs_mid_min_bps",
    "trade_price_vs_mid_max_bps",
    "largest_trade_size",
    "first_trade_pos",
    "last_trade_pos",
    "first_quote_pos",
    "last_quote_pos",
    "quote_before_trade",
    "seconds_since_trade",
    "seconds_since_quote",
    "has_trade",
    "has_quote_update",
    "hour_sin",
    "hour_cos",
)

TEN_SECOND_BASE_COLUMNS: tuple[str, ...] = (
    "mid_first",
    "mid_last",
    "mid_min",
    "mid_max",
    "spread_first_bps",
    "spread_last_bps",
    "spread_min_bps",
    "spread_max_bps",
    "bid_size_first",
    "bid_size_last",
    "ask_size_first",
    "ask_size_last",
    "quote_imbalance_first",
    "quote_imbalance_last",
    "quote_update_count",
    "trade_count",
    "trade_volume",
    "signed_trade_volume",
    "trade_imbalance",
    "first_trade_pos",
    "last_trade_pos",
    "first_quote_pos",
    "last_quote_pos",
    "seconds_since_trade",
    "seconds_since_quote",
)
TEN_SECOND_SLOT_COLUMNS: tuple[str, ...] = tuple(
    f"slot_{slot}_{name}"
    for slot in range(10)
    for name in (
        "trade_count",
        "signed_trade_volume",
        "quote_update_count",
        "mid_last",
        "spread_last_bps",
        "quote_imbalance_last",
    )
)
TEN_SECOND_FEATURE_COLUMNS: tuple[str, ...] = TEN_SECOND_BASE_COLUMNS + TEN_SECOND_SLOT_COLUMNS

ONE_SECOND_PRICE_COLUMNS = {
    "bid_first",
    "bid_last",
    "ask_first",
    "ask_last",
    "mid_first",
    "mid_last",
    "mid_min",
    "mid_max",
    "trade_price_first",
    "trade_price_last",
    "trade_price_min",
    "trade_price_max",
}
TEN_SECOND_PRICE_COLUMNS = {"mid_first", "mid_last", "mid_min", "mid_max"} | {
    f"slot_{slot}_mid_last" for slot in range(10)
}
LOG_COLUMNS = {
    "bid_size_first",
    "bid_size_last",
    "bid_size_min",
    "bid_size_max",
    "ask_size_first",
    "ask_size_last",
    "ask_size_min",
    "ask_size_max",
    "quote_update_count",
    "trade_count",
    "trade_volume",
    "dollar_volume",
    "trade_volume_at_ask",
    "trade_volume_at_bid",
    "trade_volume_inside_spread",
    "trade_volume_above_mid",
    "trade_volume_below_mid",
    "largest_trade_size",
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
    filenames = (
        f"{session}.csv.gz",
        f"{session}.csv",
        f"{session}.gz",
    )
    for candidate_root in flatfile_root_candidates(flatfiles_root):
        for root_name in roots:
            base = candidate_root / root_name
            for filename in filenames:
                candidates = (
                    base / year / month / filename,
                    base / year / filename,
                    base / filename,
                )
                for candidate in candidates:
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
        candidates.append(flatfiles_root / "us_stocks_sip")
        candidates.append(flatfiles_root / "us_stock_sip")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def cached_snapshot_path(config: DataConfig, session: str) -> Path:
    return config.cache_root / "one_second_snapshots" / session[:4] / session[5:7] / f"{session}.parquet"


def load_or_build_session_snapshots(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    cache_path = cached_snapshot_path(config, session)
    if cache_path.exists() and not config.rebuild_cache:
        scan = pl.scan_parquet(str(cache_path))
        if tickers and not uses_all_tickers(tickers):
            scan = scan.filter(pl.col("ticker").is_in(list(tickers)))
        return collect_lazy(scan)

    frame = build_sparse_one_second_snapshots(config, session, tickers)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(cache_path, compression="zstd")
    return frame


def build_sparse_one_second_snapshots(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    quote_path = find_flatfile(config.flatfiles_root, "quotes", session)
    trade_path = find_flatfile(config.flatfiles_root, "trades", session)
    if quote_path is None or trade_path is None:
        raise FileNotFoundError(f"Missing quotes/trades flatfiles for {session} under {config.flatfiles_root}.")

    quotes = read_quotes(config, quote_path, session, tickers)
    trades = read_trades(config, trade_path, tickers)
    quote_buckets = aggregate_quotes(quotes)
    trade_buckets = aggregate_trades(trades, quotes)
    if quote_buckets.is_empty() and trade_buckets.is_empty():
        return pl.DataFrame()
    if quote_buckets.is_empty():
        combined = trade_buckets
    elif trade_buckets.is_empty():
        combined = quote_buckets
    else:
        combined = quote_buckets.join(trade_buckets, on=["ticker", "second"], how="full", coalesce=True)
    return (
        combined.with_columns(pl.lit(session).alias("session_date"))
        .sort(["ticker", "second"])
        .pipe(fill_sparse_defaults)
    )


def read_quotes(config: DataConfig, path: Path, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    required = [
        "ticker",
        "sip_timestamp",
        "sequence_number",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "bid_exchange",
        "ask_exchange",
    ]
    scan = pl.scan_csv(str(path), infer_schema_length=1000, ignore_errors=True)
    names = set(scan.collect_schema().names())
    missing = sorted(set(required[:7]) - names)
    if missing:
        raise SystemExit(f"Quote flatfile {path} is missing required columns: {missing}")
    selected = [column for column in required if column in names]
    scan = scan.select(selected)
    if tickers and not uses_all_tickers(tickers):
        scan = scan.filter(pl.col("ticker").is_in(list(tickers)))
    start_ns = config.session_start_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    end_ns = config.session_end_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    day_ns = pl.col("sip_timestamp").cast(pl.Int64, strict=False) % (24 * 3600 * NANOSECONDS_PER_SECOND)
    scan = scan.filter((day_ns >= start_ns) & (day_ns < end_ns))
    multiplier = config.quote_size_lot_multiplier_before_2025_11_03 if session < QUOTE_SIZE_UNIT_SWITCH_DATE else 1
    return (
        scan.with_columns(
            pl.col("ticker").cast(pl.String).str.to_uppercase(),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            pl.col("bid_price").cast(pl.Float64, strict=False),
            pl.col("ask_price").cast(pl.Float64, strict=False),
            (pl.col("bid_size").cast(pl.Float64, strict=False).fill_null(0.0) * float(multiplier)).alias("bid_size"),
            (pl.col("ask_size").cast(pl.Float64, strict=False).fill_null(0.0) * float(multiplier)).alias("ask_size"),
        )
        .filter((pl.col("bid_price") > 0.0) & (pl.col("ask_price") > 0.0) & (pl.col("ask_price") >= pl.col("bid_price")))
        .sort(["ticker", "sip_timestamp", "sequence_number"])
        .pipe(collect_lazy)
    )


def read_trades(config: DataConfig, path: Path, tickers: tuple[str, ...]) -> pl.DataFrame:
    required = ["ticker", "sip_timestamp", "sequence_number", "price", "size", "exchange"]
    scan = pl.scan_csv(str(path), infer_schema_length=1000, ignore_errors=True)
    names = set(scan.collect_schema().names())
    missing = sorted(set(required[:5]) - names)
    if missing:
        raise SystemExit(f"Trade flatfile {path} is missing required columns: {missing}")
    selected = [column for column in required if column in names]
    scan = scan.select(selected)
    if tickers and not uses_all_tickers(tickers):
        scan = scan.filter(pl.col("ticker").is_in(list(tickers)))
    start_ns = config.session_start_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    end_ns = config.session_end_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    day_ns = pl.col("sip_timestamp").cast(pl.Int64, strict=False) % (24 * 3600 * NANOSECONDS_PER_SECOND)
    return (
        scan.filter((day_ns >= start_ns) & (day_ns < end_ns))
        .with_columns(
            pl.col("ticker").cast(pl.String).str.to_uppercase(),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
            pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
            pl.col("price").cast(pl.Float64, strict=False),
            pl.col("size").cast(pl.Float64, strict=False).fill_null(0.0),
        )
        .filter((pl.col("price") > 0.0) & (pl.col("size") > 0.0))
        .sort(["ticker", "sip_timestamp", "sequence_number"])
        .pipe(collect_lazy)
    )


def aggregate_quotes(quotes: pl.DataFrame) -> pl.DataFrame:
    if quotes.is_empty():
        return pl.DataFrame()
    prepared = quotes.with_columns(
        (pl.col("sip_timestamp") // NANOSECONDS_PER_SECOND).cast(pl.Int64).alias("second"),
        ((pl.col("sip_timestamp") % NANOSECONDS_PER_SECOND) / float(NANOSECONDS_PER_SECOND)).alias("quote_pos"),
        ((pl.col("bid_price") + pl.col("ask_price")) * 0.5).alias("mid"),
        (10000.0 * (pl.col("ask_price") - pl.col("bid_price")) / ((pl.col("bid_price") + pl.col("ask_price")) * 0.5)).alias(
            "spread_bps"
        ),
        (
            (pl.col("bid_size") - pl.col("ask_size"))
            / (pl.col("bid_size") + pl.col("ask_size")).clip(1.0, None)
        ).alias("quote_imbalance"),
    )
    return prepared.group_by(["ticker", "second"], maintain_order=True).agg(
        pl.col("bid_price").first().alias("bid_first"),
        pl.col("bid_price").last().alias("bid_last"),
        pl.col("ask_price").first().alias("ask_first"),
        pl.col("ask_price").last().alias("ask_last"),
        pl.col("mid").first().alias("mid_first"),
        pl.col("mid").last().alias("mid_last"),
        pl.col("mid").min().alias("mid_min"),
        pl.col("mid").max().alias("mid_max"),
        pl.col("spread_bps").first().alias("spread_first_bps"),
        pl.col("spread_bps").last().alias("spread_last_bps"),
        pl.col("spread_bps").min().alias("spread_min_bps"),
        pl.col("spread_bps").max().alias("spread_max_bps"),
        pl.col("bid_size").first().alias("bid_size_first"),
        pl.col("bid_size").last().alias("bid_size_last"),
        pl.col("bid_size").min().alias("bid_size_min"),
        pl.col("bid_size").max().alias("bid_size_max"),
        pl.col("ask_size").first().alias("ask_size_first"),
        pl.col("ask_size").last().alias("ask_size_last"),
        pl.col("ask_size").min().alias("ask_size_min"),
        pl.col("ask_size").max().alias("ask_size_max"),
        pl.col("quote_imbalance").first().alias("quote_imbalance_first"),
        pl.col("quote_imbalance").last().alias("quote_imbalance_last"),
        pl.col("quote_imbalance").min().alias("quote_imbalance_min"),
        pl.col("quote_imbalance").max().alias("quote_imbalance_max"),
        pl.len().alias("quote_update_count"),
        pl.col("quote_pos").min().alias("first_quote_pos"),
        pl.col("quote_pos").max().alias("last_quote_pos"),
    )


def aggregate_trades(trades: pl.DataFrame, quotes: pl.DataFrame) -> pl.DataFrame:
    if trades.is_empty():
        return pl.DataFrame()
    quote_state = quotes.select("ticker", "sip_timestamp", "bid_price", "ask_price").sort(["ticker", "sip_timestamp"])
    joined = trades.join_asof(
        quote_state,
        on="sip_timestamp",
        by="ticker",
        strategy="backward",
    )
    prepared = joined.with_columns(
        (pl.col("sip_timestamp") // NANOSECONDS_PER_SECOND).cast(pl.Int64).alias("second"),
        ((pl.col("sip_timestamp") % NANOSECONDS_PER_SECOND) / float(NANOSECONDS_PER_SECOND)).alias("trade_pos"),
        ((pl.col("bid_price") + pl.col("ask_price")) * 0.5).alias("mid"),
    ).with_columns(
        pl.when(pl.col("price") >= pl.col("ask_price"))
        .then(1.0)
        .when(pl.col("price") <= pl.col("bid_price"))
        .then(-1.0)
        .when(pl.col("price") > pl.col("mid"))
        .then(1.0)
        .when(pl.col("price") < pl.col("mid"))
        .then(-1.0)
        .otherwise(0.0)
        .alias("trade_side"),
        (10000.0 * (pl.col("price") - pl.col("mid")) / pl.col("mid").clip(1e-6, None)).fill_null(0.0).alias(
            "trade_price_vs_mid_bps"
        ),
    )
    return prepared.group_by(["ticker", "second"], maintain_order=True).agg(
        pl.len().alias("trade_count"),
        pl.col("size").sum().alias("trade_volume"),
        (pl.col("size") * pl.col("price")).sum().alias("dollar_volume"),
        (pl.col("size") * pl.col("trade_side")).sum().alias("signed_trade_volume"),
        pl.when(pl.col("trade_side") > 0).then(pl.col("size")).otherwise(0.0).sum().alias("trade_volume_at_ask"),
        pl.when(pl.col("trade_side") < 0).then(pl.col("size")).otherwise(0.0).sum().alias("trade_volume_at_bid"),
        pl.when(pl.col("trade_side") == 0).then(pl.col("size")).otherwise(0.0).sum().alias("trade_volume_inside_spread"),
        pl.when(pl.col("price") > pl.col("mid")).then(pl.col("size")).otherwise(0.0).sum().alias("trade_volume_above_mid"),
        pl.when(pl.col("price") < pl.col("mid")).then(pl.col("size")).otherwise(0.0).sum().alias("trade_volume_below_mid"),
        pl.col("price").first().alias("trade_price_first"),
        pl.col("price").last().alias("trade_price_last"),
        pl.col("price").min().alias("trade_price_min"),
        pl.col("price").max().alias("trade_price_max"),
        pl.col("trade_price_vs_mid_bps").last().alias("trade_price_vs_mid_last_bps"),
        pl.col("trade_price_vs_mid_bps").min().alias("trade_price_vs_mid_min_bps"),
        pl.col("trade_price_vs_mid_bps").max().alias("trade_price_vs_mid_max_bps"),
        pl.col("size").max().alias("largest_trade_size"),
        pl.col("trade_pos").min().alias("first_trade_pos"),
        pl.col("trade_pos").max().alias("last_trade_pos"),
    ).with_columns(
        (
            pl.col("signed_trade_volume")
            / pl.col("trade_volume").clip(1.0, None)
        ).alias("trade_imbalance")
    )


def fill_sparse_defaults(frame: pl.DataFrame) -> pl.DataFrame:
    for column in ONE_SECOND_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
    return frame.with_columns(
        pl.col("quote_update_count").fill_null(0).cast(pl.Float64),
        pl.col("trade_count").fill_null(0).cast(pl.Float64),
        pl.col("trade_volume").fill_null(0.0),
        pl.col("dollar_volume").fill_null(0.0),
        pl.col("signed_trade_volume").fill_null(0.0),
        pl.col("trade_imbalance").fill_null(0.0),
        pl.col("trade_volume_at_ask").fill_null(0.0),
        pl.col("trade_volume_at_bid").fill_null(0.0),
        pl.col("trade_volume_inside_spread").fill_null(0.0),
        pl.col("trade_volume_above_mid").fill_null(0.0),
        pl.col("trade_volume_below_mid").fill_null(0.0),
        pl.col("largest_trade_size").fill_null(0.0),
        pl.col("first_trade_pos").fill_null(-1.0),
        pl.col("last_trade_pos").fill_null(-1.0),
        pl.col("first_quote_pos").fill_null(-1.0),
        pl.col("last_quote_pos").fill_null(-1.0),
    )


class HybridMicrostructureDataset(IterableDataset):
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
            batch = BatchBuilder(
                batch_size=self.batch_size,
                one_second_context=self.config.one_second_context,
                ten_second_context=self.config.ten_second_context,
                one_second_feature_count=len(ONE_SECOND_FEATURE_COLUMNS),
                ten_second_feature_count=len(TEN_SECOND_FEATURE_COLUMNS),
                horizon_steps=self.config.horizon_steps,
                target_bit_count=target_bit_count(self.config),
            )

            for session_index, session in enumerate(sessions, start=1):
                print(LOG_RULE, flush=True)
                print(
                    f"*** {self.mode.upper()} SESSION START {session} "
                    f"| epoch {epoch + 1}/{self.epochs} | worker {worker_id + 1}/{worker_count} "
                    f"| session {session_index}/{len(sessions)}",
                    flush=True,
                )
                sparse = load_or_build_session_snapshots(self.config, session, self.tickers)
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
                        batch.add(arrays, int(origin), self.config, ticker=ticker)
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
                                f"*** {self.mode.upper()} SESSION END   {session} "
                                f"| windows={session_windows:,} | batches={session_batches:,} | max_windows_reached",
                                flush=True,
                            )
                            print(LOG_RULE, flush=True)
                            return
                if len(batch) > 0 and self.mode != "train":
                    yield batch.as_torch()
                    session_batches += 1
                    batch = batch.empty_like()
                print(
                    f"*** {self.mode.upper()} SESSION END   {session} "
                    f"| windows={session_windows:,} | batches={session_batches:,}",
                    flush=True,
                )
                print(LOG_RULE, flush=True)
                del sparse
                gc.collect()
            if len(batch) > 0:
                yield batch.as_torch()


class BatchBuilder:
    def __init__(
        self,
        *,
        batch_size: int,
        one_second_context: int,
        ten_second_context: int,
        one_second_feature_count: int,
        ten_second_feature_count: int,
        horizon_steps: int,
        target_bit_count: int,
    ) -> None:
        self.one_second_values = np.empty((batch_size, one_second_context, one_second_feature_count), dtype=np.float32)
        self.ten_second_values = np.empty((batch_size, ten_second_context, ten_second_feature_count), dtype=np.float32)
        self.targets = np.empty((batch_size, horizon_steps, 1, target_bit_count), dtype=np.float32)
        self.target_bps = np.empty((batch_size, horizon_steps, 1), dtype=np.float32)
        self.current_mid = np.empty((batch_size,), dtype=np.float32)
        self.last_mid_return_bps = np.empty((batch_size,), dtype=np.float32)
        self.origin_timestamp_ns = np.empty((batch_size,), dtype=np.int64)
        self.tickers: list[str] = [""] * batch_size
        self.count = 0

    @property
    def full(self) -> bool:
        return self.count >= self.one_second_values.shape[0]

    def __len__(self) -> int:
        return self.count

    def empty_like(self) -> "BatchBuilder":
        return BatchBuilder(
            batch_size=self.one_second_values.shape[0],
            one_second_context=self.one_second_values.shape[1],
            ten_second_context=self.ten_second_values.shape[1],
            one_second_feature_count=self.one_second_values.shape[2],
            ten_second_feature_count=self.ten_second_values.shape[2],
            horizon_steps=self.targets.shape[1],
            target_bit_count=self.targets.shape[3],
        )

    def add(self, arrays: dict[str, np.ndarray], origin: int, config: DataConfig, *, ticker: str) -> None:
        one_start = origin - config.one_second_context + 1
        one_end = origin + 1
        ten_origin = int(arrays["origin_to_ten_index"][origin])
        ten_start = ten_origin - config.ten_second_context + 1
        ten_end = ten_origin + 1
        current_mid = float(arrays["mid_last"][origin])
        previous_mid = float(arrays["mid_last"][max(0, origin - 1)])
        horizon_offsets = np.arange(1, config.horizon_steps + 1, dtype=np.int64) * int(config.horizon_seconds)
        future_indices = origin + horizon_offsets
        future_mid = arrays["mid_last"][future_indices]
        target_bps = log_return_bps(future_mid.reshape(-1, 1), current_mid).astype(np.float32)

        self.one_second_values[self.count] = normalize_feature_window(
            arrays["one_second_features"][one_start:one_end],
            ONE_SECOND_FEATURE_COLUMNS,
            ONE_SECOND_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.ten_second_values[self.count] = normalize_feature_window(
            arrays["ten_second_features"][ten_start:ten_end],
            TEN_SECOND_FEATURE_COLUMNS,
            TEN_SECOND_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.targets[self.count] = encode_binary_magnitude_targets(
            target_bps,
            bits=config.binary_magnitude_bits,
        )
        self.target_bps[self.count] = target_bps
        self.current_mid[self.count] = current_mid
        self.last_mid_return_bps[self.count] = float(log_return_bps(current_mid, previous_mid))
        self.origin_timestamp_ns[self.count] = int(arrays["seconds"][origin]) * NANOSECONDS_PER_SECOND
        self.tickers[self.count] = ticker
        self.count += 1

    def as_torch(self) -> dict[str, Any]:
        if torch is None:
            raise RuntimeError("PyTorch is required to materialize training batches.")
        rows = slice(0, self.count)
        return {
            "one_second_values": torch.from_numpy(self.one_second_values[rows].copy()),
            "ten_second_values": torch.from_numpy(self.ten_second_values[rows].copy()),
            "targets": torch.from_numpy(self.targets[rows].copy()),
            "target_bps": torch.from_numpy(self.target_bps[rows].copy()),
            "current_mid": torch.from_numpy(self.current_mid[rows].copy()),
            "last_close_return_bps": torch.from_numpy(self.last_mid_return_bps[rows].copy()),
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
    ranges = ticker_ranges(frame)
    if shuffle and rng is not None and len(ranges) > 1:
        order = np.arange(len(ranges))
        rng.shuffle(order)
        ranges = [ranges[int(index)] for index in order]
    for ticker, start, length in ranges:
        yield ticker, frame.slice(start, length)


def ticker_ranges(frame: pl.DataFrame) -> list[tuple[str, int, int]]:
    ticker_values = frame.get_column("ticker").to_numpy()
    ranges: list[tuple[str, int, int]] = []
    if len(ticker_values) == 0:
        return ranges
    start = 0
    current = str(ticker_values[0])
    for index in range(1, len(ticker_values)):
        value = str(ticker_values[index])
        if value != current:
            ranges.append((current, start, index - start))
            start = index
            current = value
    ranges.append((current, start, len(ticker_values) - start))
    return ranges


def ticker_arrays(frame: pl.DataFrame, config: DataConfig) -> dict[str, np.ndarray] | None:
    if frame.is_empty():
        return None
    dense = dense_ticker_frame(frame)
    if dense.height < config.one_second_context + config.horizon_steps * config.horizon_seconds:
        return None
    one_second_features = dense.select(list(ONE_SECOND_FEATURE_COLUMNS)).to_numpy().astype(np.float32)
    seconds = dense.get_column("second").to_numpy().astype(np.int64)
    mid_last = dense.get_column("mid_last").to_numpy().astype(np.float32)
    ten_seconds, ten_second_features = build_ten_second_features(dense)
    origin_to_ten_index = np.searchsorted(ten_seconds, seconds, side="right") - 1
    return {
        "seconds": seconds,
        "mid_last": mid_last,
        "one_second_features": one_second_features,
        "ten_second_seconds": ten_seconds,
        "ten_second_features": ten_second_features,
        "origin_to_ten_index": origin_to_ten_index.astype(np.int64),
    }


def dense_ticker_frame(frame: pl.DataFrame) -> pl.DataFrame:
    min_second = int(frame.get_column("second").min())
    max_second = int(frame.get_column("second").max())
    grid = pl.DataFrame({"second": np.arange(min_second, max_second + 1, dtype=np.int64)})
    joined = grid.join(frame.drop("ticker", strict=False), on="second", how="left")

    quote_state_cols = [
        "bid_first",
        "bid_last",
        "ask_first",
        "ask_last",
        "mid_first",
        "mid_last",
        "mid_min",
        "mid_max",
        "spread_first_bps",
        "spread_last_bps",
        "spread_min_bps",
        "spread_max_bps",
        "bid_size_first",
        "bid_size_last",
        "bid_size_min",
        "bid_size_max",
        "ask_size_first",
        "ask_size_last",
        "ask_size_min",
        "ask_size_max",
        "quote_imbalance_first",
        "quote_imbalance_last",
        "quote_imbalance_min",
        "quote_imbalance_max",
    ]
    joined = joined.with_columns([pl.col(column).forward_fill() for column in quote_state_cols])
    joined = joined.filter(pl.col("mid_last").is_not_null() & (pl.col("mid_last") > 0.0))
    joined = joined.with_columns(
        pl.col("quote_update_count").fill_null(0.0),
        pl.col("trade_count").fill_null(0.0),
        pl.col("trade_volume").fill_null(0.0),
        pl.col("dollar_volume").fill_null(0.0),
        pl.col("signed_trade_volume").fill_null(0.0),
        pl.col("trade_imbalance").fill_null(0.0),
        pl.col("trade_volume_at_ask").fill_null(0.0),
        pl.col("trade_volume_at_bid").fill_null(0.0),
        pl.col("trade_volume_inside_spread").fill_null(0.0),
        pl.col("trade_volume_above_mid").fill_null(0.0),
        pl.col("trade_volume_below_mid").fill_null(0.0),
        pl.col("trade_price_first").fill_null(pl.col("mid_last")),
        pl.col("trade_price_last").fill_null(pl.col("mid_last")),
        pl.col("trade_price_min").fill_null(pl.col("mid_last")),
        pl.col("trade_price_max").fill_null(pl.col("mid_last")),
        pl.col("trade_price_vs_mid_last_bps").fill_null(0.0),
        pl.col("trade_price_vs_mid_min_bps").fill_null(0.0),
        pl.col("trade_price_vs_mid_max_bps").fill_null(0.0),
        pl.col("largest_trade_size").fill_null(0.0),
        pl.col("first_trade_pos").fill_null(-1.0),
        pl.col("last_trade_pos").fill_null(-1.0),
        pl.col("first_quote_pos").fill_null(-1.0),
        pl.col("last_quote_pos").fill_null(-1.0),
    )
    has_trade = pl.col("trade_count") > 0.0
    has_quote = pl.col("quote_update_count") > 0.0
    joined = joined.with_columns(
        has_trade.cast(pl.Float64).alias("has_trade"),
        has_quote.cast(pl.Float64).alias("has_quote_update"),
        ((pl.col("first_quote_pos") >= 0.0) & (pl.col("first_trade_pos") >= 0.0) & (pl.col("first_quote_pos") < pl.col("first_trade_pos")))
        .cast(pl.Float64)
        .alias("quote_before_trade"),
    )
    joined = add_age_features(joined)
    seconds = joined.get_column("second").to_numpy().astype(np.float64)
    seconds_of_day = np.mod(seconds, 86400.0)
    hour_angle = 2.0 * np.pi * seconds_of_day / 86400.0
    return joined.with_columns(
        pl.Series("hour_sin", np.sin(hour_angle).astype(np.float32)),
        pl.Series("hour_cos", np.cos(hour_angle).astype(np.float32)),
    ).select(["second", *ONE_SECOND_FEATURE_COLUMNS])


def add_age_features(frame: pl.DataFrame) -> pl.DataFrame:
    seconds = frame.get_column("second").to_numpy().astype(np.int64)
    has_trade = frame.get_column("has_trade").to_numpy() > 0.0
    has_quote = frame.get_column("has_quote_update").to_numpy() > 0.0
    seconds_since_trade = np.full(len(seconds), 1e6, dtype=np.float32)
    seconds_since_quote = np.full(len(seconds), 1e6, dtype=np.float32)
    last_trade_second: int | None = None
    last_quote_second: int | None = None
    for index, second in enumerate(seconds):
        if has_trade[index]:
            last_trade_second = int(second)
        if has_quote[index]:
            last_quote_second = int(second)
        if last_trade_second is not None:
            seconds_since_trade[index] = float(second - last_trade_second)
        if last_quote_second is not None:
            seconds_since_quote[index] = float(second - last_quote_second)
    return frame.with_columns(
        pl.Series("seconds_since_trade", np.clip(seconds_since_trade, 0.0, 3600.0)),
        pl.Series("seconds_since_quote", np.clip(seconds_since_quote, 0.0, 3600.0)),
    )


def build_ten_second_features(dense: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    one = dense.select(list(ONE_SECOND_FEATURE_COLUMNS)).to_numpy().astype(np.float32)
    seconds = dense.get_column("second").to_numpy().astype(np.int64)
    first_bucket = int(math.floor(seconds[0] / 10.0) * 10)
    last_bucket = int(math.floor(seconds[-1] / 10.0) * 10)
    bucket_starts = np.arange(first_bucket, last_bucket + 1, 10, dtype=np.int64)
    bucket_ends = bucket_starts + 9
    feature_index = {name: idx for idx, name in enumerate(ONE_SECOND_FEATURE_COLUMNS)}
    rows = np.zeros((len(bucket_starts), len(TEN_SECOND_FEATURE_COLUMNS)), dtype=np.float32)
    second_to_index = {int(second): idx for idx, second in enumerate(seconds)}

    for row_idx, start_second in enumerate(bucket_starts):
        indices = [second_to_index.get(int(start_second + offset)) for offset in range(10)]
        valid_indices = [idx for idx in indices if idx is not None]
        if not valid_indices:
            continue
        chunk = one[valid_indices]
        base = {
            "mid_first": chunk[0, feature_index["mid_last"]],
            "mid_last": chunk[-1, feature_index["mid_last"]],
            "mid_min": np.min(chunk[:, feature_index["mid_min"]]),
            "mid_max": np.max(chunk[:, feature_index["mid_max"]]),
            "spread_first_bps": chunk[0, feature_index["spread_last_bps"]],
            "spread_last_bps": chunk[-1, feature_index["spread_last_bps"]],
            "spread_min_bps": np.min(chunk[:, feature_index["spread_min_bps"]]),
            "spread_max_bps": np.max(chunk[:, feature_index["spread_max_bps"]]),
            "bid_size_first": chunk[0, feature_index["bid_size_last"]],
            "bid_size_last": chunk[-1, feature_index["bid_size_last"]],
            "ask_size_first": chunk[0, feature_index["ask_size_last"]],
            "ask_size_last": chunk[-1, feature_index["ask_size_last"]],
            "quote_imbalance_first": chunk[0, feature_index["quote_imbalance_last"]],
            "quote_imbalance_last": chunk[-1, feature_index["quote_imbalance_last"]],
            "quote_update_count": np.sum(chunk[:, feature_index["quote_update_count"]]),
            "trade_count": np.sum(chunk[:, feature_index["trade_count"]]),
            "trade_volume": np.sum(chunk[:, feature_index["trade_volume"]]),
            "signed_trade_volume": np.sum(chunk[:, feature_index["signed_trade_volume"]]),
            "first_trade_pos": first_event_pos(chunk, feature_index["first_trade_pos"], valid_indices, indices),
            "last_trade_pos": last_event_pos(chunk, feature_index["last_trade_pos"], valid_indices, indices),
            "first_quote_pos": first_event_pos(chunk, feature_index["first_quote_pos"], valid_indices, indices),
            "last_quote_pos": last_event_pos(chunk, feature_index["last_quote_pos"], valid_indices, indices),
            "seconds_since_trade": chunk[-1, feature_index["seconds_since_trade"]],
            "seconds_since_quote": chunk[-1, feature_index["seconds_since_quote"]],
        }
        trade_volume = float(base["trade_volume"])
        base["trade_imbalance"] = float(base["signed_trade_volume"]) / max(1.0, trade_volume)
        for column, value in base.items():
            rows[row_idx, TEN_SECOND_FEATURE_COLUMNS.index(column)] = float(value)

        for slot, dense_index in enumerate(indices):
            if dense_index is None:
                continue
            source = one[dense_index]
            slot_values = {
                f"slot_{slot}_trade_count": source[feature_index["trade_count"]],
                f"slot_{slot}_signed_trade_volume": source[feature_index["signed_trade_volume"]],
                f"slot_{slot}_quote_update_count": source[feature_index["quote_update_count"]],
                f"slot_{slot}_mid_last": source[feature_index["mid_last"]],
                f"slot_{slot}_spread_last_bps": source[feature_index["spread_last_bps"]],
                f"slot_{slot}_quote_imbalance_last": source[feature_index["quote_imbalance_last"]],
            }
            for column, value in slot_values.items():
                rows[row_idx, TEN_SECOND_FEATURE_COLUMNS.index(column)] = float(value)
    return bucket_ends, rows


def first_event_pos(chunk: np.ndarray, column_idx: int, valid_indices: list[int], all_indices: list[int | None]) -> float:
    for slot, dense_index in enumerate(all_indices):
        if dense_index is None:
            continue
        local = valid_indices.index(dense_index)
        pos = float(chunk[local, column_idx])
        if pos >= 0.0:
            return (slot + pos) / 10.0
    return -1.0


def last_event_pos(chunk: np.ndarray, column_idx: int, valid_indices: list[int], all_indices: list[int | None]) -> float:
    for slot in range(len(all_indices) - 1, -1, -1):
        dense_index = all_indices[slot]
        if dense_index is None:
            continue
        local = valid_indices.index(dense_index)
        pos = float(chunk[local, column_idx])
        if pos >= 0.0:
            return (slot + pos) / 10.0
    return -1.0


def valid_origins(arrays: dict[str, np.ndarray], config: DataConfig) -> np.ndarray:
    seconds = arrays["seconds"]
    mid = arrays["mid_last"]
    ten_map = arrays["origin_to_ten_index"]
    max_future = config.horizon_steps * config.horizon_seconds
    earliest = max(config.one_second_context - 1, 0)
    latest = len(seconds) - max_future - 1
    if latest < earliest:
        return np.empty((0,), dtype=np.int64)
    candidates = np.arange(earliest, latest + 1, max(1, config.origin_stride_seconds), dtype=np.int64)
    if candidates.size == 0:
        return candidates
    future_offsets = np.arange(1, config.horizon_steps + 1, dtype=np.int64) * int(config.horizon_seconds)
    future_indices = candidates[:, None] + future_offsets.reshape(1, -1)
    contiguous_target = seconds[future_indices] == (seconds[candidates].reshape(-1, 1) + future_offsets.reshape(1, -1))
    one_context_contiguous = seconds[candidates] - seconds[candidates - config.one_second_context + 1] == config.one_second_context - 1
    ten_ok = ten_map[candidates] >= config.ten_second_context - 1
    valid_mid = (mid[candidates] > 0.0) & np.all(mid[future_indices] > 0.0, axis=1)
    mask = np.all(contiguous_target, axis=1) & one_context_contiguous & ten_ok & valid_mid
    return candidates[mask]


def normalize_feature_window(
    window: np.ndarray,
    feature_columns: tuple[str, ...],
    price_columns: set[str],
    *,
    current_mid: float,
) -> np.ndarray:
    values = np.asarray(window, dtype=np.float32).copy()
    current_mid_safe = max(float(current_mid), 1e-6)
    for index, column in enumerate(feature_columns):
        column_values = values[:, index]
        if column in price_columns:
            safe = np.maximum(column_values, 1e-6)
            values[:, index] = np.log(safe / current_mid_safe) * 10000.0
        elif column in LOG_COLUMNS or column.endswith("_trade_count") or column.endswith("_quote_update_count"):
            values[:, index] = np.log1p(np.maximum(column_values, 0.0))
    mean = values.mean(axis=0, keepdims=True)
    std = values.std(axis=0, keepdims=True)
    std = np.where(std > 1e-6, std, 1.0)
    values = (values - mean) / std
    return np.nan_to_num(values, nan=0.0, posinf=10.0, neginf=-10.0).astype(np.float32)


def target_bit_count(config: DataConfig) -> int:
    if config.target_mode == "binary_magnitude_bps":
        return 1 + int(config.binary_magnitude_bits)
    raise ValueError(f"Unsupported target mode: {config.target_mode}")


def collect_lazy(frame: pl.LazyFrame) -> pl.DataFrame:
    try:
        return frame.collect(engine="streaming")
    except TypeError:
        try:
            return frame.collect(streaming=True)
        except TypeError:
            return frame.collect()


def count_coverage(
    *,
    config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    batch_size: int,
) -> SessionCoverage:
    coverage = SessionCoverage(sessions=len(sessions))
    for index, session in enumerate(sessions, start=1):
        sparse = load_or_build_session_snapshots(config, session, tickers)
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
