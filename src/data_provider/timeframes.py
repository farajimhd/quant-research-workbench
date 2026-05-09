from __future__ import annotations

from datetime import date, timezone

import polars as pl

from src.data_provider.config import TIMEFRAMES


def add_exchange_time_columns(frame: pl.DataFrame, exchange_timezone: str) -> pl.DataFrame:
    return (
        frame.with_columns(pl.from_epoch("window_start", time_unit="ns").dt.replace_time_zone("UTC").alias("bar_time_utc"))
        .with_columns(pl.col("bar_time_utc").dt.convert_time_zone(exchange_timezone).alias("bar_time_market"))
        .with_columns(
            pl.col("bar_time_market").dt.date().cast(pl.Utf8).alias("session_date"),
            (pl.col("bar_time_market").dt.strftime("%Y-%m").alias("session_month")),
            (
                (pl.col("bar_time_market").dt.hour().cast(pl.Int32) * 60)
                + pl.col("bar_time_market").dt.minute().cast(pl.Int32)
            ).alias("minute_of_day"),
        )
    )


def add_bar_id(frame: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    return frame.with_columns(
        pl.concat_str(
            [
                pl.lit(timeframe),
                pl.col("ticker").cast(pl.Utf8),
                pl.col("bar_time_utc").dt.strftime("%Y-%m-%dT%H:%M:%S%.f%z"),
            ],
            separator="|",
        ).alias("bar_id"),
        pl.lit(timeframe).alias("timeframe"),
    )


def canonicalize_1m(raw_frame: pl.DataFrame, exchange_timezone: str) -> pl.DataFrame:
    if raw_frame.is_empty():
        return raw_frame
    return (
        raw_frame.with_columns(
            pl.col("ticker").cast(pl.Utf8),
            pl.col("volume").cast(pl.Float64),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("transactions").cast(pl.Float64),
            pl.col("window_start").cast(pl.Int64),
        )
        .pipe(add_exchange_time_columns, exchange_timezone)
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, "1m")
    )


def timeframe_minutes(timeframe: str) -> int | None:
    value = TIMEFRAMES.get(timeframe)
    return value if isinstance(value, int) else None


def aggregate_intraday(frame_1m: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    minutes = timeframe_minutes(timeframe)
    if minutes is None:
        raise ValueError(f"{timeframe} is not an intraday minute timeframe")
    if timeframe == "1m" or frame_1m.is_empty():
        return frame_1m
    return (
        frame_1m.with_columns(((pl.col("minute_of_day") // minutes) * minutes).alias("_bucket_minute"))
        .group_by(["ticker", "session_date", "_bucket_minute"])
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
            pl.col("session_month").first().alias("session_month"),
        )
        .drop("_bucket_minute")
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, timeframe)
    )


def aggregate_daily(frame_1m: pl.DataFrame) -> pl.DataFrame:
    if frame_1m.is_empty():
        return frame_1m
    return (
        frame_1m.group_by(["ticker", "session_date"])
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
            pl.col("session_month").first().alias("session_month"),
        )
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, "1d")
    )


def aggregate_monthly(daily_frame: pl.DataFrame) -> pl.DataFrame:
    if daily_frame.is_empty():
        return daily_frame
    return (
        daily_frame.group_by(["ticker", "session_month"])
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
            pl.col("session_date").min().alias("session_date"),
        )
        .with_columns(pl.col("session_month"))
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, "1mo")
    )
