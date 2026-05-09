from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import polars as pl


FIXED_HORIZONS_MINUTES = [5, 10, 15, 30, 60, 120]
METHOD_WINDOWS = {
    "SCALP": (1, 10),
    "MOMENTUM_SCALP": (5, 30),
    "DAY_TRADE": (30, None),
    "SWING_TECHNICAL": (390, 20 * 390),
    "MEAN_REVERSION_LONG": (390, 60 * 390),
}


def step_minutes_from_frame(frame: pl.DataFrame) -> int:
    timeframe = str(frame["timeframe"][0]) if "timeframe" in frame.columns and frame.height else "1m"
    if timeframe.endswith("m"):
        return max(1, int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return max(1, int(timeframe[:-1]) * 60)
    if timeframe == "1d":
        return 390
    return 390 * 21


def _future_window(rows: list[dict], index: int, min_bars: int, max_bars: int | None) -> list[dict]:
    start = index + min_bars
    if start >= len(rows):
        return []
    end = len(rows) if max_bars is None else min(len(rows), index + max_bars + 1)
    return rows[start:end]


def _quality(entry_price: float, best_return: float, mae: float, efficiency: float) -> float:
    risk_penalty = min(abs(mae) * 20.0, 1.0)
    return max(0.0, min(1.0, (best_return * 20.0 * 0.45) + (efficiency * 0.35) + ((1.0 - risk_penalty) * 0.20)))


def _path_efficiency(entry: float, future: list[dict], exit_index: int) -> float:
    if not future or exit_index <= 0:
        return 0.0
    closes = [entry] + [float(row["close"]) for row in future[: exit_index + 1]]
    distance = abs(closes[-1] - closes[0])
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return distance / path if path > 0 else 0.0


def build_bar_supervision(frame: pl.DataFrame, horizons_minutes: Iterable[int] = FIXED_HORIZONS_MINUTES) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame()
    step = step_minutes_from_frame(frame)
    rows_out = []
    for ticker, ticker_frame in frame.sort(["ticker", "bar_time_utc"]).group_by("ticker", maintain_order=True):
        rows = ticker_frame.to_dicts()
        for index, row in enumerate(rows):
            entry = float(row["close"])
            for horizon_minutes in horizons_minutes:
                max_bars = max(1, int(horizon_minutes / step))
                future = _future_window(rows, index, 1, max_bars)
                valid = bool(future)
                highs = [float(item["high"]) for item in future]
                lows = [float(item["low"]) for item in future]
                closes = [float(item["close"]) for item in future]
                best_high = max(highs) if highs else entry
                worst_low = min(lows) if lows else entry
                best_index = highs.index(best_high) if highs else 0
                worst_index = lows.index(worst_low) if lows else 0
                mfe = (best_high / entry) - 1.0 if entry else 0.0
                mae = (worst_low / entry) - 1.0 if entry else 0.0
                efficiency = _path_efficiency(entry, future, best_index)
                rows_out.append(
                    {
                        "bar_id": row["bar_id"],
                        "ticker": row["ticker"],
                        "timeframe": row["timeframe"],
                        "bar_time_utc": row["bar_time_utc"],
                        "bar_time_market": row["bar_time_market"],
                        "session_date": row["session_date"],
                        "horizon": f"{horizon_minutes}m",
                        "horizon_minutes": horizon_minutes,
                        "future_bar_count": len(future),
                        "valid_future_window": valid,
                        "fwd_close_return": (closes[-1] / entry) - 1.0 if closes and entry else 0.0,
                        "fwd_high_return": mfe,
                        "fwd_low_return": mae,
                        "fwd_mfe": mfe,
                        "fwd_mae": mae,
                        "fwd_mfe_to_mae_ratio": mfe / abs(mae) if mae else 0.0,
                        "time_to_mfe_bars": best_index + 1 if future else None,
                        "time_to_mae_bars": worst_index + 1 if future else None,
                        "time_to_mfe_minutes": (best_index + 1) * step if future else None,
                        "time_to_mae_minutes": (worst_index + 1) * step if future else None,
                        "mfe_before_mae": best_index <= worst_index if future else None,
                        "oracle_best_exit_bar_id": future[best_index]["bar_id"] if future else None,
                        "oracle_best_exit_time_utc": future[best_index]["bar_time_utc"] if future else None,
                        "oracle_best_exit_price": best_high,
                        "oracle_best_exit_return": mfe,
                        "oracle_long_entry_signal": valid and mfe >= 0.01 and abs(mae) <= 0.005 and best_index <= worst_index,
                        "oracle_long_entry_confidence": _quality(entry, mfe, mae, efficiency),
                        "oracle_long_exit_signal": valid and mfe <= abs(mae),
                        "oracle_long_exit_confidence": _quality(entry, abs(mae), -mfe, 1.0 - efficiency),
                        "path_efficiency": efficiency,
                        "green_bar_ratio": sum(1 for item in future if float(item["close"]) >= float(item["open"])) / len(future) if future else 0.0,
                    }
                )
    return pl.DataFrame(rows_out, infer_schema_length=None) if rows_out else pl.DataFrame()


def build_method_supervision(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame()
    step = step_minutes_from_frame(frame)
    rows_out = []
    for _, ticker_frame in frame.sort(["ticker", "bar_time_utc"]).group_by("ticker", maintain_order=True):
        rows = ticker_frame.to_dicts()
        for index, row in enumerate(rows):
            entry = float(row["close"])
            for method, (min_minutes, max_minutes) in METHOD_WINDOWS.items():
                min_bars = max(1, int(min_minutes / step))
                max_bars = None if max_minutes is None else max(1, int(max_minutes / step))
                future = _future_window(rows, index, min_bars, max_bars)
                highs = [float(item["high"]) for item in future]
                lows = [float(item["low"]) for item in future]
                if highs:
                    best_high = max(highs)
                    best_index = highs.index(best_high)
                    worst_before_best = min(lows[: best_index + 1]) if lows else entry
                    best_return = (best_high / entry) - 1.0 if entry else 0.0
                    mae_before_best = (worst_before_best / entry) - 1.0 if entry else 0.0
                    efficiency = _path_efficiency(entry, future, best_index)
                    confidence = _quality(entry, best_return, mae_before_best, efficiency)
                    action = "ENTER_NOW" if confidence >= 0.65 and best_return > 0.005 else "WATCH" if confidence >= 0.45 else "IGNORE"
                else:
                    best_high = entry
                    best_index = 0
                    best_return = 0.0
                    mae_before_best = 0.0
                    efficiency = 0.0
                    confidence = 0.0
                    action = "IGNORE"
                rows_out.append(
                    {
                        "bar_id": row["bar_id"],
                        "ticker": row["ticker"],
                        "timeframe": row["timeframe"],
                        "bar_time_utc": row["bar_time_utc"],
                        "bar_time_market": row["bar_time_market"],
                        "session_date": row["session_date"],
                        "trade_method": method,
                        "method_min_horizon_minutes": min_minutes,
                        "method_max_horizon_minutes": max_minutes,
                        "valid_future_window": bool(future),
                        "method_best_exit_bar_id": future[best_index]["bar_id"] if future else None,
                        "method_best_exit_time_utc": future[best_index]["bar_time_utc"] if future else None,
                        "method_best_horizon_bars": best_index + min_bars if future else None,
                        "method_best_horizon_minutes": (best_index + min_bars) * step if future else None,
                        "method_best_price": best_high,
                        "method_best_return": best_return,
                        "method_mae_before_best": mae_before_best,
                        "method_mfe_mae_ratio": best_return / abs(mae_before_best) if mae_before_best else 0.0,
                        "method_path_efficiency": efficiency,
                        "method_entry_signal": action == "ENTER_NOW",
                        "method_exit_signal": action == "IGNORE",
                        "method_confidence": confidence,
                        "oracle_action": action,
                    }
                )
    return pl.DataFrame(rows_out, infer_schema_length=None) if rows_out else pl.DataFrame()


def build_scanner_supervision(method_supervision: pl.DataFrame) -> pl.DataFrame:
    if method_supervision.is_empty():
        return pl.DataFrame()
    return (
        method_supervision.with_columns(
            pl.col("method_confidence").rank("dense", descending=True).over(["bar_time_utc", "trade_method"]).alias("oracle_rank"),
            pl.len().over(["bar_time_utc", "trade_method"]).alias("universe_size"),
        )
        .with_columns(
            (1.0 - ((pl.col("oracle_rank") - 1.0) / pl.max_horizontal(pl.col("universe_size") - 1.0, pl.lit(1.0)))).alias("oracle_percentile"),
            (pl.col("oracle_rank") <= 1).alias("is_top_1"),
            (pl.col("oracle_rank") <= 3).alias("is_top_3"),
            (pl.col("oracle_rank") <= 5).alias("is_top_5"),
            (pl.col("oracle_rank") <= 10).alias("is_top_10"),
        )
        .with_columns(
            (pl.col("oracle_percentile") >= 0.99).alias("is_top_1pct"),
            (pl.col("oracle_percentile") >= 0.95).alias("is_top_5pct"),
        )
        .select(
            "bar_id",
            "ticker",
            "timeframe",
            "bar_time_utc",
            "bar_time_market",
            "session_date",
            "trade_method",
            "universe_size",
            "oracle_rank",
            "oracle_percentile",
            "method_best_return",
            "method_mae_before_best",
            "method_best_horizon_minutes",
            "method_confidence",
            "oracle_action",
            "is_top_1",
            "is_top_3",
            "is_top_5",
            "is_top_10",
            "is_top_1pct",
            "is_top_5pct",
        )
    )
