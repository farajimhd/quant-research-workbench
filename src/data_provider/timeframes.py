from __future__ import annotations

from datetime import date, timezone

import polars as pl

from src.data_provider.config import TIMEFRAMES


SPREAD_COLUMNS = [
    "quote_bid_price",
    "quote_ask_price",
    "spread",
    "quote_midpoint",
    "spread_bps",
    "spread_bps_abs",
    "quote_bid_size",
    "quote_ask_size",
    "quote_sip_timestamp",
    "quote_missing",
    "spread_is_locked_or_crossed",
    "spread_bps_avg",
    "spread_bps_median",
    "spread_bps_max",
    "quote_valid_ratio",
    "locked_or_crossed_count",
    "quoted_share_depth",
    "quoted_dollar_depth",
]
LEGACY_SPREAD_COLUMNS = [
    "actual_spread",
    "actual_spread_bps",
    "actual_spread_bps_abs",
    "actual_spread_bps_avg",
    "actual_spread_bps_median",
    "actual_spread_bps_max",
]


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


def enrich_1m_with_spread(frame_1m: pl.DataFrame, spread_frame: pl.DataFrame) -> pl.DataFrame:
    if frame_1m.is_empty():
        return frame_1m
    if spread_frame.is_empty():
        return drop_existing_spread_columns(frame_1m)
    enriched = drop_existing_spread_columns(frame_1m).join(spread_frame, on=["ticker", "window_start"], how="left")
    return add_spread_quality_columns(enriched)


def drop_existing_spread_columns(frame: pl.DataFrame) -> pl.DataFrame:
    drop_columns = [column for column in [*SPREAD_COLUMNS, *LEGACY_SPREAD_COLUMNS] if column in frame.columns]
    return frame.drop(drop_columns) if drop_columns else frame


def add_spread_quality_columns(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty() or "spread_bps" not in frame.columns:
        return frame
    quote_valid_expr = (
        pl.col("spread_bps").is_not_null()
        & (pl.col("quote_missing").fill_null(True) == False)
        & (pl.col("quote_bid_price").fill_null(0.0) > 0)
        & (pl.col("quote_ask_price").fill_null(0.0) > 0)
    )
    result = frame.with_columns(
        pl.col("spread_bps").abs().alias("spread_bps_abs"),
        quote_valid_expr.cast(pl.Float64).alias("quote_valid_ratio"),
        pl.col("spread_is_locked_or_crossed").fill_null(False).cast(pl.Int64).alias("locked_or_crossed_count"),
        (pl.col("quote_bid_size").fill_null(0) + pl.col("quote_ask_size").fill_null(0)).cast(pl.Float64).alias("quoted_share_depth"),
    )
    if "quote_midpoint" in result.columns:
        result = result.with_columns((pl.col("quote_midpoint") * pl.col("quoted_share_depth")).alias("quoted_dollar_depth"))
    return result.with_columns(
        pl.col("spread_bps_abs").alias("spread_bps_avg"),
        pl.col("spread_bps_abs").alias("spread_bps_median"),
        pl.col("spread_bps_abs").alias("spread_bps_max"),
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
        .agg(*intraday_aggregation_expressions(frame_1m))
        .drop("_bucket_minute")
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, timeframe)
    )


def aggregate_daily(frame_1m: pl.DataFrame) -> pl.DataFrame:
    if frame_1m.is_empty():
        return frame_1m
    return (
        frame_1m.group_by(["ticker", "session_date"])
        .agg(*intraday_aggregation_expressions(frame_1m))
        .sort(["ticker", "bar_time_utc"])
        .pipe(add_bar_id, "1d")
    )


def intraday_aggregation_expressions(frame: pl.DataFrame) -> list[pl.Expr]:
    columns = set(frame.columns)
    exprs: list[pl.Expr] = [
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
    ]
    for column in [
        "quote_bid_price",
        "quote_ask_price",
        "spread",
        "quote_midpoint",
        "spread_bps",
        "spread_bps_abs",
        "quote_bid_size",
        "quote_ask_size",
        "quote_sip_timestamp",
        "quote_missing",
        "spread_is_locked_or_crossed",
        "quoted_share_depth",
        "quoted_dollar_depth",
    ]:
        if column in columns:
            exprs.append(pl.col(column).drop_nulls().last().alias(column))
    if "spread_bps_abs" in columns:
        exprs.extend(
            [
                pl.col("spread_bps_abs").drop_nulls().mean().alias("spread_bps_avg"),
                pl.col("spread_bps_abs").drop_nulls().median().alias("spread_bps_median"),
                pl.col("spread_bps_abs").drop_nulls().max().alias("spread_bps_max"),
            ]
        )
    if "quote_valid_ratio" in columns:
        exprs.append(pl.col("quote_valid_ratio").mean().alias("quote_valid_ratio"))
    if "locked_or_crossed_count" in columns:
        exprs.append(pl.col("locked_or_crossed_count").sum().alias("locked_or_crossed_count"))
    return exprs


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
