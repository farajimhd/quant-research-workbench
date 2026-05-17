from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl


PORTFOLIO_CANDLE_TIMEFRAMES = ("30m", "1h", "2h", "4h", "1d")
EXCHANGE_TIME_ZONE = "America/New_York"


def default_portfolio_candle_timeframe(start_date: date, end_date: date) -> str:
    return "30m"


def build_portfolio_candles(
    portfolio_rows: list[dict],
    *,
    initial_cash: float,
    timeframes: tuple[str, ...] = PORTFOLIO_CANDLE_TIMEFRAMES,
) -> list[dict]:
    if not portfolio_rows:
        return []
    frame = (
        pl.DataFrame(normalize_portfolio_timestamps(portfolio_rows), infer_schema_length=None)
        .with_columns(
            pl.col("equity").cast(pl.Float64),
        )
        .sort("timestamp")
    )
    frame = ensure_portfolio_metric_columns(frame, initial_cash)
    rows = []
    for timeframe in timeframes:
        if timeframe == "1m":
            candles = minute_candles(frame)
        else:
            candles = aggregate_candles(frame, timeframe)
        rows.extend(candles.with_columns(pl.lit(timeframe).alias("timeframe")).to_dicts())
    return rows


def normalize_portfolio_timestamps(portfolio_rows: list[dict]) -> list[dict]:
    timezone = ZoneInfo(EXCHANGE_TIME_ZONE)
    normalized_rows = []
    for row in portfolio_rows:
        timestamp = row.get("timestamp")
        if isinstance(timestamp, datetime):
            dt = timestamp
        elif timestamp is None:
            dt = None
        else:
            try:
                dt = datetime.fromisoformat(str(timestamp))
            except ValueError:
                dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone)
            else:
                dt = dt.astimezone(timezone)
        normalized_rows.append({**row, "timestamp": dt})
    return normalized_rows


def ensure_portfolio_metric_columns(frame: pl.DataFrame, initial_cash: float) -> pl.DataFrame:
    if "pnl" not in frame.columns:
        frame = frame.with_columns((pl.col("equity").cast(pl.Float64) - float(initial_cash)).alias("pnl"))
    if "open_unrealized_pnl" not in frame.columns:
        frame = frame.with_columns(pl.lit(0.0).alias("open_unrealized_pnl"))
    if "realized_pnl" not in frame.columns:
        frame = frame.with_columns((pl.col("pnl") - pl.col("open_unrealized_pnl")).alias("realized_pnl"))
    if "gross_exposure" not in frame.columns:
        frame = frame.with_columns(pl.lit(0.0).alias("gross_exposure"))
    if "peak_equity" not in frame.columns:
        frame = frame.with_columns(pl.col("equity").cum_max().clip(lower_bound=float(initial_cash)).alias("peak_equity"))
    if "drawdown" not in frame.columns:
        frame = frame.with_columns((pl.col("equity") - pl.col("peak_equity")).alias("drawdown"))
    if "drawdown_pct" not in frame.columns:
        frame = frame.with_columns(
            pl.when(pl.col("peak_equity") > 0)
            .then(pl.col("drawdown") / pl.col("peak_equity"))
            .otherwise(0.0)
            .alias("drawdown_pct")
        )
    return frame.with_columns(
        pl.col("pnl").cast(pl.Float64),
        pl.col("open_unrealized_pnl").cast(pl.Float64),
        pl.col("realized_pnl").cast(pl.Float64),
        pl.col("gross_exposure").cast(pl.Float64),
        pl.col("peak_equity").cast(pl.Float64),
        pl.col("drawdown").cast(pl.Float64),
        pl.col("drawdown_pct").cast(pl.Float64),
    )


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
        pl.col("open_unrealized_pnl").alias("open_unrealized_open"),
        pl.col("open_unrealized_pnl").alias("open_unrealized_high"),
        pl.col("open_unrealized_pnl").alias("open_unrealized_low"),
        pl.col("open_unrealized_pnl").alias("open_unrealized_close"),
        pl.col("realized_pnl").alias("realized_pnl_open"),
        pl.col("realized_pnl").alias("realized_pnl_high"),
        pl.col("realized_pnl").alias("realized_pnl_low"),
        pl.col("realized_pnl").alias("realized_pnl_close"),
        pl.col("drawdown").alias("drawdown_open"),
        pl.col("drawdown").alias("drawdown_high"),
        pl.col("drawdown").alias("drawdown_low"),
        pl.col("drawdown").alias("drawdown_close"),
        pl.col("drawdown_pct").alias("drawdown_pct_close"),
        "gross_exposure",
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
            pl.col("open_unrealized_pnl").first().alias("open_unrealized_open"),
            pl.col("open_unrealized_pnl").max().alias("open_unrealized_high"),
            pl.col("open_unrealized_pnl").min().alias("open_unrealized_low"),
            pl.col("open_unrealized_pnl").last().alias("open_unrealized_close"),
            pl.col("realized_pnl").first().alias("realized_pnl_open"),
            pl.col("realized_pnl").max().alias("realized_pnl_high"),
            pl.col("realized_pnl").min().alias("realized_pnl_low"),
            pl.col("realized_pnl").last().alias("realized_pnl_close"),
            pl.col("drawdown").first().alias("drawdown_open"),
            pl.col("drawdown").max().alias("drawdown_high"),
            pl.col("drawdown").min().alias("drawdown_low"),
            pl.col("drawdown").last().alias("drawdown_close"),
            pl.col("drawdown_pct").last().alias("drawdown_pct_close"),
            pl.col("gross_exposure").max().alias("gross_exposure"),
            pl.col("cash").last().alias("cash"),
            pl.col("open_positions").max().alias("open_positions"),
        )
        .drop_nulls(["open", "high", "low", "close"])
        .sort("timestamp")
    )


def polars_duration(timeframe: str) -> str:
    if timeframe == "1m":
        return "1m"
    if timeframe == "30m":
        return "30m"
    if timeframe == "1h":
        return "1h"
    if timeframe == "2h":
        return "2h"
    if timeframe == "4h":
        return "4h"
    if timeframe == "1d":
        return "1d"
    raise ValueError(f"Unsupported portfolio candle timeframe: {timeframe}")
