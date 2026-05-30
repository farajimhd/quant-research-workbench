from __future__ import annotations

import math
import gc
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import polars as pl

try:
    import torch
    from torch.utils.data import IterableDataset
except ModuleNotFoundError:
    torch = None

    class IterableDataset:  # type: ignore[no-redef]
        pass

from research.inhouse_transformer.v13.config import DataConfig
from src.data_provider.store import existing_dates, partition_path


SOURCE_COLUMNS = (
    "ticker",
    "session_date",
    "bar_time_market",
    "minute_of_day",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
    "spread_bps",
    "quote_bid_size",
    "quote_ask_size",
    "quote_valid_ratio",
    "quoted_share_depth",
)

MULTISCALE_FEATURE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
    "spread_bps",
    "quote_bid_size",
    "quote_ask_size",
    "quoted_share_depth",
    "quote_imbalance",
    "quote_valid_ratio",
    "range_bps",
    "body_bps",
    "available",
)

ANCHOR_VALUE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
    "range_bps",
    "body_bps",
    "age_days",
    "anchor_type",
    "available",
)

ANCHOR_NAMES = (
    "same_minute_d1",
    "same_minute_d2",
    "same_minute_d3",
    "same_minute_d4",
    "same_minute_d5",
    "same_minute_d6",
    "same_weekday_minute",
    "previous_regular_close",
    "previous_day_open",
    "previous_day_high",
    "previous_day_low",
    "previous_day_close",
    "previous_day_volume",
    "previous_day_range",
    "previous_week_high",
    "previous_week_low",
    "previous_week_close",
    "previous_week_volume",
    "previous_week_range",
)

RELATIVE_TIME_FEATURE_COLUMNS = (
    "age_minutes_from_t_scaled",
    "age_sessions_from_t_scaled",
    "bucket_duration_minutes_scaled",
    "is_same_session",
    "is_previous_session",
    "is_same_weekday",
    "is_anchor_summary",
)

LOG_RULE = "*" * 96


@dataclass(slots=True)
class SessionCoverage:
    sessions: int = 0
    sessions_with_windows: int = 0
    windows: int = 0
    batches: int = 0


def parse_ticker_list(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def available_sessions(processed_root: Path, start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    sessions = [
        session
        for session in existing_dates(processed_root, "bars", "1m")
        if start.isoformat() <= session <= end.isoformat()
    ]
    if not sessions:
        raise SystemExit(f"No provider 1m bars found between {start} and {end} under {processed_root}.")
    return sessions


def resolve_end_date(processed_root: Path, requested_end: str) -> str:
    if requested_end:
        return requested_end
    sessions = existing_dates(processed_root, "bars", "1m")
    if not sessions:
        raise SystemExit(f"No provider 1m bars found under {processed_root}.")
    return sessions[-1]


def select_top_tickers(processed_root: Path, sessions: list[str], max_tickers: int) -> tuple[str, ...]:
    if max_tickers <= 0:
        return ()
    paths = [partition_path(processed_root, "bars", "1m", session) for session in sessions]
    scan = pl.scan_parquet([str(path) for path in paths], missing_columns="insert", extra_columns="ignore")
    ranking = (
        scan.select("ticker", "close", "volume")
        .with_columns(
            (
                pl.col("close").cast(pl.Float64, strict=False).fill_null(0.0)
                * pl.col("volume").cast(pl.Float64, strict=False).fill_null(0.0)
            ).alias("_dollar_volume")
        )
        .group_by("ticker")
        .agg(pl.sum("_dollar_volume").alias("_dollar_volume"))
        .sort("_dollar_volume", descending=True)
        .head(max_tickers)
        .pipe(collect_lazy)
    )
    return tuple(str(value) for value in ranking.get_column("ticker").to_list())


def load_session_frame(config: DataConfig, session: str, tickers: tuple[str, ...]) -> pl.DataFrame:
    path = partition_path(config.processed_root, "bars", config.timeframe, session)
    scan = pl.scan_parquet(str(path), missing_columns="insert", extra_columns="ignore")
    names = set(scan.collect_schema().names())
    required = {"ticker", "session_date", "bar_time_market", "minute_of_day", "open", "high", "low", "close"}
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"Provider bars are missing required columns: {missing}")

    scan = scan.select([column for column in SOURCE_COLUMNS if column in names])
    if tickers:
        scan = scan.filter(pl.col("ticker").is_in(list(tickers)))
    if config.session_scope == "regular":
        scan = scan.filter((pl.col("minute_of_day") >= 9 * 60 + 30) & (pl.col("minute_of_day") < 16 * 60))

    optional_exprs = []
    if "volume" not in names:
        optional_exprs.append(pl.lit(0.0, dtype=pl.Float32).alias("volume"))
    if "transactions" not in names:
        optional_exprs.append(pl.lit(0.0, dtype=pl.Float32).alias("transactions"))
    for optional_column in ("spread_bps", "quote_bid_size", "quote_ask_size", "quote_valid_ratio", "quoted_share_depth"):
        if optional_column not in names:
            optional_exprs.append(pl.lit(0.0, dtype=pl.Float32).alias(optional_column))
    if optional_exprs:
        scan = scan.with_columns(optional_exprs)

    return (
        scan.with_columns(
            pl.col("open").cast(pl.Float32, strict=False),
            pl.col("high").cast(pl.Float32, strict=False),
            pl.col("low").cast(pl.Float32, strict=False),
            pl.col("close").cast(pl.Float32, strict=False),
            pl.col("volume").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("transactions").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("spread_bps").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("quote_bid_size").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("quote_ask_size").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("quote_valid_ratio").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("quoted_share_depth").cast(pl.Float32, strict=False).fill_null(0.0),
            pl.col("minute_of_day").cast(pl.Int32, strict=False),
        )
        .filter(
            (pl.col("open") > 0.0)
            & (pl.col("high") > 0.0)
            & (pl.col("low") > 0.0)
            & (pl.col("close") > 0.0)
            & pl.col("minute_of_day").is_not_null()
        )
        .sort(["ticker", "bar_time_market"])
        .pipe(collect_lazy)
    )


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
    max_batches_per_session: int,
) -> SessionCoverage:
    coverage = SessionCoverage(sessions=len(sessions))
    carryover: dict[str, pl.DataFrame] = {}
    for index, session in enumerate(sessions, start=1):
        frame = load_session_frame(config, session, tickers)
        session_windows = 0
        if not frame.is_empty():
            for ticker, ticker_frame in iter_ticker_frames(frame):
                combined = combine_carryover(carryover.get(ticker), ticker_frame, config)
                window_count = count_ticker_windows(combined, str(ticker_frame["session_date"][0]), config)
                session_windows += window_count
                carryover[ticker] = tail_carryover(combined, config)
        session_batches = math.ceil(session_windows / batch_size) if session_windows else 0
        if max_batches_per_session > 0:
            session_batches = min(session_batches, max_batches_per_session)
            session_windows = min(session_windows, session_batches * batch_size)
        coverage.windows += session_windows
        coverage.batches += session_batches
        if session_windows:
            coverage.sessions_with_windows += 1
        print(
            f"Coverage count {session} ({index}/{len(sessions)}): "
            f"windows={session_windows:,} cumulative_windows={coverage.windows:,}",
            flush=True,
        )
    return coverage


class RollingBarWindowDataset(IterableDataset):
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
        max_batches_per_session: int = 0,
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
        self.max_batches_per_session = max_batches_per_session
        self.shuffle = shuffle

    def __iter__(self) -> Iterator[dict[str, Any]]:
        rng = np.random.default_rng(self.seed)
        emitted_windows = 0
        for epoch in range(self.epochs):
            sessions = list(self.sessions)
            if self.shuffle and not self.config.carry_context_across_session:
                rng.shuffle(sessions)
            carryover: dict[str, pl.DataFrame] = load_initial_carryover(self.config, sessions, self.tickers)
            batch = BatchBuilder(
                batch_size=self.batch_size,
                context_length=self.config.context_length,
                feature_count=len(self.config.input_feature_columns),
                time_feature_count=len(self.config.time_feature_columns),
                five_min_context_length=multiscale_token_count(
                    self.config.five_min_lookback_minutes,
                    self.config.five_min_bucket_minutes,
                ),
                five_min_feature_count=len(MULTISCALE_FEATURE_COLUMNS),
                thirty_min_context_length=multiscale_token_count(
                    self.config.thirty_min_lookback_minutes,
                    self.config.thirty_min_bucket_minutes,
                ),
                thirty_min_feature_count=len(MULTISCALE_FEATURE_COLUMNS),
                anchor_context_length=len(ANCHOR_NAMES),
                anchor_feature_count=len(ANCHOR_VALUE_COLUMNS),
                horizon=self.config.horizon,
                target_count=len(self.config.target_columns),
            )
            for session_index, session in enumerate(sessions, start=1):
                print(LOG_RULE, flush=True)
                print(
                    f"*** {self.mode.upper()} SESSION START {session} "
                    f"| epoch {epoch + 1}/{self.epochs} | session {session_index}/{len(sessions)}",
                    flush=True,
                )
                session_batches = 0
                session_windows = 0
                frame = load_session_frame(self.config, session, self.tickers)
                ticker_frames = iter_ticker_frames(frame, rng=rng, shuffle=self.shuffle) if not frame.is_empty() else iter(())
                for ticker, ticker_frame in ticker_frames:
                    combined = combine_carryover(carryover.get(ticker), ticker_frame, self.config)
                    arrays = ticker_arrays(combined, self.config)
                    current_session = str(ticker_frame["session_date"][0])
                    origins = valid_origins(arrays, current_session, self.config)
                    if self.shuffle and origins.size:
                        rng.shuffle(origins)
                    for origin in origins:
                        batch.add(arrays, int(origin), self.config, ticker=str(ticker))
                        session_windows += 1
                        emitted_windows += 1
                        if batch.full:
                            yield batch.as_torch()
                            session_batches += 1
                            batch = batch.empty_like()
                            if 0 < self.max_batches_per_session <= session_batches:
                                break
                        if 0 < self.max_windows <= emitted_windows:
                            if len(batch) > 0:
                                yield batch.as_torch()
                                session_batches += 1
                            print(
                                f"*** {self.mode.upper()} SESSION END   {session} "
                                f"| windows={session_windows:,} | batches={session_batches:,} | max_windows_reached",
                                flush=True,
                            )
                            print(LOG_RULE, flush=True)
                            return
                    carryover[ticker] = tail_carryover(combined, self.config)
                    if 0 < self.max_batches_per_session <= session_batches:
                        break
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
                del frame
                gc.collect()
            if len(batch) > 0:
                yield batch.as_torch()


class BatchBuilder:
    def __init__(
        self,
        *,
        batch_size: int,
        context_length: int,
        feature_count: int,
        time_feature_count: int,
        five_min_context_length: int,
        five_min_feature_count: int,
        thirty_min_context_length: int,
        thirty_min_feature_count: int,
        anchor_context_length: int,
        anchor_feature_count: int,
        horizon: int,
        target_count: int,
    ) -> None:
        self.values = np.empty((batch_size, context_length, feature_count), dtype=np.float32)
        self.time_features = np.empty((batch_size, context_length, time_feature_count), dtype=np.float32)
        self.five_min_values = np.empty((batch_size, five_min_context_length, five_min_feature_count), dtype=np.float32)
        self.five_min_time_features = np.empty((batch_size, five_min_context_length, time_feature_count), dtype=np.float32)
        self.thirty_min_values = np.empty(
            (batch_size, thirty_min_context_length, thirty_min_feature_count),
            dtype=np.float32,
        )
        self.thirty_min_time_features = np.empty(
            (batch_size, thirty_min_context_length, time_feature_count),
            dtype=np.float32,
        )
        self.anchor_values = np.empty((batch_size, anchor_context_length, anchor_feature_count), dtype=np.float32)
        self.anchor_time_features = np.empty((batch_size, anchor_context_length, time_feature_count), dtype=np.float32)
        self.targets = np.empty((batch_size, horizon, target_count), dtype=np.float32)
        self.direction = np.empty((batch_size, horizon), dtype=np.float32)
        self.current_close = np.empty((batch_size,), dtype=np.float32)
        self.target_center = np.empty((batch_size,), dtype=np.float32)
        self.target_scale = np.empty((batch_size,), dtype=np.float32)
        self.last_close_return_bps = np.empty((batch_size,), dtype=np.float32)
        self.target_timestamp_ns = np.empty((batch_size,), dtype=np.int64)
        self.tickers: list[str] = [""] * batch_size
        self.count = 0

    @property
    def full(self) -> bool:
        return self.count >= self.values.shape[0]

    def __len__(self) -> int:
        return self.count

    def empty_like(self) -> "BatchBuilder":
        return BatchBuilder(
            batch_size=self.values.shape[0],
            context_length=self.values.shape[1],
            feature_count=self.values.shape[2],
            time_feature_count=self.time_features.shape[2],
            five_min_context_length=self.five_min_values.shape[1],
            five_min_feature_count=self.five_min_values.shape[2],
            thirty_min_context_length=self.thirty_min_values.shape[1],
            thirty_min_feature_count=self.thirty_min_values.shape[2],
            anchor_context_length=self.anchor_values.shape[1],
            anchor_feature_count=self.anchor_values.shape[2],
            horizon=self.targets.shape[1],
            target_count=self.targets.shape[2],
        )

    def add(self, arrays: dict[str, np.ndarray], origin: int, config: DataConfig, *, ticker: str) -> None:
        start = origin - config.context_length + 1
        end = origin + 1
        target_start = origin + 1
        target_end = origin + 1 + config.horizon
        current_close = arrays["close"][origin]
        previous_close = arrays["close"][max(0, origin - 1)]
        last_close_return_bps = float(log_return_bps(current_close, previous_close))
        price_center, price_scale = window_price_center_scale(arrays, start, end, current_close)
        target_prices = np.column_stack(
            [
                arrays[column][target_start:target_end]
                for column in config.target_columns
            ]
        )
        close_index = config.target_columns.index("close")
        if config.target_mode == "actual_price_zscore":
            center, scale = price_center, price_scale
            targets = ((target_prices - center) / scale).astype(np.float32)
        elif config.target_mode == "return_bps":
            center = 0.0
            scale = 1.0
            targets = log_return_bps(target_prices, current_close)
        else:
            raise ValueError(f"Unsupported target_mode: {config.target_mode}")

        self.values[self.count] = normalize_actual_feature_window(
            arrays["features"][start:end],
            config,
            current_close=current_close,
        )
        self.time_features[self.count] = build_relative_time_features(
            arrays,
            np.arange(start, end, dtype=np.int64),
            origin,
            config,
            bucket_minutes=1.0,
        )
        self.five_min_values[self.count], self.five_min_time_features[self.count] = build_multiscale_context(
            arrays,
            origin,
            config,
            lookback_minutes=config.five_min_lookback_minutes,
            bucket_minutes=config.five_min_bucket_minutes,
            current_close=current_close,
        )
        self.thirty_min_values[self.count], self.thirty_min_time_features[self.count] = build_multiscale_context(
            arrays,
            origin,
            config,
            lookback_minutes=config.thirty_min_lookback_minutes,
            bucket_minutes=config.thirty_min_bucket_minutes,
            current_close=current_close,
        )
        self.anchor_values[self.count], self.anchor_time_features[self.count] = build_anchor_context(
            arrays,
            origin,
            config,
            current_close=current_close,
        )
        self.targets[self.count] = targets
        self.direction[self.count] = (target_prices[:, close_index] > current_close).astype(np.float32)
        self.current_close[self.count] = current_close
        self.target_center[self.count] = center
        self.target_scale[self.count] = scale
        self.last_close_return_bps[self.count] = last_close_return_bps
        self.target_timestamp_ns[self.count] = int(arrays["timestamps_ns"][target_start])
        self.tickers[self.count] = ticker
        self.count += 1

    def as_torch(self) -> dict[str, Any]:
        if torch is None:
            raise RuntimeError("PyTorch is required to materialize training batches.")
        rows = slice(0, self.count)
        return {
            "values": torch.from_numpy(self.values[rows].copy()),
            "time_features": torch.from_numpy(self.time_features[rows].copy()),
            "five_min_values": torch.from_numpy(self.five_min_values[rows].copy()),
            "five_min_time_features": torch.from_numpy(self.five_min_time_features[rows].copy()),
            "thirty_min_values": torch.from_numpy(self.thirty_min_values[rows].copy()),
            "thirty_min_time_features": torch.from_numpy(self.thirty_min_time_features[rows].copy()),
            "anchor_values": torch.from_numpy(self.anchor_values[rows].copy()),
            "anchor_time_features": torch.from_numpy(self.anchor_time_features[rows].copy()),
            "targets": torch.from_numpy(self.targets[rows].copy()),
            "direction": torch.from_numpy(self.direction[rows].copy()),
            "current_close": torch.from_numpy(self.current_close[rows].copy()),
            "target_center": torch.from_numpy(self.target_center[rows].copy()),
            "target_scale": torch.from_numpy(self.target_scale[rows].copy()),
            "last_close_return_bps": torch.from_numpy(self.last_close_return_bps[rows].copy()),
            "target_timestamp_ns": torch.from_numpy(self.target_timestamp_ns[rows].copy()),
            "ticker": list(self.tickers[: self.count]),
        }


def iter_ticker_frames(
    frame: pl.DataFrame,
    *,
    rng: np.random.Generator | None = None,
    shuffle: bool = False,
) -> Iterator[tuple[str, pl.DataFrame]]:
    ranges = ticker_ranges(frame)
    if shuffle and rng is not None and len(ranges) > 1:
        order = np.arange(len(ranges))
        rng.shuffle(order)
        ranges = [ranges[int(index)] for index in order]
    for ticker, start, length in ranges:
        yield ticker, frame.slice(start, length)


def ticker_ranges(frame: pl.DataFrame) -> list[tuple[str, int, int]]:
    if frame.is_empty():
        return []
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
    return ranges


def combine_carryover(previous: pl.DataFrame | None, current: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    if previous is None or previous.is_empty() or not config.carry_context_across_session:
        return current
    return pl.concat([previous, current], how="vertical_relaxed").sort("bar_time_market")


def tail_carryover(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    rows = history_tail_rows(config)
    return frame.tail(rows)


def history_tail_rows(config: DataConfig) -> int:
    # Keep enough rows for regular + extended hours, calendar anchors, and sparse higher timeframe buckets.
    minutes = max(config.thirty_min_lookback_minutes, config.five_min_lookback_minutes)
    session_rows = max(1, config.anchor_history_sessions + 1) * 960
    return max(config.context_length + config.horizon, minutes * 2, session_rows)


def load_initial_carryover(
    config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
) -> dict[str, pl.DataFrame]:
    if not sessions or not config.carry_context_across_session or config.anchor_history_sessions <= 0:
        return {}
    all_sessions = existing_dates(config.processed_root, "bars", config.timeframe)
    first_session = sessions[0]
    prior_sessions = [session for session in all_sessions if session < first_session]
    prior_sessions = prior_sessions[-config.anchor_history_sessions :]
    if not prior_sessions:
        return {}

    print(
        f"*** HISTORY BOOTSTRAP for {first_session}: loading prior sessions "
        f"{prior_sessions[0]} -> {prior_sessions[-1]} ({len(prior_sessions)} sessions)",
        flush=True,
    )
    frames = [load_session_frame(config, session, tickers) for session in prior_sessions]
    frames = [frame for frame in frames if not frame.is_empty()]
    if not frames:
        return {}
    history = pl.concat(frames, how="vertical_relaxed").sort(["ticker", "bar_time_market"])
    carryover: dict[str, pl.DataFrame] = {}
    for ticker, ticker_frame in iter_ticker_frames(history):
        carryover[str(ticker)] = tail_carryover(ticker_frame, config)
    return carryover


def count_ticker_windows(frame: pl.DataFrame, current_session: str, config: DataConfig) -> int:
    arrays = ticker_arrays(frame, config)
    return int(valid_origins(arrays, current_session, config).size)


def ticker_arrays(frame: pl.DataFrame, config: DataConfig) -> dict[str, np.ndarray]:
    open_ = column_array(frame, "open")
    high = column_array(frame, "high")
    low = column_array(frame, "low")
    close = column_array(frame, "close")
    volume = nonnegative_array(frame, "volume")
    transactions = nonnegative_array(frame, "transactions")
    spread_bps = np.nan_to_num(column_array(frame, "spread_bps"), nan=0.0, posinf=1000.0, neginf=-1000.0)
    quote_bid_size = nonnegative_array(frame, "quote_bid_size")
    quote_ask_size = nonnegative_array(frame, "quote_ask_size")
    quoted_share_depth = nonnegative_array(frame, "quoted_share_depth")
    quoted_share_depth = np.where(quoted_share_depth > 0.0, quoted_share_depth, quote_bid_size + quote_ask_size)
    quote_size_sum = quote_bid_size + quote_ask_size
    quote_imbalance = np.divide(
        quote_bid_size - quote_ask_size,
        quote_size_sum,
        out=np.zeros_like(quote_size_sum, dtype=np.float32),
        where=quote_size_sum > 0.0,
    )
    quote_valid_ratio = np.clip(nonnegative_array(frame, "quote_valid_ratio"), 0.0, 1.0)
    minute = frame.get_column("minute_of_day").to_numpy().astype(np.float32)
    sessions = frame.get_column("session_date").to_numpy().astype(str)
    timestamps_ns = frame.get_column("bar_time_market").dt.timestamp("ns").to_numpy().astype(np.int64)
    session_ordinals, session_weekdays = session_calendar_arrays(sessions)
    calendar = scaled_calendar_arrays(frame)

    features = np.column_stack(
        [
            open_,
            high,
            low,
            close,
            volume,
            transactions,
            spread_bps,
            quote_bid_size,
            quote_ask_size,
            quoted_share_depth,
            quote_imbalance,
            quote_valid_ratio,
        ]
    ).astype(np.float32)
    features = np.nan_to_num(features, nan=0.0, posinf=1e9, neginf=-1e9)

    gap_seconds = np.zeros_like(close, dtype=np.float32)
    if len(timestamps_ns) > 1:
        gap_seconds[1:] = np.maximum(0.0, (timestamps_ns[1:] - timestamps_ns[:-1]).astype(np.float64) / 1_000_000_000.0)
    is_new_session = np.zeros_like(close, dtype=np.float32)
    if len(sessions) > 1:
        is_new_session[1:] = (sessions[1:] != sessions[:-1]).astype(np.float32)
    regular_position = np.clip((minute - (9 * 60 + 30)) / 390.0, 0.0, 1.0)
    minute_cycle = minute / 1440.0
    time_features = np.column_stack(
        [
            np.sin(2.0 * np.pi * minute_cycle),
            np.cos(2.0 * np.pi * minute_cycle),
            np.sin(2.0 * np.pi * regular_position),
            np.cos(2.0 * np.pi * regular_position),
            (minute < 9 * 60 + 30).astype(np.float32),
            ((minute >= 9 * 60 + 30) & (minute < 16 * 60)).astype(np.float32),
            (minute >= 16 * 60).astype(np.float32),
            is_new_session,
            np.clip(gap_seconds / 60.0, 0.0, 1440.0) / 1440.0,
            calendar["year_scaled"],
            calendar["month_scaled"],
            calendar["day_scaled"],
            calendar["hour_scaled"],
            calendar["minute_scaled"],
            calendar["second_scaled"],
            calendar["microsecond_scaled"],
            calendar["minute_of_day_scaled"],
            calendar["day_of_year_scaled"],
            calendar["day_of_week_scaled"],
        ]
    ).astype(np.float32)

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "transactions": transactions,
        "spread_bps": spread_bps,
        "quote_bid_size": quote_bid_size,
        "quote_ask_size": quote_ask_size,
        "quoted_share_depth": quoted_share_depth,
        "quote_imbalance": quote_imbalance,
        "quote_valid_ratio": quote_valid_ratio,
        "minute_of_day": minute.astype(np.int32),
        "sessions": sessions,
        "session_ordinals": session_ordinals,
        "session_weekdays": session_weekdays,
        "timestamps_ns": timestamps_ns,
        "features": features,
        "time_features": time_features,
    }


def session_calendar_arrays(sessions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    unique_sessions = sorted({str(session) for session in sessions})
    ordinal_by_session = {session: index for index, session in enumerate(unique_sessions)}
    weekday_by_session = {session: date.fromisoformat(session).weekday() for session in unique_sessions}
    ordinals = np.array([ordinal_by_session[str(session)] for session in sessions], dtype=np.int32)
    weekdays = np.array([weekday_by_session[str(session)] for session in sessions], dtype=np.int8)
    return ordinals, weekdays


def scaled_calendar_arrays(frame: pl.DataFrame) -> dict[str, np.ndarray]:
    decoded = frame.select(
        ((pl.col("bar_time_market").dt.year().cast(pl.Float32) - 2000.0) / 100.0).alias("year_scaled"),
        ((pl.col("bar_time_market").dt.month().cast(pl.Float32) - 1.0) / 11.0).alias("month_scaled"),
        ((pl.col("bar_time_market").dt.day().cast(pl.Float32) - 1.0) / 30.0).alias("day_scaled"),
        (pl.col("bar_time_market").dt.hour().cast(pl.Float32) / 23.0).alias("hour_scaled"),
        (pl.col("bar_time_market").dt.minute().cast(pl.Float32) / 59.0).alias("minute_scaled"),
        (pl.col("bar_time_market").dt.second().cast(pl.Float32) / 59.0).alias("second_scaled"),
        (pl.col("bar_time_market").dt.microsecond().cast(pl.Float32) / 999_999.0).alias("microsecond_scaled"),
        (pl.col("minute_of_day").cast(pl.Float32) / 1439.0).alias("minute_of_day_scaled"),
        ((pl.col("bar_time_market").dt.ordinal_day().cast(pl.Float32) - 1.0) / 365.0).alias("day_of_year_scaled"),
        ((pl.col("bar_time_market").dt.weekday().cast(pl.Float32) - 1.0) / 6.0).alias("day_of_week_scaled"),
    )
    return {
        column: np.nan_to_num(decoded.get_column(column).to_numpy().astype(np.float32), nan=0.0)
        for column in decoded.columns
    }


def valid_origins(arrays: dict[str, np.ndarray], current_session: str, config: DataConfig) -> np.ndarray:
    n = arrays["close"].shape[0]
    min_origin = config.context_length - 1
    max_origin = n - config.horizon - 1
    if max_origin < min_origin:
        return np.empty(0, dtype=np.int64)
    origins = np.arange(min_origin, max_origin + 1, dtype=np.int64)
    sessions = arrays["sessions"]
    current_mask = sessions[origins] == current_session
    origins = origins[current_mask]
    if origins.size and not config.allow_target_across_session:
        target_sessions = np.stack([sessions[origins + offset] for offset in range(1, config.horizon + 1)], axis=1)
        origins = origins[np.all(target_sessions == sessions[origins, None], axis=1)]
    return origins


def column_array(frame: pl.DataFrame, column: str) -> np.ndarray:
    return frame.get_column(column).to_numpy().astype(np.float32)


def nonnegative_array(frame: pl.DataFrame, column: str) -> np.ndarray:
    values = column_array(frame, column)
    return np.nan_to_num(np.maximum(values, 0.0), nan=0.0, posinf=0.0, neginf=0.0)


def window_price_center_scale(
    arrays: dict[str, np.ndarray],
    start: int,
    end: int,
    current_close: float,
) -> tuple[float, float]:
    prices = np.column_stack(
        [
            arrays["open"][start:end],
            arrays["high"][start:end],
            arrays["low"][start:end],
            arrays["close"][start:end],
        ]
    ).reshape(-1)
    center = float(np.nanmean(prices))
    if not math.isfinite(center) or center <= 0.0:
        center = max(float(current_close), 1e-6)
    scale = float(np.nanstd(prices))
    scale_floor = max(abs(float(current_close)) * 1e-4, 1e-4)
    if not math.isfinite(scale) or scale < scale_floor:
        scale = scale_floor
    return center, scale


def normalize_actual_feature_window(
    values: np.ndarray,
    config: DataConfig,
    *,
    current_close: float | None = None,
) -> np.ndarray:
    if config.input_normalization != "window_zscore_only":
        raise ValueError(f"Unsupported input_normalization: {config.input_normalization}")
    raw = np.asarray(values, dtype=np.float32).copy()
    if current_close is not None:
        raw = price_inputs_to_origin_return_bps(raw, config, current_close)
    raw = anchored_activity_inputs_to_log_ratio(raw, config)
    mean = np.nanmean(raw, axis=0, dtype=np.float64).astype(np.float32)
    std = np.nanstd(raw, axis=0, dtype=np.float64).astype(np.float32)
    mean = np.nan_to_num(mean, nan=0.0, posinf=0.0, neginf=0.0)
    std = np.nan_to_num(std, nan=1.0, posinf=1.0, neginf=1.0)
    std = np.where(std < 1e-6, 1.0, std)
    normalized = (raw - mean) / std
    return np.nan_to_num(normalized, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def multiscale_token_count(lookback_minutes: int, bucket_minutes: int) -> int:
    if lookback_minutes <= 0 or bucket_minutes <= 0:
        raise ValueError("Multiscale lookback and bucket minutes must be positive.")
    return max(1, int(math.ceil(lookback_minutes / bucket_minutes)))


def build_relative_time_features(
    arrays: dict[str, np.ndarray],
    row_indices: np.ndarray,
    origin: int,
    config: DataConfig,
    *,
    bucket_minutes: float,
    is_anchor_summary: float = 0.0,
) -> np.ndarray:
    rows = np.asarray(row_indices, dtype=np.int64)
    base = arrays["time_features"][rows].astype(np.float32, copy=True)
    feature_count = len(config.time_feature_columns)
    if base.shape[1] < feature_count:
        padded = np.zeros((base.shape[0], feature_count), dtype=np.float32)
        padded[:, : base.shape[1]] = base
        base = padded

    columns = {name: index for index, name in enumerate(config.time_feature_columns)}
    origin_ts = int(arrays["timestamps_ns"][origin])
    row_ts = arrays["timestamps_ns"][rows].astype(np.int64)
    age_minutes = np.maximum(0.0, (origin_ts - row_ts).astype(np.float64) / 60_000_000_000.0)
    age_minute_denominator = max(
        float(config.thirty_min_lookback_minutes),
        float(config.anchor_history_sessions * 1440),
        1.0,
    )

    origin_session_ordinal = int(arrays["session_ordinals"][origin])
    row_session_ordinals = arrays["session_ordinals"][rows].astype(np.int32)
    age_sessions = np.maximum(0, origin_session_ordinal - row_session_ordinals)
    age_session_denominator = max(float(config.anchor_history_sessions), 1.0)

    origin_weekday = int(arrays["session_weekdays"][origin])
    row_weekdays = arrays["session_weekdays"][rows].astype(np.int8)

    set_time_column(base, columns, "age_minutes_from_t_scaled", np.clip(age_minutes / age_minute_denominator, 0.0, 1.0))
    set_time_column(base, columns, "age_sessions_from_t_scaled", np.clip(age_sessions / age_session_denominator, 0.0, 1.0))
    set_time_column(base, columns, "bucket_duration_minutes_scaled", np.full(base.shape[0], min(float(bucket_minutes) / 1440.0, 1.0)))
    set_time_column(base, columns, "is_same_session", (age_sessions == 0).astype(np.float32))
    set_time_column(base, columns, "is_previous_session", (age_sessions == 1).astype(np.float32))
    set_time_column(base, columns, "is_same_weekday", (row_weekdays == origin_weekday).astype(np.float32))
    set_time_column(base, columns, "is_anchor_summary", np.full(base.shape[0], float(is_anchor_summary), dtype=np.float32))
    return np.nan_to_num(base, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def set_time_column(
    values: np.ndarray,
    columns: dict[str, int],
    name: str,
    column_values: np.ndarray,
) -> None:
    index = columns.get(name)
    if index is not None:
        values[:, index] = np.asarray(column_values, dtype=np.float32)


def build_multiscale_context(
    arrays: dict[str, np.ndarray],
    origin: int,
    config: DataConfig,
    *,
    lookback_minutes: int,
    bucket_minutes: int,
    current_close: float,
) -> tuple[np.ndarray, np.ndarray]:
    token_count = multiscale_token_count(lookback_minutes, bucket_minutes)
    values = np.zeros((token_count, len(MULTISCALE_FEATURE_COLUMNS)), dtype=np.float32)
    time_features = np.zeros((token_count, len(config.time_feature_columns)), dtype=np.float32)
    timestamps = arrays["timestamps_ns"]
    end_ts = int(timestamps[origin])
    bucket_ns = int(bucket_minutes * 60 * 1_000_000_000)
    lookback_ns = int(token_count * bucket_ns)
    feature_names = list(MULTISCALE_FEATURE_COLUMNS)

    for token_index in range(token_count):
        bucket_start = end_ts - lookback_ns + token_index * bucket_ns
        bucket_end = bucket_start + bucket_ns
        start = int(np.searchsorted(timestamps, bucket_start, side="right"))
        end = int(np.searchsorted(timestamps, bucket_end, side="right"))
        end = min(end, origin + 1)
        if start >= end:
            continue
        values[token_index] = aggregate_bar_slice(arrays, start, end, feature_names)
        time_features[token_index] = build_relative_time_features(
            arrays,
            np.array([end - 1], dtype=np.int64),
            origin,
            config,
            bucket_minutes=float(bucket_minutes),
        )[0]

    normalized = normalize_multiscale_values(
        values,
        config,
        feature_names,
        current_close=current_close,
        origin_features=arrays["features"][origin],
    )
    return normalized, time_features


def aggregate_bar_slice(
    arrays: dict[str, np.ndarray],
    start: int,
    end: int,
    feature_names: list[str],
) -> np.ndarray:
    row = np.zeros((len(feature_names),), dtype=np.float32)
    open_ = float(arrays["open"][start])
    high = float(np.nanmax(arrays["high"][start:end]))
    low = float(np.nanmin(arrays["low"][start:end]))
    close = float(arrays["close"][end - 1])
    values_by_name = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": float(np.nansum(arrays["volume"][start:end])),
        "transactions": float(np.nansum(arrays["transactions"][start:end])),
        "spread_bps": float(np.nanmean(arrays["spread_bps"][start:end])),
        "quote_bid_size": float(np.nanmean(arrays["quote_bid_size"][start:end])),
        "quote_ask_size": float(np.nanmean(arrays["quote_ask_size"][start:end])),
        "quoted_share_depth": float(np.nanmean(arrays["quoted_share_depth"][start:end])),
        "quote_imbalance": float(np.nanmean(arrays["quote_imbalance"][start:end])),
        "quote_valid_ratio": float(np.nanmean(arrays["quote_valid_ratio"][start:end])),
        "range_bps": float(log_return_bps(max(high, 1e-6), max(low, 1e-6))),
        "body_bps": float(log_return_bps(max(close, 1e-6), max(open_, 1e-6))),
        "available": 1.0,
    }
    for index, name in enumerate(feature_names):
        row[index] = values_by_name.get(name, 0.0)
    return np.nan_to_num(row, nan=0.0, posinf=1e9, neginf=-1e9).astype(np.float32)


def normalize_multiscale_values(
    values: np.ndarray,
    config: DataConfig,
    feature_names: list[str],
    *,
    current_close: float,
    origin_features: np.ndarray,
) -> np.ndarray:
    transformed = values.astype(np.float32).copy()
    available_index = feature_names.index("available") if "available" in feature_names else -1
    available = transformed[:, available_index] > 0.0 if available_index >= 0 else np.ones(transformed.shape[0], dtype=bool)
    for column in ("open", "high", "low", "close"):
        if column in feature_names:
            index = feature_names.index(column)
            transformed[available, index] = log_return_bps(transformed[available, index], current_close).astype(np.float32)
    origin_by_name = {
        name: origin_features[index]
        for index, name in enumerate(config.input_feature_columns)
        if index < origin_features.shape[0]
    }
    for column in ("volume", "transactions", "quote_bid_size", "quote_ask_size", "quoted_share_depth"):
        if column in feature_names:
            index = feature_names.index(column)
            origin_value = float(origin_by_name.get(column, 0.0))
            transformed[available, index] = (
                np.log1p(np.maximum(transformed[available, index], 0.0)) - math.log1p(max(origin_value, 0.0))
            ).astype(np.float32)
    zscore_feature_matrix(transformed, available, skip_names={"available"}, feature_names=feature_names)
    if available_index >= 0:
        transformed[:, available_index] = values[:, available_index]
    return np.nan_to_num(transformed, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def build_anchor_context(
    arrays: dict[str, np.ndarray],
    origin: int,
    config: DataConfig,
    *,
    current_close: float,
) -> tuple[np.ndarray, np.ndarray]:
    values = np.zeros((len(ANCHOR_NAMES), len(ANCHOR_VALUE_COLUMNS)), dtype=np.float32)
    time_features = np.zeros((len(ANCHOR_NAMES), len(config.time_feature_columns)), dtype=np.float32)
    current_session = str(arrays["sessions"][origin])
    current_minute = int(arrays["minute_of_day"][origin])
    prior_sessions = sorted({str(session) for session in arrays["sessions"][:origin] if str(session) < current_session})
    previous_sessions = prior_sessions[-6:]

    for offset, session in enumerate(reversed(previous_sessions), start=1):
        anchor_index = offset - 1
        row_index = find_session_minute_row(arrays, session, current_minute, before_origin=origin)
        if row_index is not None:
            fill_anchor_from_row(values, time_features, arrays, anchor_index, row_index, origin, anchor_index, config)

    same_week_index = find_same_weekday_minute_row(arrays, current_session, current_minute, origin)
    if same_week_index is not None:
        fill_anchor_from_row(values, time_features, arrays, 6, same_week_index, origin, 6, config)

    previous_session = prior_sessions[-1] if prior_sessions else ""
    if previous_session:
        fill_previous_day_anchors(values, time_features, arrays, previous_session, origin, config)
        fill_previous_week_anchors(values, time_features, arrays, prior_sessions[-5:], origin, config)

    normalized = normalize_anchor_values(
        values,
        config,
        current_close=current_close,
        origin_features=arrays["features"][origin],
    )
    return normalized, time_features


def find_session_minute_row(
    arrays: dict[str, np.ndarray],
    session: str,
    minute: int,
    *,
    before_origin: int,
) -> int | None:
    mask = (arrays["sessions"][:before_origin] == session) & (arrays["minute_of_day"][:before_origin] == minute)
    matches = np.flatnonzero(mask)
    if matches.size == 0:
        return None
    return int(matches[-1])


def find_same_weekday_minute_row(
    arrays: dict[str, np.ndarray],
    current_session: str,
    minute: int,
    origin: int,
) -> int | None:
    current_weekday = date.fromisoformat(current_session).weekday()
    prior_sessions = sorted({str(session) for session in arrays["sessions"][:origin] if str(session) < current_session})
    for session in reversed(prior_sessions):
        if date.fromisoformat(session).weekday() != current_weekday:
            continue
        row = find_session_minute_row(arrays, session, minute, before_origin=origin)
        if row is not None:
            return row
    return None


def fill_anchor_from_row(
    values: np.ndarray,
    time_features: np.ndarray,
    arrays: dict[str, np.ndarray],
    anchor_index: int,
    row_index: int,
    origin: int,
    anchor_type: int,
    config: DataConfig,
    *,
    is_anchor_summary: bool = False,
) -> None:
    values[anchor_index] = anchor_row(
        open_=float(arrays["open"][row_index]),
        high=float(arrays["high"][row_index]),
        low=float(arrays["low"][row_index]),
        close=float(arrays["close"][row_index]),
        volume=float(arrays["volume"][row_index]),
        transactions=float(arrays["transactions"][row_index]),
        origin_ts=int(arrays["timestamps_ns"][origin]),
        anchor_ts=int(arrays["timestamps_ns"][row_index]),
        anchor_type=anchor_type,
    )
    time_features[anchor_index] = build_relative_time_features(
        arrays,
        np.array([row_index], dtype=np.int64),
        origin,
        config,
        bucket_minutes=1.0,
        is_anchor_summary=1.0 if is_anchor_summary else 0.0,
    )[0]


def fill_previous_day_anchors(
    values: np.ndarray,
    time_features: np.ndarray,
    arrays: dict[str, np.ndarray],
    session: str,
    origin: int,
    config: DataConfig,
) -> None:
    rows = np.flatnonzero(arrays["sessions"][:origin] == session)
    if rows.size == 0:
        return
    regular_rows = rows[
        (arrays["minute_of_day"][rows] >= 9 * 60 + 30) & (arrays["minute_of_day"][rows] < 16 * 60)
    ]
    close_row = int(regular_rows[-1] if regular_rows.size else rows[-1])
    fill_anchor_from_row(values, time_features, arrays, 7, close_row, origin, 7, config, is_anchor_summary=True)
    summary_rows = rows
    open_row = int(summary_rows[0])
    high_row = int(summary_rows[np.nanargmax(arrays["high"][summary_rows])])
    low_row = int(summary_rows[np.nanargmin(arrays["low"][summary_rows])])
    day_open = float(arrays["open"][open_row])
    day_high = float(arrays["high"][high_row])
    day_low = float(arrays["low"][low_row])
    day_close = float(arrays["close"][int(summary_rows[-1])])
    day_volume = float(np.nansum(arrays["volume"][summary_rows]))
    day_transactions = float(np.nansum(arrays["transactions"][summary_rows]))
    for anchor_index, price_name, price_value, anchor_type in (
        (8, "open", day_open, 8),
        (9, "high", day_high, 9),
        (10, "low", day_low, 10),
        (11, "close", day_close, 11),
        (12, "volume", day_close, 12),
        (13, "range", day_close, 13),
    ):
        values[anchor_index] = anchor_row(
            open_=day_open if price_name == "open" else price_value,
            high=day_high if price_name in {"high", "range"} else price_value,
            low=day_low if price_name in {"low", "range"} else price_value,
            close=day_close if price_name in {"close", "volume", "range"} else price_value,
            volume=day_volume,
            transactions=day_transactions,
            origin_ts=int(arrays["timestamps_ns"][origin]),
            anchor_ts=int(arrays["timestamps_ns"][int(summary_rows[-1])]),
            anchor_type=anchor_type,
        )
        time_features[anchor_index] = build_relative_time_features(
            arrays,
            np.array([int(summary_rows[-1])], dtype=np.int64),
            origin,
            config,
            bucket_minutes=1440.0,
            is_anchor_summary=1.0,
        )[0]


def fill_previous_week_anchors(
    values: np.ndarray,
    time_features: np.ndarray,
    arrays: dict[str, np.ndarray],
    sessions: list[str],
    origin: int,
    config: DataConfig,
) -> None:
    if not sessions:
        return
    row_parts = [np.flatnonzero(arrays["sessions"][:origin] == session) for session in sessions]
    rows = np.concatenate([part for part in row_parts if part.size]) if any(part.size for part in row_parts) else np.array([], dtype=np.int64)
    if rows.size == 0:
        return
    high = float(np.nanmax(arrays["high"][rows]))
    low = float(np.nanmin(arrays["low"][rows]))
    close = float(arrays["close"][int(rows[-1])])
    open_ = float(arrays["open"][int(rows[0])])
    volume = float(np.nansum(arrays["volume"][rows]))
    transactions = float(np.nansum(arrays["transactions"][rows]))
    last_ts = int(arrays["timestamps_ns"][int(rows[-1])])
    for anchor_index, anchor_type in ((14, 14), (15, 15), (16, 16), (17, 17), (18, 18)):
        values[anchor_index] = anchor_row(
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            transactions=transactions,
            origin_ts=int(arrays["timestamps_ns"][origin]),
            anchor_ts=last_ts,
            anchor_type=anchor_type,
        )
        time_features[anchor_index] = build_relative_time_features(
            arrays,
            np.array([int(rows[-1])], dtype=np.int64),
            origin,
            config,
            bucket_minutes=5.0 * 1440.0,
            is_anchor_summary=1.0,
        )[0]


def anchor_row(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    transactions: float,
    origin_ts: int,
    anchor_ts: int,
    anchor_type: int,
) -> np.ndarray:
    age_days = max(0.0, (origin_ts - anchor_ts) / 1_000_000_000.0 / 86_400.0)
    return np.array(
        [
            open_,
            high,
            low,
            close,
            volume,
            transactions,
            float(log_return_bps(max(high, 1e-6), max(low, 1e-6))),
            float(log_return_bps(max(close, 1e-6), max(open_, 1e-6))),
            min(age_days / 30.0, 1.0),
            anchor_type / max(1.0, float(len(ANCHOR_NAMES) - 1)),
            1.0,
        ],
        dtype=np.float32,
    )


def normalize_anchor_values(
    values: np.ndarray,
    config: DataConfig,
    *,
    current_close: float,
    origin_features: np.ndarray,
) -> np.ndarray:
    feature_names = list(ANCHOR_VALUE_COLUMNS)
    transformed = values.astype(np.float32).copy()
    available = transformed[:, feature_names.index("available")] > 0.0
    for column in ("open", "high", "low", "close"):
        index = feature_names.index(column)
        transformed[available, index] = log_return_bps(transformed[available, index], current_close).astype(np.float32)
    origin_by_name = {
        name: origin_features[index]
        for index, name in enumerate(config.input_feature_columns)
        if index < origin_features.shape[0]
    }
    for column in ("volume", "transactions"):
        index = feature_names.index(column)
        origin_value = float(origin_by_name.get(column, 0.0))
        transformed[available, index] = (
            np.log1p(np.maximum(transformed[available, index], 0.0)) - math.log1p(max(origin_value, 0.0))
        ).astype(np.float32)
    zscore_feature_matrix(transformed, available, skip_names={"age_days", "anchor_type", "available"}, feature_names=feature_names)
    for column in ("age_days", "anchor_type", "available"):
        index = feature_names.index(column)
        transformed[:, index] = values[:, index]
    return np.nan_to_num(transformed, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def zscore_feature_matrix(
    values: np.ndarray,
    available: np.ndarray,
    *,
    skip_names: set[str],
    feature_names: list[str],
) -> None:
    if not np.any(available):
        values[:] = 0.0
        return
    for index, name in enumerate(feature_names):
        if name in skip_names:
            continue
        column = values[available, index]
        mean = float(np.nanmean(column))
        std = float(np.nanstd(column))
        if not math.isfinite(mean):
            mean = 0.0
        if not math.isfinite(std) or std < 1e-6:
            std = 1.0
        values[available, index] = (values[available, index] - mean) / std
        values[~available, index] = 0.0


def price_inputs_to_origin_return_bps(
    values: np.ndarray,
    config: DataConfig,
    current_close: float,
) -> np.ndarray:
    transformed = values.copy()
    feature_names = list(config.input_feature_columns)
    for column in ("open", "high", "low", "close"):
        if column in feature_names:
            index = feature_names.index(column)
            transformed[:, index] = log_return_bps(transformed[:, index], current_close).astype(np.float32)
    return transformed


def anchored_activity_inputs_to_log_ratio(values: np.ndarray, config: DataConfig) -> np.ndarray:
    transformed = values.copy()
    feature_names = list(config.input_feature_columns)
    for column in ("volume", "transactions", "quote_bid_size", "quote_ask_size", "quoted_share_depth"):
        if column in feature_names:
            index = feature_names.index(column)
            logged = np.log1p(np.maximum(transformed[:, index], 0.0)).astype(np.float32)
            transformed[:, index] = logged - logged[-1]
    return transformed


def denormalize_actual_zscore(
    values: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    center_array = np.asarray(center, dtype=np.float64).reshape(-1, 1, 1)
    scale_array = np.asarray(scale, dtype=np.float64).reshape(-1, 1, 1)
    return values * scale_array + center_array


def target_values_to_bps(
    values: np.ndarray,
    current_close: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
    target_mode: str,
) -> np.ndarray:
    if target_mode == "actual_price_zscore":
        prices = denormalize_actual_zscore(values, center, scale)
        return simple_return_bps(prices, np.asarray(current_close, dtype=np.float64).reshape(-1, 1, 1))
    if target_mode == "return_bps":
        return np.asarray(values, dtype=np.float64)
    raise ValueError(f"Unsupported target_mode: {target_mode}")


def log_return_bps(numerator: np.ndarray, denominator: np.ndarray | float) -> np.ndarray:
    numerator = np.asarray(numerator, dtype=np.float32)
    denominator_array = np.asarray(denominator, dtype=np.float32)
    safe_num = np.maximum(numerator, 1e-6)
    safe_den = np.maximum(denominator_array, 1e-6)
    return np.log(safe_num / safe_den) * 10000.0


def simple_return_bps(numerator: np.ndarray, denominator: np.ndarray | float) -> np.ndarray:
    numerator = np.asarray(numerator, dtype=np.float64)
    denominator_array = np.asarray(denominator, dtype=np.float64)
    safe_den = np.maximum(denominator_array, 1e-6)
    return (numerator / safe_den - 1.0) * 10000.0
