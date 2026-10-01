"""Vectorized rolling producer observations for the unpublished liquidity exit.

This is a necessary activity condition, not trade admission. Inputs must come
from independently certified producer attempts; this compiler does not grant
that certification. No fill/outcome labels, market reads or order calls exist.
"""
import polars as pl

from src.trading_runtime.strategy_liquidity_fade_failure import MAX_TRADE_COUNT

SOURCE_KEYS = ("source_build_id", "session_date", "ticker", "source_attempt_id")
REQUIRED = (*SOURCE_KEYS, "boundary_ms", "trade_count")


def compile_liquidity_fade_observations(frame: pl.DataFrame) -> pl.DataFrame:
    """Keep shape (n_rows,) and input order; derive four completed 5s counts.

    Each shift stays within one build/day/ticker/attempt. Missing/zero-activity
    observations are not dropped before the window calculation. The output's
    activity bit still requires scalar original-entry, whole-held, price,
    momentum, fresh-quote and financial checks before an exit can be proposed.
    """
    if type(frame) is not pl.DataFrame or any(c not in frame.columns for c in REQUIRED):
        raise ValueError("Liquidity compiler requires its exact producer columns")
    if (any(frame.schema[c] != pl.String for c in SOURCE_KEYS)
            or not frame.schema["boundary_ms"].is_integer()
            or not frame.schema["trade_count"].is_integer()):
        raise ValueError("Liquidity compiler requires typed source keys and integer observations")
    invalid = pl.any_horizontal(
        *(pl.col(c).is_null() | (pl.col(c).str.len_chars() == 0) for c in SOURCE_KEYS),
        pl.col("boundary_ms").is_null(), (pl.col("boundary_ms") <= 0),
        (pl.col("boundary_ms") > 57_600_000), (pl.col("boundary_ms") % 5000 != 0),
        (pl.col("trade_count") < 0).fill_null(False),
    )
    if frame.select(invalid.any()).item():
        raise ValueError("Liquidity compiler has invalid source identity, clock or activity")
    maximum = frame.select(pl.col("trade_count").max()).item()
    if maximum is not None and maximum > MAX_TRADE_COUNT:
        raise ValueError("Liquidity compiler trade count exceeds native UInt64 authority")
    if frame.select(pl.struct(*SOURCE_KEYS, "boundary_ms").is_duplicated().any()).item():
        raise ValueError("Liquidity compiler repeats a producer candle")
    # Internal names never shadow producer columns. No source row is silently
    # overwritten, normalized to zero, filtered away or deduplicated.
    outputs = tuple(f"trade_count_{i}" for i in range(4)) + (
        "activity_window_complete", "prior_10s_trade_count", "recent_10s_trade_count", "liquidity_fade")
    if any(c.startswith("_liquidity_") or c in outputs for c in frame.columns):
        raise ValueError("Liquidity compiler cannot overwrite existing source columns")
    f = frame.with_row_index("_liquidity_order").sort([*SOURCE_KEYS, "boundary_ms"])
    # Four lag columns each have shape (n_rows,). Group keys prevent a later
    # attempt or another session from filling gaps in this attempt's window.
    f = f.with_columns(
        *[pl.col("trade_count").shift(3-i).over(SOURCE_KEYS).alias(f"trade_count_{i}")
          for i in range(4)],
        pl.col("boundary_ms").shift(3).over(SOURCE_KEYS).alias("_liquidity_oldest"),
    )
    f = f.with_columns(
        ((pl.col("boundary_ms") - pl.col("_liquidity_oldest") == 15_000)
         & pl.all_horizontal(*(pl.col(f"trade_count_{i}").is_not_null() for i in range(4))))
        .fill_null(False).alias("activity_window_complete"),
        # Decimal integer arithmetic covers sums/multiples of UInt64 counts
        # without unsigned overflow or floating ratio-boundary rounding.
        (pl.col("trade_count_0").cast(pl.Decimal(38, 0))
         + pl.col("trade_count_1").cast(pl.Decimal(38, 0))).alias("prior_10s_trade_count"),
        (pl.col("trade_count_2").cast(pl.Decimal(38, 0))
         + pl.col("trade_count_3").cast(pl.Decimal(38, 0))).alias("recent_10s_trade_count"),
    )
    return f.with_columns(
        (pl.col("activity_window_complete") & (pl.col("prior_10s_trade_count") > 0)
         & (4 * pl.col("recent_10s_trade_count") <= pl.col("prior_10s_trade_count")))
        .fill_null(False).alias("liquidity_fade"),
    ).sort("_liquidity_order").drop("_liquidity_order", "_liquidity_oldest")
