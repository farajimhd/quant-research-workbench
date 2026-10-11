"""Producer-side elapsed-second volume for certified dense QMD inputs.

Coverage and publication remain the producer's responsibility. This transform
must receive every completed clock, including certified zero-volume clocks.
"""
import polars as pl


def elapsed_volume_history(frame: pl.DataFrame, window_seconds: int) -> pl.DataFrame:
    """Include the current completed second; never bridge a session boundary."""
    keys = ["listing_id", "session_date"]
    required = {*keys, "boundary_at", "available_at", "volume"}
    if (type(window_seconds) is not int or not 1 <= window_seconds <= 3600
            or not required <= set(frame.columns) or frame.height > 262_144):
        raise ValueError("Elapsed volume requires a bounded explicit window and dense inputs")
    for name in ("boundary_at", "available_at"):
        dtype = frame.schema[name]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
            raise ValueError("Elapsed volume clocks must be timezone-aware")
    if frame.schema["session_date"] != pl.Date:
        raise ValueError("Elapsed volume needs an explicit session date")
    if frame.schema["listing_id"] != pl.String or not frame.schema["volume"].is_numeric():
        raise ValueError("Elapsed volume requires listing identities and numeric shares")
    if frame.select(pl.any_horizontal(pl.col(k).is_null() for k in required).any()).item():
        raise ValueError("Elapsed volume cannot infer missing coverage")
    ordered = frame.sort([*keys, "boundary_at"])
    bad = ordered.select((
        pl.struct(*keys, "boundary_at").is_duplicated()
        | (pl.col("listing_id").str.len_chars() == 0)
        | (pl.col("available_at") > pl.col("boundary_at"))
        | ~pl.col("volume").is_finite() | (pl.col("volume") < 0)
        | (pl.col("volume") > 2 ** 53)
        | (pl.col("volume") != pl.col("volume").floor())
        | ((pl.col("boundary_at").diff().over(keys).dt.total_microseconds()
            != 1_000_000).fill_null(False))
        | (pl.col("boundary_at").dt.microsecond() != 0)
    ).any()).item()
    if bad:
        raise ValueError("Elapsed volume has future, duplicate, non-dense or invalid shares")
    result = ordered.with_columns(
        pl.col("volume").cast(pl.Float64).rolling_sum(
            window_size=window_seconds, min_samples=1).over(keys).alias("rolling_volume"),
        pl.lit(window_seconds).alias("volume_window_seconds"))
    if result.select((~pl.col("rolling_volume").is_finite()
                      | (pl.col("rolling_volume") > 2 ** 53)).any()).item():
        raise ValueError("Elapsed volume exceeds exact share-count range")
    return result
