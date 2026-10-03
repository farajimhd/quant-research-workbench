"""Producer-owned completed-second features for Strategy 43.

This is deliberately outside the Strategy and Backtest read path. Input is a
dense, certified one-second grid for ONE listing/session; missing observations
stay missing. Publication must seal the resulting rows and their input plan.
"""
from __future__ import annotations

import polars as pl

FACT_COLUMNS = (
    "boundary_ms", "observed", "close", "low", "high", "dollar_volume",
    "previous_five_second_close", "previous_ten_second_mean_notional",
    "swing_low", "swing_available_ms", "ten_second_mean_movement",
)


def derive_completed_features(seconds: pl.DataFrame) -> pl.DataFrame:
    """Match the research history clocks without looking beyond each row.

    Entry reads the swing and attention rings BEFORE their current-second
    update. Trailing reads movement AFTER that update. A tied five-second
    minimum is a confirmed pivot, matching the Torch kernel exactly.
    """
    required = {"boundary_ms", "observed", "close", "low", "high", "dollar_volume"}
    if not isinstance(seconds, pl.DataFrame) or not required <= set(seconds.columns):
        raise ValueError("Strategy 43 producer requires the typed dense second grid")
    if (seconds.height == 0 or seconds.schema["observed"] != pl.Boolean
            or not seconds.schema["boundary_ms"].is_integer()):
        raise ValueError("Strategy 43 producer grid types differ")
    clocks = seconds["boundary_ms"]
    if (seconds["observed"].null_count() or clocks.null_count() or clocks[0] != 1000
            or clocks[-1] > 57_600_000
            or not clocks.diff().drop_nulls().eq(1000).all()):
        raise ValueError("Strategy 43 producer needs consecutive session-relative seconds")
    frame = seconds.select(*required).with_columns(
        pl.when(pl.col("observed") & pl.col(name).is_finite())
        .then(pl.col(name).cast(pl.Float64)).otherwise(None).alias(name)
        for name in ("close", "low", "high")
    ).with_columns(
        pl.col("low").shift(2).alias("_pivot"),
        pl.col("low").rolling_min(5, min_samples=5).alias("_minimum"),
        pl.col("close").diff().abs().alias("_movement"),
        # Torch uses execution notional even for a quote-only/no-price bucket.
        pl.col("dollar_volume").cast(pl.Float64).fill_nan(0).fill_null(0).alias("dollar_volume"),
    ).with_columns(
        pl.when(pl.col("_pivot") == pl.col("_minimum"))
        .then(pl.col("_pivot")).otherwise(None).alias("_confirmed"),
        pl.when(pl.col("_pivot") == pl.col("_minimum"))
        .then(pl.col("boundary_ms")).otherwise(None).alias("_confirmed_at"),
    ).with_columns(
        pl.col("close").shift(5).alias("previous_five_second_close"),
        pl.col("dollar_volume").shift(1).rolling_mean(10, min_samples=10)
        .alias("previous_ten_second_mean_notional"),
        pl.col("_confirmed").forward_fill().shift(1).alias("swing_low"),
        pl.col("_confirmed_at").forward_fill().shift(1).fill_null(0)
        .cast(pl.Int64).alias("swing_available_ms"),
        pl.col("_movement").rolling_mean(10, min_samples=10)
        .alias("ten_second_mean_movement"),
    )
    return frame.select(*FACT_COLUMNS)
