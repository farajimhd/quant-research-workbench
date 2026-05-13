from __future__ import annotations

from datetime import date

import polars as pl


PORTFOLIO_CANDLE_TIMEFRAMES = ("1m", "1h", "2h", "4h", "1d")


def default_portfolio_candle_timeframe(start_date: date, end_date: date) -> str:
    days = max(1, (end_date - start_date).days + 1)
    if days <= 5:
        return "1h"
    if days <= 15:
        return "2h"
    if days <= 45:
        return "1d"
    return "1d"


def build_portfolio_candles(
    portfolio_rows: list[dict],
    *,
    initial_cash: float,
    timeframes: tuple[str, ...] = PORTFOLIO_CANDLE_TIMEFRAMES,
) -> list[dict]:
    if not portfolio_rows:
        return []
    frame = (
        pl.DataFrame(portfolio_rows, infer_schema_length=None)
        .with_columns(
            pl.col("timestamp").cast(pl.Datetime).alias("timestamp"),
            pl.col("equity").cast(pl.Float64),
            (pl.col("equity").cast(pl.Float64) - float(initial_cash)).alias("pnl"),
        )
        .sort("timestamp")
    )
    rows = []
    for timeframe in timeframes:
        if timeframe == "1m":
            candles = minute_candles(frame)
        else:
            candles = aggregate_candles(frame, timeframe)
        rows.extend(candles.with_columns(pl.lit(timeframe).alias("timeframe")).to_dicts())
    return rows


def minute_candles(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select(
        "timestamp",
        "timeframe" if "timeframe" in frame.columns else pl.lit("1m").alias("timeframe"),
        pl.col("pnl").alias("open"),
        pl.col("pnl").alias("high"),
        pl.col("pnl").alias("low"),
        pl.col("pnl").alias("close"),
        pl.col("equity").alias("equity_open"),
        pl.col("equity").alias("equity_high"),
        pl.col("equity").alias("equity_low"),
        pl.col("equity").alias("equity_close"),
        "cash",
        "open_positions",
    )


def aggregate_candles(frame: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    every = polars_duration(timeframe)
    return (
        frame.group_by_dynamic("timestamp", every=every, closed="left", label="left")
        .agg(
            pl.col("pnl").first().alias("open"),
            pl.col("pnl").max().alias("high"),
            pl.col("pnl").min().alias("low"),
            pl.col("pnl").last().alias("close"),
            pl.col("equity").first().alias("equity_open"),
            pl.col("equity").max().alias("equity_high"),
            pl.col("equity").min().alias("equity_low"),
            pl.col("equity").last().alias("equity_close"),
            pl.col("cash").last().alias("cash"),
            pl.col("open_positions").max().alias("open_positions"),
        )
        .drop_nulls(["open", "high", "low", "close"])
        .sort("timestamp")
    )


def polars_duration(timeframe: str) -> str:
    if timeframe == "1m":
        return "1m"
    if timeframe == "1h":
        return "1h"
    if timeframe == "2h":
        return "2h"
    if timeframe == "4h":
        return "4h"
    if timeframe == "1d":
        return "1d"
    raise ValueError(f"Unsupported portfolio candle timeframe: {timeframe}")
