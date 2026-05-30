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

from research.inhouse_transformer.v19.config import DataConfig
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

LOG_RULE = "*" * 96
ALL_TICKERS_SENTINEL = "__ALL_TICKERS__"


@dataclass(slots=True)
class SessionCoverage:
    sessions: int = 0
    sessions_with_windows: int = 0
    windows: int = 0
    batches: int = 0


def parse_ticker_list(raw: str) -> tuple[str, ...]:
    parts = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    if len(parts) == 1 and parts[0] in {"ALL", "*"}:
        return (ALL_TICKERS_SENTINEL,)
    return parts


def uses_all_tickers(tickers: tuple[str, ...]) -> bool:
    return len(tickers) == 1 and tickers[0] == ALL_TICKERS_SENTINEL


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
    if tickers and not uses_all_tickers(tickers):
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
            carryover: dict[str, pl.DataFrame] = {}
            batch = BatchBuilder(
                batch_size=self.batch_size,
                context_length=self.config.context_length,
                feature_count=len(self.config.input_feature_columns),
                time_feature_count=len(self.config.time_feature_columns),
                horizon=self.config.horizon,
                target_count=len(self.config.target_columns),
                target_bit_count=target_bit_count(self.config),
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
        horizon: int,
        target_count: int,
        target_bit_count: int,
    ) -> None:
        self.values = np.empty((batch_size, context_length, feature_count), dtype=np.float32)
        self.time_features = np.empty((batch_size, context_length, time_feature_count), dtype=np.float32)
        self.targets = np.empty((batch_size, horizon, target_count, target_bit_count), dtype=np.float32)
        self.target_bps = np.empty((batch_size, horizon, target_count), dtype=np.float32)
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
            horizon=self.targets.shape[1],
            target_count=self.targets.shape[2],
            target_bit_count=self.targets.shape[3],
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
        target_bps = log_return_bps(target_prices, current_close).astype(np.float32)
        if config.target_mode == "actual_price_zscore":
            center, scale = price_center, price_scale
            targets = ((target_prices - center) / scale).astype(np.float32)[..., None]
        elif config.target_mode == "return_bps":
            center = 0.0
            scale = 1.0
            targets = target_bps[..., None]
        elif config.target_mode == "binary_magnitude_bps":
            center = 0.0
            scale = 1.0
            targets = encode_binary_magnitude_targets(target_bps, bits=config.binary_magnitude_bits)
        else:
            raise ValueError(f"Unsupported target_mode: {config.target_mode}")

        self.values[self.count] = normalize_actual_feature_window(
            arrays["features"][start:end],
            config,
            current_close=current_close,
        )
        self.time_features[self.count] = normalize_time_feature_window(arrays["time_features"][start:end], config)
        self.targets[self.count] = targets
        self.target_bps[self.count] = target_bps
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
            "targets": torch.from_numpy(self.targets[rows].copy()),
            "target_bps": torch.from_numpy(self.target_bps[rows].copy()),
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


def target_bit_count(config: DataConfig) -> int:
    if config.target_mode == "binary_magnitude_bps":
        return 1 + int(config.binary_magnitude_bits)
    return 1


def combine_carryover(previous: pl.DataFrame | None, current: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    if previous is None or previous.is_empty() or not config.carry_context_across_session:
        return current
    return pl.concat([previous, current], how="vertical_relaxed").sort("bar_time_market")


def tail_carryover(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    rows = max(config.context_length, config.horizon)
    return frame.tail(rows)


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
        "sessions": sessions,
        "timestamps_ns": timestamps_ns,
        "features": features,
        "time_features": time_features,
    }


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


def normalize_time_feature_window(values: np.ndarray, config: DataConfig) -> np.ndarray:
    if config.time_feature_normalization != "window_zscore_all":
        raise ValueError(f"Unsupported time_feature_normalization: {config.time_feature_normalization}")
    raw = np.asarray(values, dtype=np.float32)
    mean = np.nanmean(raw, axis=0, dtype=np.float64).astype(np.float32)
    std = np.nanstd(raw, axis=0, dtype=np.float64).astype(np.float32)
    mean = np.nan_to_num(mean, nan=0.0, posinf=0.0, neginf=0.0)
    std = np.nan_to_num(std, nan=1.0, posinf=1.0, neginf=1.0)
    std = np.where(std < 1e-6, 1.0, std)
    normalized = (raw - mean) / std
    return np.nan_to_num(normalized, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


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
    if target_mode == "binary_magnitude_bps":
        return decode_binary_magnitude_logits_to_bps(values)
    raise ValueError(f"Unsupported target_mode: {target_mode}")


def encode_binary_magnitude_targets(target_bps: np.ndarray, *, bits: int) -> np.ndarray:
    target_bps = np.asarray(target_bps, dtype=np.float32)
    sign = (target_bps >= 0.0).astype(np.float32)[..., None]
    max_magnitude = (1 << int(bits)) - 1
    magnitude = np.rint(np.abs(target_bps)).astype(np.int64)
    magnitude = np.clip(magnitude, 0, max_magnitude)
    bit_weights = (1 << np.arange(int(bits), dtype=np.int64)).reshape((1,) * magnitude.ndim + (-1,))
    magnitude_bits = ((magnitude[..., None] & bit_weights) > 0).astype(np.float32)
    return np.concatenate([sign, magnitude_bits], axis=-1).astype(np.float32)


def decode_binary_magnitude_logits_to_bps(values: np.ndarray) -> np.ndarray:
    logits = np.asarray(values, dtype=np.float64)
    if logits.ndim < 1 or logits.shape[-1] < 2:
        raise ValueError(f"Expected binary magnitude logits with a bit axis, got shape {logits.shape}.")
    probabilities = sigmoid_np(logits)
    sign = np.where(probabilities[..., 0] >= 0.5, 1.0, -1.0)
    bits = probabilities[..., 1:] >= 0.5
    weights = (1 << np.arange(bits.shape[-1], dtype=np.int64)).astype(np.float64)
    magnitude = (bits.astype(np.float64) * weights).sum(axis=-1)
    return sign * magnitude


def binary_magnitude_logits_to_distribution_stats(values: np.ndarray) -> dict[str, np.ndarray]:
    logits = np.asarray(values, dtype=np.float64)
    if logits.ndim < 1 or logits.shape[-1] < 2:
        raise ValueError(f"Expected binary magnitude logits with a bit axis, got shape {logits.shape}.")
    probabilities = sigmoid_np(logits)
    sign_probability = probabilities[..., 0]
    magnitude_probabilities = probabilities[..., 1:]
    weights = (1 << np.arange(magnitude_probabilities.shape[-1], dtype=np.int64)).astype(np.float64)

    expected_magnitude = (magnitude_probabilities * weights).sum(axis=-1)
    magnitude_variance = (magnitude_probabilities * (1.0 - magnitude_probabilities) * np.square(weights)).sum(axis=-1)
    magnitude_std = np.sqrt(np.maximum(magnitude_variance, 0.0))

    sign_mean = 2.0 * sign_probability - 1.0
    expected_signed_bps = sign_mean * expected_magnitude
    confidence_denominator = np.abs(expected_signed_bps) + magnitude_std + 1e-12
    confidence = np.divide(
        np.abs(expected_signed_bps),
        confidence_denominator,
        out=np.zeros_like(expected_signed_bps, dtype=np.float64),
        where=confidence_denominator > 0.0,
    )
    return {
        "expected_signed_bps": expected_signed_bps,
        "expected_magnitude_bps": expected_magnitude,
        "magnitude_std_bps": magnitude_std,
        "confidence": np.clip(confidence, 0.0, 1.0),
        "sign_confidence": np.abs(sign_mean),
        "p_up": sign_probability,
    }


def sigmoid_np(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


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
