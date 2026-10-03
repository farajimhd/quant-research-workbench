"""Producer-only vectorized windows from complete certified sparse 30s bars."""
from uuid import uuid5, NAMESPACE_URL
import polars as pl


def derive_windows(bars, *, ticker, session_end_ms, build, day, attempt):
    if session_end_ms <= 0 or session_end_ms % 30000:
        raise ValueError("Strategy 45 requires aligned session bounds")
    if bars.height and (bars["boundary_ms"].n_unique() != bars.height
            or bars.filter((pl.col("boundary_ms") <= 0) | (pl.col("boundary_ms") > session_end_ms)
                           | (pl.col("boundary_ms") % 30000 != 0)).height):
        raise ValueError("Duplicate or out-of-window published 30s candle")
    if bars.height and bars.filter(pl.any_horizontal(
            *[pl.col(k).is_null() | ~pl.col(k).is_finite() | (pl.col(k) < 0) for k in ("volume", "trade_count")])
            | (pl.col("trade_count") != pl.col("trade_count").floor())).height:
        raise ValueError("Invalid published 30s activity")
    grid = pl.DataFrame({"boundary_ms": range(30000, session_end_ms + 1, 30000)})
    frame = grid.join(bars, on="boundary_ms", how="left", validate="1:1").sort("boundary_ms").with_columns(
        pl.col("volume").fill_null(0), pl.col("trade_count").fill_null(0).cast(pl.UInt64))
    frame = frame.with_columns(
        pl.col("trade_count").rolling_sum(2, min_samples=2).alias("trades_60s"),
        pl.col("volume").rolling_sum(10, min_samples=10).alias("volume_300s"),
        (pl.col("boundary_ms") >= 300000).cast(pl.UInt8).alias("history_ready"))
    ids = [str(uuid5(NAMESPACE_URL, f"strategy-45-liquidity:{build}:{day}:{ticker}:{attempt}:{boundary}"))
           for boundary in frame["boundary_ms"]]
    return frame.select("boundary_ms", "history_ready", "trades_60s", "volume_300s").with_columns(
        pl.Series("fact_id", ids), pl.lit(build).alias("source_build_id"),
        pl.lit(day).alias("session_date"), pl.lit(ticker).alias("ticker"),
        pl.lit(attempt).alias("derivation_attempt_id"))
