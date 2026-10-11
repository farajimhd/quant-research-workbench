"""Producer-owned vectorized history over certified dense one-second bars.

This reusable calculation belongs in QMD History materialization, never a
strategy or Backtest callback. Acquisition, coverage publication and immutable
source-attempt certification stay with the existing producer funnel. It accepts
no research files, broker prices, execution state or historical flatfiles.
"""
from __future__ import annotations

import re
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import polars as pl

SWING_WINDOWS = (1, 2, 3, 4, 5, 10, 15, 20, 30, 45, 60)


def _trailing(values, width, *, maximum=False, current=False):
    padded = np.concatenate((np.full(width - int(current), np.nan), values))
    rows = sliding_window_view(padded, width)[:len(values)]
    return np.max(rows, axis=1) if maximum else np.sum(rows[:, ::-1], axis=1) / width


def prepare_causal_history(frame: pl.DataFrame, names: tuple[str, ...]) -> pl.DataFrame:
    """One complete bounded listing segment, including every elapsed second.

    Source-known and reference-close are explicit producer projections of the
    latest certified market row. Empty price buckets remain missing in elapsed
    history, while empty activity contributes zero. Temporal rule evaluation
    must retain elapsed clocks, including empty seconds.
    """
    required = {"listing_id", "boundary_at", "available_at", "observed", "extremes_valid",
        "source_known", "reference_close", "close", "high", "low", "notional"}
    if not required <= set(frame.columns) or not 1 <= frame.height <= 262144:
        raise ValueError("History producer needs bounded certified dense bar columns")
    if not names or len(names) > 664 or len(set(names)) != len(names):
        raise ValueError("History requests must be bounded and unique")
    if frame["listing_id"].null_count() or frame["listing_id"].n_unique() != 1:
        raise ValueError("History producer requires one complete listing segment")
    for name in ("boundary_at", "available_at"):
        if not isinstance(frame.schema[name], pl.Datetime) or frame.schema[name].time_zone is None:
            raise ValueError("History producer requires timezone-aware clocks")
        if frame[name].null_count():
            raise ValueError("History producer has missing clocks")
    # Cast clocks to microseconds explicitly, independent of Polars storage unit.
    clocks = frame["boundary_at"].dt.cast_time_unit("us").cast(pl.Int64).to_numpy()
    if (np.any(clocks % 1000000) or np.any(np.diff(clocks) != 1000000)
            or frame.filter(pl.col("available_at") > pl.col("boundary_at")).height):
        raise ValueError("History producer has gaps, unordered or future clocks")
    for name in ("observed", "extremes_valid", "source_known"):
        if frame.schema[name] != pl.Boolean or frame[name].null_count():
            raise ValueError("History observation/known masks must be explicit Boolean")
    observed = frame["observed"].to_numpy()
    extremes = frame["extremes_valid"].to_numpy() & observed
    known = frame["source_known"].to_numpy()
    arrays = {name: frame[name].cast(pl.Float64).to_numpy() for name in (
        "close", "high", "low", "notional", "reference_close")}
    notional = arrays["notional"]
    if not np.isfinite(notional).all() or np.any(notional < 0) or np.any((~known) & (notional != 0)):
        raise ValueError("History activity must be finite nonnegative and source-known")
    for name, mask in (("close", observed), ("high", extremes), ("low", extremes)):
        if np.any(mask & (~np.isfinite(arrays[name]) | (arrays[name] <= 0))):
            raise ValueError("Eligible history prices must be finite and positive")
        arrays[name] = np.where(mask, arrays[name], np.nan)
    # Last-price and extrema eligibility are independently certified. A
    # last-only closing trade may legitimately lie outside eligible extrema.
    if np.any(extremes & (arrays["low"] > arrays["high"])):
        raise ValueError("History extrema bounds differ")
    if np.any(observed & ~known):
        raise ValueError("Observed history bucket has no certified source row")
    mark, high, low = (arrays[name] for name in ("close", "high", "low"))
    output, cache = [], {}
    for name in names:
        relative = name.endswith(".relative_to_close")
        raw_name = name.removesuffix(".relative_to_close")
        match = re.fullmatch(r"history\.(recent_high|momentum_close|attention_mean|average_move)\.(\d+)", raw_name)
        swing = re.fullmatch(r"history\.swing_low\.(\d+)\.(\d+)", raw_name)
        if raw_name not in cache:
            if match:
                kind, width = match.group(1), int(match.group(2))
                if not 1 <= width <= 60 or (kind == "average_move" and not 2 <= width <= 32):
                    raise ValueError("History window is outside shared producer contract")
                if kind == "recent_high":
                    values = _trailing(high, width, maximum=True)
                elif kind == "attention_mean":
                    values = _trailing(notional, width)
                elif kind == "average_move":
                    movement = np.abs(mark - np.concatenate(([np.nan], mark[:-1])))
                    values = _trailing(movement, width, current=True)
                else:
                    values = np.full(frame.height, np.nan)
                    if width < frame.height:
                        values[width:] = mark[:-width]
            elif swing:
                left, right = map(int, swing.groups())
                if left not in SWING_WINDOWS or right not in SWING_WINDOWS:
                    raise ValueError("Swing window is outside shared producer contract")
                width = left + right + 1
                rows = sliding_window_view(np.concatenate((np.full(width - 1, np.nan), low)), width)
                pivot = rows[:, left]
                confirmed = np.isfinite(rows).all(axis=1) & (pivot == np.min(rows, axis=1))
                latest = np.maximum.accumulate(np.where(confirmed, np.arange(frame.height), -1))
                prior = np.concatenate(([-1], latest[:-1]))
                values = np.where(prior >= 0, pivot[prior.clip(0)], np.nan)
            else:
                raise ValueError("Unknown shared causal history field")
            cache[raw_name] = values
        values = cache[raw_name]
        if relative:
            if match and match.group(1) == "attention_mean":
                raise ValueError("Dollar activity has no relative-to-price history field")
            # Search first rounds raw history to float32, promotes that rounded
            # value for relative arithmetic, then rounds the result to float32.
            with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
                values = (values.astype(np.float32).astype(np.float64) / arrays["reference_close"] - 1).astype(np.float32)
            valid = np.isfinite(values) & known & (arrays["reference_close"] > 0)
        else:
            valid = np.isfinite(values) & known
        output.extend((pl.Series(name, values), pl.Series(name + "__valid", valid)))
    return frame.select("listing_id", "boundary_at", "available_at").with_columns(output)
