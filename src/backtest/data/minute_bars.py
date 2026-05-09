from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from src.backtest.config import BacktestConfig
from src.backtest.indicators import add_standard_indicators
from src.data_provider.config import DataProviderConfig
from src.data_provider.provider import MarketDataProvider


@dataclass(slots=True)
class DayFrames:
    session_date: date
    minute_bars: pl.DataFrame
    five_minute_bars: pl.DataFrame
    prior_daily_stats: pl.DataFrame


def date_range(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def minute_file_path(data_root: Path, session_date: date) -> Path:
    return (
        data_root
        / f"{session_date.year:04d}"
        / f"{session_date.month:02d}"
        / f"{session_date.isoformat()}.csv.gz"
    )


def available_session_dates(config: BacktestConfig) -> list[date]:
    sessions = []
    for session in date_range(config.start_date, config.end_date):
        if minute_file_path(config.data_root, session).exists():
            sessions.append(session)
    return sessions


def add_market_time_columns(frame: pl.DataFrame, config: BacktestConfig) -> pl.DataFrame:
    offset_ns = int(config.market_utc_offset_hours * 60 * 60 * 1_000_000_000)
    return (
        frame.with_columns((pl.col("window_start") + offset_ns).alias("_market_window_start"))
        .with_columns(
            pl.from_epoch("window_start", time_unit="ns").alias("bar_time_utc"),
            pl.from_epoch("_market_window_start", time_unit="ns").alias("bar_time_market"),
        )
        .with_columns(
            (
                (pl.col("bar_time_market").dt.hour().cast(pl.Int32) * 60)
                + pl.col("bar_time_market").dt.minute().cast(pl.Int32)
            ).alias("minute_of_day")
        )
        .drop("_market_window_start")
    )


def load_minute_bars(config: BacktestConfig, session_date: date) -> pl.DataFrame:
    bars = load_provider_bars(config, session_date, "1m")
    if bars.is_empty():
        raise FileNotFoundError(
            f"Processed 1m provider bars not found for {session_date.isoformat()} under {config.processed_data_root}. "
            "Build data from the Data Provider page first."
        )
    return regular_session_filter(bars, config)


def load_provider_bars(config: BacktestConfig, session_date: date, timeframe: str) -> pl.DataFrame:
    provider = MarketDataProvider(
        DataProviderConfig(
            raw_root=config.data_root,
            processed_root=config.processed_data_root,
            exchange_timezone="America/New_York",
        )
    )
    return provider.load_bars(
        start_date=session_date,
        end_date=session_date,
        timeframe=timeframe,
        feature_groups=["core", "session", "momentum", "volatility", "volume_liquidity", "price_action"],
    )


def regular_session_filter(frame: pl.DataFrame, config: BacktestConfig) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        (pl.col("minute_of_day") >= config.session_start_minute)
        & (pl.col("minute_of_day") < config.session_end_minute)
    ).sort(["ticker", "bar_time_market"])


def consolidate_five_minute(minute_bars: pl.DataFrame, config: BacktestConfig) -> pl.DataFrame:
    return (
        minute_bars.with_columns(
            (((pl.col("minute_of_day") - config.session_start_minute) // 5) * 5 + config.session_start_minute).alias(
                "five_minute_bucket"
            )
        )
        .group_by(["ticker", "five_minute_bucket"])
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("transactions").sum().alias("transactions"),
            pl.col("window_start").min().alias("window_start"),
            pl.col("bar_time_utc").min().alias("bar_time_utc"),
            pl.col("bar_time_market").min().alias("bar_time_market"),
            pl.col("minute_of_day").min().alias("minute_of_day"),
        )
        .sort(["ticker", "bar_time_market"])
        .pipe(add_standard_indicators)
        .with_columns(
            (pl.col("bar_time_market") + pl.duration(minutes=5))
            .cast(pl.Datetime("ns", "America/New_York"))
            .alias("indicator_available_time")
        )
    )


def attach_five_minute_context(minute_bars: pl.DataFrame, five_minute_bars: pl.DataFrame) -> pl.DataFrame:
    minute_bars = minute_bars.sort(["ticker", "bar_time_market"])
    context = five_minute_bars.select(
        "ticker",
        "indicator_available_time",
        pl.col("close").alias("close_5m"),
        pl.col("vwap").alias("vwap_5m"),
        pl.col("macd_line").alias("macd_line_5m"),
        pl.col("macd_signal").alias("macd_signal_5m"),
        pl.col("macd_hist").alias("macd_hist_5m"),
        pl.col("macd_ready").alias("macd_ready_5m"),
        pl.col("tema9").alias("tema9_5m"),
        pl.col("tema20").alias("tema20_5m"),
        pl.col("tema_ready").alias("tema_ready_5m"),
    ).sort(["ticker", "indicator_available_time"])

    # Polars cannot verify sortedness for grouped as-of joins even after explicit sorting.
    # The inputs are sorted above, so disable the warning-only sortedness check.
    return minute_bars.join_asof(
        context,
        left_on="bar_time_market",
        right_on="indicator_available_time",
        by="ticker",
        strategy="backward",
        check_sortedness=False,
    )


def prior_daily_paths(config: BacktestConfig, session_date: date, lookback_files: int = 20) -> list[Path]:
    paths = []
    cursor = session_date - timedelta(days=1)
    while cursor >= session_date - timedelta(days=60) and len(paths) < lookback_files:
        path = minute_file_path(config.data_root, cursor)
        if path.exists():
            paths.append(path)
        cursor -= timedelta(days=1)
    return list(reversed(paths))


def load_prior_daily_stats(config: BacktestConfig, session_date: date) -> pl.DataFrame:
    provider = MarketDataProvider(
        DataProviderConfig(
            raw_root=config.data_root,
            processed_root=config.processed_data_root,
            exchange_timezone="America/New_York",
        )
    )
    prior_start = session_date - timedelta(days=60)
    prior_end = session_date - timedelta(days=1)
    daily = provider.load_bars(
        start_date=prior_start,
        end_date=prior_end,
        timeframe="1d",
        feature_groups=["core", "volatility"],
    )
    if not daily.is_empty():
        return (
            daily.sort(["ticker", "session_date"])
            .with_columns((pl.col("high") - pl.col("low")).alias("daily_range"))
            .group_by("ticker")
            .agg(
                pl.col("volume").tail(14).mean().alias("avg_daily_volume_14"),
                pl.col("daily_range").tail(14).mean().alias("atr_14"),
                pl.col("close").last().alias("previous_close"),
                pl.len().alias("daily_rows"),
            )
        )

    scans = []
    for source in prior_daily_paths(config, session_date):
        scans.append(
            pl.scan_csv(source)
            .group_by("ticker")
            .agg(
                pl.col("volume").sum().alias("daily_volume"),
                pl.col("high").max().alias("daily_high"),
                pl.col("low").min().alias("daily_low"),
                pl.col("close").last().alias("daily_close"),
            )
            .with_columns(pl.lit(source.stem.removesuffix(".csv")).alias("session_date"))
        )

    schema = {
        "ticker": pl.String,
        "avg_daily_volume_14": pl.Float64,
        "atr_14": pl.Float64,
        "previous_close": pl.Float64,
        "daily_rows": pl.UInt32,
    }
    if not scans:
        return pl.DataFrame(schema=schema)

    return (
        pl.concat(scans)
        .sort(["ticker", "session_date"])
        .with_columns((pl.col("daily_high") - pl.col("daily_low")).alias("daily_range"))
        .group_by("ticker")
        .agg(
            pl.col("daily_volume").tail(14).mean().alias("avg_daily_volume_14"),
            pl.col("daily_range").tail(14).mean().alias("atr_14"),
            pl.col("daily_close").last().alias("previous_close"),
            pl.len().alias("daily_rows"),
        )
        .collect()
    )


def load_day_frames(config: BacktestConfig, session_date: date) -> DayFrames:
    minute_bars = load_minute_bars(config, session_date)
    five_minute_bars = regular_session_filter(load_provider_bars(config, session_date, "5m"), config)
    if five_minute_bars.is_empty():
        five_minute_bars = consolidate_five_minute(minute_bars, config)
    elif "indicator_available_time" not in five_minute_bars.columns:
        five_minute_bars = five_minute_bars.with_columns(
            (pl.col("bar_time_market") + pl.duration(minutes=5))
            .cast(pl.Datetime("ns", "America/New_York"))
            .alias("indicator_available_time")
        )
    minute_bars = attach_five_minute_context(minute_bars, five_minute_bars)
    prior_daily_stats = load_prior_daily_stats(config, session_date)
    return DayFrames(
        session_date=session_date,
        minute_bars=minute_bars,
        five_minute_bars=five_minute_bars,
        prior_daily_stats=prior_daily_stats,
    )
