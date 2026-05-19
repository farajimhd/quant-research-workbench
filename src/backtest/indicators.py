from __future__ import annotations

import polars as pl


def add_vwap(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.sort(["ticker", "bar_time_market"])
        .with_columns(
            (pl.col("close") * pl.col("volume")).cum_sum().over("ticker").alias("_cum_dollar_volume"),
            pl.col("volume").cum_sum().over("ticker").alias("_cum_volume"),
        )
        .with_columns(
            pl.when(pl.col("_cum_volume") > 0)
            .then(pl.col("_cum_dollar_volume") / pl.col("_cum_volume"))
            .otherwise(None)
            .alias("vwap")
        )
        .drop(["_cum_dollar_volume", "_cum_volume"])
    )


def add_ema(frame: pl.DataFrame, column: str, span: int, alias: str) -> pl.DataFrame:
    return frame.with_columns(
        pl.col(column).ewm_mean(span=span, adjust=False).over("ticker").alias(alias)
    )


def add_tema(frame: pl.DataFrame, column: str, period: int, prefix: str) -> pl.DataFrame:
    ema1 = f"{prefix}_ema1"
    ema2 = f"{prefix}_ema2"
    ema3 = f"{prefix}_ema3"
    open_ema1 = f"{prefix}_open_ema1"
    open_ema2 = f"{prefix}_open_ema2"
    open_ema3 = f"{prefix}_open_ema3"
    alpha = 2.0 / (period + 1.0)
    prev_ema1 = pl.col(ema1).shift(1).over("ticker")
    prev_ema2 = pl.col(ema2).shift(1).over("ticker")
    prev_ema3 = pl.col(ema3).shift(1).over("ticker")
    return (
        add_ema(frame, column, period, ema1)
        .with_columns(pl.col(ema1).ewm_mean(span=period, adjust=False).over("ticker").alias(ema2))
        .with_columns(pl.col(ema2).ewm_mean(span=period, adjust=False).over("ticker").alias(ema3))
        .with_columns(
            pl.when(prev_ema1.is_null())
            .then(pl.col("open"))
            .otherwise((alpha * pl.col("open")) + ((1.0 - alpha) * prev_ema1))
            .alias(open_ema1)
        )
        .with_columns(
            pl.when(prev_ema2.is_null())
            .then(pl.col(open_ema1))
            .otherwise((alpha * pl.col(open_ema1)) + ((1.0 - alpha) * prev_ema2))
            .alias(open_ema2)
        )
        .with_columns(
            pl.when(prev_ema3.is_null())
            .then(pl.col(open_ema2))
            .otherwise((alpha * pl.col(open_ema2)) + ((1.0 - alpha) * prev_ema3))
            .alias(open_ema3)
        )
        .with_columns(
            ((3.0 * pl.col(ema1)) - (3.0 * pl.col(ema2)) + pl.col(ema3)).alias(prefix),
            ((3.0 * pl.col(open_ema1)) - (3.0 * pl.col(open_ema2)) + pl.col(open_ema3)).alias(f"current_open_{prefix}"),
        )
    )


def add_macd(
    frame: pl.DataFrame,
    column: str = "close",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pl.DataFrame:
    return (
        frame.with_columns(
            pl.col(column).ewm_mean(span=fast, adjust=False).over("ticker").alias("macd_fast_ema"),
            pl.col(column).ewm_mean(span=slow, adjust=False).over("ticker").alias("macd_slow_ema"),
        )
        .with_columns((pl.col("macd_fast_ema") - pl.col("macd_slow_ema")).alias("macd_line"))
        .with_columns(pl.col("macd_line").ewm_mean(span=signal, adjust=False).over("ticker").alias("macd_signal"))
        .with_columns((pl.col("macd_line") - pl.col("macd_signal")).alias("macd_hist"))
    )


def add_standard_indicators(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        add_vwap(frame)
        .pipe(add_macd)
        .pipe(add_tema, "close", 9, "tema9")
        .pipe(add_tema, "close", 20, "tema20")
        .with_columns(
            pl.cum_count("close").over("ticker").alias("indicator_bar_count"),
            (pl.cum_count("close").over("ticker") >= 35).alias("macd_ready"),
            (pl.cum_count("close").over("ticker") >= 20).alias("tema_ready"),
        )
    )
