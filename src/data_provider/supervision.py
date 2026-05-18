from __future__ import annotations

from typing import Callable, Iterable, Iterator

import polars as pl


FIXED_HORIZON_BARS = [1, 2, 3]
ORACLE_MAX_HORIZON_BARS = 60
METHOD_BAR_WINDOWS = {
    "PRICE_VOLUME_SHOCK": (1, 3),
    "SCALP": (1, 2),
    "MOMENTUM_SCALP": (2, 3),
}
BASE_COLUMNS = ["bar_id", "ticker", "timeframe", "bar_time_utc", "bar_time_market", "session_date"]


def step_minutes_from_frame(frame: pl.DataFrame) -> int:
    timeframe = str(frame["timeframe"][0]) if "timeframe" in frame.columns and frame.height else "1m"
    if timeframe.endswith("m"):
        return max(1, int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return max(1, int(timeframe[:-1]) * 60)
    if timeframe == "1d":
        return 390
    return 390 * 21


def _sorted(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.sort(["ticker", "bar_time_utc"])


def method_windows_for_timeframe(timeframe: str) -> dict[str, tuple[int, int]]:
    return METHOD_BAR_WINDOWS


def _future_list(column: str, offsets: range, *, default: pl.Expr | None = None) -> pl.Expr:
    values = [pl.col(column).shift(-offset).over("ticker") for offset in offsets]
    if default is not None:
        values = [value.fill_null(default) for value in values]
    return pl.concat_list(values)


def _bounded(value: pl.Expr, lower: float = 0.0, upper: float = 1.0) -> pl.Expr:
    return value.clip(lower, upper)


def _safe_ratio(numerator: pl.Expr, denominator: pl.Expr, default: float = 0.0) -> pl.Expr:
    return pl.when(denominator != 0).then(numerator / denominator).otherwise(default)


def _quality_expr(entry: pl.Expr, best_return: pl.Expr, mae: pl.Expr, efficiency: pl.Expr) -> pl.Expr:
    risk_penalty = pl.min_horizontal(mae.abs() * 20.0, pl.lit(1.0))
    return _bounded((best_return * 20.0 * 0.45) + (efficiency * 0.35) + ((1.0 - risk_penalty) * 0.20))


def _score_0_100(value: pl.Expr) -> pl.Expr:
    return (_bounded(value) * 100.0).round(2)


def _path_efficiency_expr(entry: pl.Expr, future_closes: pl.Expr, best_index: pl.Expr, best_price: pl.Expr) -> pl.Expr:
    close_path = future_closes.list.slice(0, best_index + 1)
    path = (
        (close_path.list.first() - entry).abs().fill_null(0.0)
        + close_path.list.diff().list.eval(pl.element().abs()).list.sum().fill_null(0.0)
    )
    return pl.when(path > 0).then((best_price - entry).abs() / path).otherwise(0.0)


def _future_volume_shock_list(offsets: range) -> pl.Expr:
    current_volume = pl.col("volume").fill_null(0.0)
    current_dollar_volume = pl.col("dollar_volume").fill_null(pl.col("close") * current_volume)
    values = []
    for offset in offsets:
        future_volume = pl.col("volume").shift(-offset).over("ticker").fill_null(0.0)
        future_dollar_volume = pl.col("dollar_volume").shift(-offset).over("ticker").fill_null(0.0)
        values.append(
            (
                (pl.col("volume_z20").shift(-offset).over("ticker").fill_null(0.0) >= 2.5)
                | (pl.col("relative_volume20").shift(-offset).over("ticker").fill_null(0.0) >= 3.0)
                | (pl.col("relative_dollar_volume20").shift(-offset).over("ticker").fill_null(0.0) >= 3.0)
                | ((current_volume > 0) & (future_volume >= current_volume * 3.0))
                | ((current_dollar_volume > 0) & (future_dollar_volume >= current_dollar_volume * 3.0))
            ).fill_null(False)
        )
    return pl.concat_list(values)


def _bar_horizon_frame(frame: pl.DataFrame, horizon_bars: int, step: int) -> pl.DataFrame:
    max_bars = max(1, int(horizon_bars))
    offsets = range(1, max_bars + 1)
    future_highs = _future_list("high", offsets)
    future_lows = _future_list("low", offsets)
    future_closes = _future_list("close", offsets)
    future_volumes = _future_list("volume", offsets, default=pl.lit(0.0))
    future_dollar_volumes = _future_list("dollar_volume", offsets, default=pl.lit(0.0))
    future_transactions = _future_list("transactions", offsets, default=pl.lit(0.0))
    future_relative_volumes = _future_list("relative_volume20", offsets, default=pl.lit(0.0))
    future_relative_dollar_volumes = _future_list("relative_dollar_volume20", offsets, default=pl.lit(0.0))
    future_volume_z = _future_list("volume_z20", offsets, default=pl.lit(0.0))
    future_bar_ids = _future_list("bar_id", offsets)
    future_times = _future_list("bar_time_utc", offsets)
    future_market_times = _future_list("bar_time_market", offsets)
    future_volume_shocks = _future_volume_shock_list(offsets)
    future_green = pl.concat_list(
        [
            (pl.col("close").shift(-offset).over("ticker") >= pl.col("open").shift(-offset).over("ticker"))
            .fill_null(False)
            .cast(pl.Int8)
            for offset in offsets
        ]
    )

    return (
        frame.with_columns(
            future_highs.alias("_future_highs"),
            future_lows.alias("_future_lows"),
            future_closes.alias("_future_closes"),
            future_volumes.alias("_future_volumes"),
            future_dollar_volumes.alias("_future_dollar_volumes"),
            future_transactions.alias("_future_transactions"),
            future_relative_volumes.alias("_future_relative_volumes"),
            future_relative_dollar_volumes.alias("_future_relative_dollar_volumes"),
            future_volume_z.alias("_future_volume_z"),
            future_bar_ids.alias("_future_bar_ids"),
            future_times.alias("_future_times"),
            future_market_times.alias("_future_market_times"),
            future_volume_shocks.alias("_future_volume_shocks"),
            future_green.alias("_future_green"),
        )
        .with_columns(
            pl.col("_future_closes").list.drop_nulls().list.len().alias("_future_count"),
            pl.coalesce(pl.col("_future_highs").list.max(), pl.col("close")).alias("_best_high"),
            pl.coalesce(pl.col("_future_lows").list.min(), pl.col("close")).alias("_worst_low"),
            pl.coalesce(pl.col("_future_highs").list.arg_max(), pl.lit(0)).alias("_best_index"),
            pl.coalesce(pl.col("_future_lows").list.arg_min(), pl.lit(0)).alias("_worst_index"),
            pl.col("_future_volume_shocks").list.any().alias("_has_volume_shock"),
            pl.col("_future_volume_shocks").list.arg_max().alias("_volume_shock_index_raw"),
        )
        .with_columns(
            ((pl.col("_best_high") / pl.col("close")) - 1.0).fill_nan(0.0).fill_null(0.0).alias("_mfe"),
            ((pl.col("_worst_low") / pl.col("close")) - 1.0).fill_nan(0.0).fill_null(0.0).alias("_mae"),
            pl.when(pl.col("_has_volume_shock")).then(pl.col("_volume_shock_index_raw")).otherwise(None).alias("_volume_shock_index"),
            pl.col("_future_closes").list.drop_nulls().list.last().alias("_last_future_close"),
        )
        .with_columns(
            _path_efficiency_expr(pl.col("close"), pl.col("_future_closes"), pl.col("_best_index"), pl.col("_best_high")).alias("_path_efficiency"),
            pl.col("_future_lows").list.slice(0, pl.col("_volume_shock_index") + 1).list.min().alias("_low_before_volume_shock"),
            pl.col("_future_closes").list.get(pl.col("_volume_shock_index"), null_on_oob=True).alias("_volume_shock_close"),
            pl.col("_future_volumes").list.max().fill_null(0.0).alias("_max_volume"),
            pl.col("_future_dollar_volumes").list.max().fill_null(0.0).alias("_max_dollar_volume"),
            pl.col("_future_relative_volumes").list.max().fill_null(0.0).alias("_max_relative_volume"),
            pl.col("_future_relative_dollar_volumes").list.max().fill_null(0.0).alias("_max_relative_dollar_volume"),
            pl.col("_future_volume_z").list.max().fill_null(0.0).alias("_max_volume_z"),
            pl.col("_future_volumes").list.sum().fill_null(0.0).alias("_volume_sum"),
            pl.col("_future_dollar_volumes").list.sum().fill_null(0.0).alias("_dollar_volume_sum"),
            pl.col("_future_transactions").list.sum().fill_null(0.0).alias("_transactions_sum"),
            pl.col("_future_bar_ids").list.get(pl.col("_best_index"), null_on_oob=True).alias("_best_bar_id"),
            pl.col("_future_times").list.get(pl.col("_best_index"), null_on_oob=True).alias("_best_time"),
            pl.col("_future_bar_ids").list.get(pl.col("_volume_shock_index"), null_on_oob=True).alias("_volume_shock_bar_id"),
            pl.col("_future_times").list.get(pl.col("_volume_shock_index"), null_on_oob=True).alias("_volume_shock_time"),
            pl.col("_future_market_times").list.get(pl.col("_volume_shock_index"), null_on_oob=True).alias("_volume_shock_market_time"),
            (pl.col("_future_green").list.sum() / pl.max_horizontal(pl.col("_future_count"), pl.lit(1))).fill_null(0.0).alias("_green_bar_ratio"),
        )
        .with_columns(
            (pl.col("_max_dollar_volume") * 0.01).alias("_estimated_capacity"),
            _safe_ratio(pl.col("_max_volume"), pl.col("volume")).alias("_volume_expansion_ratio"),
            _safe_ratio(pl.col("_max_dollar_volume"), pl.col("dollar_volume")).alias("_dollar_volume_expansion_ratio"),
        )
        .with_columns(
            pl.min_horizontal(pl.col("_estimated_capacity") / 25_000.0, pl.lit(1.0)).alias("_capacity_score")
        )
        .with_columns(
            _bounded(
                (pl.min_horizontal(pl.col("_max_relative_volume") / 5.0, pl.lit(1.0)) * 0.40)
                + (pl.min_horizontal(pl.max_horizontal(pl.col("_max_volume_z"), pl.lit(0.0)) / 4.0, pl.lit(1.0)) * 0.30)
                + (pl.col("_capacity_score") * 0.30)
            ).alias("_liquidity_quality"),
            _quality_expr(pl.col("close"), pl.col("_mfe"), pl.col("_mae"), pl.col("_path_efficiency")).alias("_price_quality"),
        )
        .select(
            *BASE_COLUMNS,
            pl.lit(f"{horizon_bars}bar").alias("horizon"),
            pl.lit(horizon_bars).alias("horizon_bars"),
            pl.lit(horizon_bars * step).alias("horizon_minutes"),
            pl.col("_future_count").alias("future_bar_count"),
            (pl.col("_future_count") > 0).alias("valid_future_window"),
            _safe_ratio(pl.col("_last_future_close"), pl.col("close")).sub(1.0).fill_null(0.0).alias("fwd_close_return"),
            pl.col("_mfe").alias("fwd_high_return"),
            pl.col("_mae").alias("fwd_low_return"),
            pl.col("_mfe").alias("fwd_mfe"),
            pl.col("_mae").alias("fwd_mae"),
            _safe_ratio(pl.col("_mfe"), pl.col("_mae").abs()).alias("fwd_mfe_to_mae_ratio"),
            pl.when(pl.col("_future_count") > 0).then(pl.col("_best_index") + 1).otherwise(None).alias("time_to_mfe_bars"),
            pl.when(pl.col("_future_count") > 0).then(pl.col("_worst_index") + 1).otherwise(None).alias("time_to_mae_bars"),
            pl.when(pl.col("_future_count") > 0).then((pl.col("_best_index") + 1) * step).otherwise(None).alias("time_to_mfe_minutes"),
            pl.when(pl.col("_future_count") > 0).then((pl.col("_worst_index") + 1) * step).otherwise(None).alias("time_to_mae_minutes"),
            pl.when(pl.col("_future_count") > 0).then(pl.col("_best_index") <= pl.col("_worst_index")).otherwise(None).alias("mfe_before_mae"),
            pl.col("_best_bar_id").alias("oracle_best_exit_bar_id"),
            pl.col("_best_time").alias("oracle_best_exit_time_utc"),
            pl.col("_best_high").alias("oracle_best_exit_price"),
            pl.col("_mfe").alias("oracle_best_exit_return"),
            ((pl.col("_future_count") > 0) & (pl.col("_mfe") >= 0.01) & (pl.col("_mae").abs() <= 0.005) & (pl.col("_best_index") <= pl.col("_worst_index"))).alias("oracle_long_entry_signal"),
            pl.col("_price_quality").alias("oracle_long_entry_confidence"),
            ((pl.col("_future_count") > 0) & (pl.col("_mfe") <= pl.col("_mae").abs())).alias("oracle_long_exit_signal"),
            _quality_expr(pl.col("close"), pl.col("_mae").abs(), -pl.col("_mfe"), 1.0 - pl.col("_path_efficiency")).alias("oracle_long_exit_confidence"),
            pl.col("_path_efficiency").alias("path_efficiency"),
            pl.col("_green_bar_ratio").alias("green_bar_ratio"),
            pl.col("_volume_sum").alias("fwd_volume_sum"),
            pl.col("_dollar_volume_sum").alias("fwd_dollar_volume_sum"),
            pl.col("_transactions_sum").alias("fwd_transactions_sum"),
            pl.col("_max_volume").alias("fwd_max_volume"),
            pl.col("_max_dollar_volume").alias("fwd_max_dollar_volume"),
            pl.col("_max_relative_volume").alias("fwd_max_relative_volume20"),
            pl.col("_max_relative_dollar_volume").alias("fwd_max_relative_dollar_volume20"),
            pl.col("_max_volume_z").alias("fwd_max_volume_z20"),
            pl.col("_volume_expansion_ratio").alias("fwd_volume_expansion_ratio"),
            pl.col("_dollar_volume_expansion_ratio").alias("fwd_dollar_volume_expansion_ratio"),
            pl.col("_has_volume_shock").alias("fwd_liquidity_confirmed"),
            pl.col("_volume_shock_bar_id").alias("fwd_first_volume_shock_bar_id"),
            pl.col("_volume_shock_time").alias("fwd_first_volume_shock_time_utc"),
            pl.col("_volume_shock_market_time").alias("fwd_first_volume_shock_time_market"),
            pl.when(pl.col("_has_volume_shock")).then((pl.col("_volume_shock_index") + 1) * step).otherwise(None).alias("fwd_minutes_to_volume_shock"),
            pl.when(pl.col("_has_volume_shock")).then(pl.col("_volume_shock_index") <= pl.col("_best_index")).otherwise(None).alias("fwd_volume_shock_before_mfe"),
            pl.when(pl.col("_has_volume_shock")).then(_safe_ratio(pl.col("_volume_shock_close"), pl.col("close")).sub(1.0)).otherwise(None).alias("fwd_return_at_volume_shock"),
            pl.when(pl.col("_has_volume_shock")).then(_safe_ratio(pl.col("_low_before_volume_shock"), pl.col("close")).sub(1.0)).otherwise(None).alias("fwd_drawdown_before_volume_shock"),
            pl.col("_estimated_capacity").alias("fwd_estimated_capacity_dollars"),
            pl.col("_capacity_score").alias("fwd_capacity_score"),
            pl.col("_price_quality").alias("fwd_price_outcome_quality"),
            pl.col("_liquidity_quality").alias("fwd_liquidity_quality_score"),
            pl.when((pl.col("_price_quality") >= 0.60) & (pl.col("_liquidity_quality") >= 0.60))
            .then(pl.lit("good_price_good_volume"))
            .when(pl.col("_price_quality") >= 0.60)
            .then(pl.lit("good_price_bad_volume"))
            .when(pl.col("_liquidity_quality") >= 0.60)
            .then(pl.lit("bad_price_good_volume"))
            .otherwise(pl.lit("bad_price_bad_volume"))
            .alias("fwd_outcome_bucket"),
        )
    )


def build_bar_supervision(frame: pl.DataFrame, horizons_bars: Iterable[int] = FIXED_HORIZON_BARS) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame()
    frames = [horizon_frame for _, horizon_frame in iter_bar_supervision_frames(frame, horizons_bars, assume_sorted=False)]
    return pl.concat(frames, how="diagonal") if frames else pl.DataFrame()


def build_oracle_supervision(frame: pl.DataFrame, *, max_horizon_bars: int = ORACLE_MAX_HORIZON_BARS, assume_sorted: bool = False) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame()
    sorted_frame = frame if assume_sorted else _sorted(frame)
    step = step_minutes_from_frame(sorted_frame)
    max_future_bars = max(1, min(int(max_horizon_bars), _max_future_bars(sorted_frame)))
    offsets = range(1, max_future_bars + 1)
    future_highs = _future_list("high", offsets)
    future_lows = _future_list("low", offsets)
    future_closes = _future_list("close", offsets)
    future_bar_ids = _future_list("bar_id", offsets)
    future_times = _future_list("bar_time_utc", offsets)

    metrics = (
        sorted_frame.with_columns(
            future_highs.alias("_future_highs"),
            future_lows.alias("_future_lows"),
            future_closes.alias("_future_closes"),
            future_bar_ids.alias("_future_bar_ids"),
            future_times.alias("_future_times"),
        )
        .with_columns(
            pl.col("_future_closes").list.drop_nulls().list.len().alias("_future_count"),
            pl.coalesce(pl.col("_future_highs").list.max(), pl.col("close")).alias("_long_best_price"),
            pl.coalesce(pl.col("_future_highs").list.arg_max(), pl.lit(0)).alias("_long_best_index"),
            pl.coalesce(pl.col("_future_lows").list.min(), pl.col("close")).alias("_short_best_price"),
            pl.coalesce(pl.col("_future_lows").list.arg_min(), pl.lit(0)).alias("_short_best_index"),
        )
        .with_columns(
            pl.col("_future_lows").list.slice(0, pl.col("_long_best_index") + 1).list.min().alias("_long_worst_before_best"),
            pl.col("_future_highs").list.slice(0, pl.col("_short_best_index") + 1).list.max().alias("_short_worst_before_best"),
            pl.col("_future_bar_ids").list.get(pl.col("_long_best_index"), null_on_oob=True).alias("_long_best_bar_id"),
            pl.col("_future_times").list.get(pl.col("_long_best_index"), null_on_oob=True).alias("_long_best_time"),
            pl.col("_future_bar_ids").list.get(pl.col("_short_best_index"), null_on_oob=True).alias("_short_best_bar_id"),
            pl.col("_future_times").list.get(pl.col("_short_best_index"), null_on_oob=True).alias("_short_best_time"),
        )
        .with_columns(
            _safe_ratio(pl.col("_long_best_price"), pl.col("close")).sub(1.0).fill_nan(0.0).fill_null(0.0).alias("_long_profit"),
            _safe_ratio(pl.col("close"), pl.col("_short_best_price")).sub(1.0).fill_nan(0.0).fill_null(0.0).alias("_short_profit"),
            _safe_ratio(pl.col("_long_worst_before_best"), pl.col("close")).sub(1.0).fill_nan(0.0).fill_null(0.0).alias("_long_drawdown"),
            _safe_ratio(pl.col("_short_worst_before_best"), pl.col("close")).sub(1.0).fill_nan(0.0).fill_null(0.0).alias("_short_adverse"),
        )
        .with_columns(
            _path_efficiency_expr(pl.col("close"), pl.col("_future_closes"), pl.col("_long_best_index"), pl.col("_long_best_price")).alias("_long_efficiency"),
            _path_efficiency_expr(pl.col("close"), pl.col("_future_closes"), pl.col("_short_best_index"), pl.col("_short_best_price")).alias("_short_efficiency"),
            _bounded((pl.col("_long_best_index") + 1).cast(pl.Float64) / 12.0).alias("_long_time_quality"),
            _bounded((pl.col("_short_best_index") + 1).cast(pl.Float64) / 12.0).alias("_short_time_quality"),
        )
        .with_columns(
            _bounded(
                (_bounded(pl.col("_long_profit") / 0.08) * 0.48)
                + (_bounded(1.0 - (pl.col("_long_drawdown").abs() / 0.025)) * 0.22)
                + (pl.col("_long_efficiency") * 0.18)
                + (pl.col("_long_time_quality") * 0.12)
            ).alias("_long_enter_quality"),
            _bounded(
                (_bounded(pl.col("_short_profit") / 0.08) * 0.48)
                + (_bounded(1.0 - (pl.col("_short_adverse") / 0.025)) * 0.22)
                + (pl.col("_short_efficiency") * 0.18)
                + (pl.col("_short_time_quality") * 0.12)
            ).alias("_short_enter_quality"),
        )
        .with_columns(
            _bounded(
                (_bounded(pl.col("_short_profit") / 0.05) * 0.46)
                + (_bounded(1.0 - (pl.max_horizontal(pl.col("_long_profit"), pl.lit(0.0)) / 0.012)) * 0.34)
                + (_bounded(pl.col("_short_time_quality")) * 0.20)
            ).alias("_long_exit_quality"),
            _bounded(
                (_bounded(pl.col("_long_profit") / 0.05) * 0.46)
                + (_bounded(1.0 - (pl.max_horizontal(pl.col("_short_profit"), pl.lit(0.0)) / 0.012)) * 0.34)
                + (_bounded(pl.col("_long_time_quality")) * 0.20)
            ).alias("_short_exit_quality"),
        )
        .with_columns(
            _score_0_100(pl.col("_long_enter_quality")).alias("oracle_long_enter_score"),
            _score_0_100(pl.col("_long_exit_quality")).alias("oracle_long_exit_score"),
            _score_0_100(pl.col("_short_enter_quality")).alias("oracle_short_enter_score"),
            _score_0_100(pl.col("_short_exit_quality")).alias("oracle_short_exit_score"),
        )
    )

    long_exit_points = (
        metrics.filter(
            (pl.col("_future_count") > 0)
            & pl.col("_long_best_bar_id").is_not_null()
            & (pl.col("_long_profit") >= 0.008)
            & (pl.col("oracle_long_enter_score") >= 45.0)
        )
        .sort(["_long_best_bar_id", "_long_profit", "oracle_long_enter_score"], descending=[False, True, True])
        .unique("_long_best_bar_id", keep="first", maintain_order=True)
        .select(
            pl.col("_long_best_bar_id").alias("bar_id"),
            pl.col("bar_id").alias("_long_exit_entry_bar_id"),
            pl.col("bar_time_utc").alias("_long_exit_entry_time"),
            pl.col("_long_profit").alias("_long_exit_realized_profit"),
            pl.col("_long_drawdown").alias("_long_exit_realized_drawdown"),
            (pl.col("_long_best_index") + 1).alias("_long_exit_realized_horizon_bars"),
            pl.max_horizontal(pl.col("oracle_long_enter_score"), pl.col("oracle_long_exit_score")).alias("_long_exit_realized_score"),
        )
    )
    short_exit_points = (
        metrics.filter(
            (pl.col("_future_count") > 0)
            & pl.col("_short_best_bar_id").is_not_null()
            & (pl.col("_short_profit") >= 0.008)
            & (pl.col("oracle_short_enter_score") >= 45.0)
        )
        .sort(["_short_best_bar_id", "_short_profit", "oracle_short_enter_score"], descending=[False, True, True])
        .unique("_short_best_bar_id", keep="first", maintain_order=True)
        .select(
            pl.col("_short_best_bar_id").alias("bar_id"),
            pl.col("bar_id").alias("_short_exit_entry_bar_id"),
            pl.col("bar_time_utc").alias("_short_exit_entry_time"),
            pl.col("_short_profit").alias("_short_exit_realized_profit"),
            pl.col("_short_adverse").alias("_short_exit_realized_adverse"),
            (pl.col("_short_best_index") + 1).alias("_short_exit_realized_horizon_bars"),
            pl.max_horizontal(pl.col("oracle_short_enter_score"), pl.col("oracle_short_exit_score")).alias("_short_exit_realized_score"),
        )
    )

    return (
        metrics.join(long_exit_points, on="bar_id", how="left")
        .join(short_exit_points, on="bar_id", how="left")
        .with_columns(
            pl.coalesce(pl.col("_long_exit_realized_profit"), pl.lit(0.0)).alias("_long_exit_realized_profit"),
            pl.coalesce(pl.col("_short_exit_realized_profit"), pl.lit(0.0)).alias("_short_exit_realized_profit"),
            pl.coalesce(pl.col("_long_exit_realized_score"), pl.lit(0.0)).alias("_long_exit_realized_score"),
            pl.coalesce(pl.col("_short_exit_realized_score"), pl.lit(0.0)).alias("_short_exit_realized_score"),
            pl.coalesce(pl.col("_long_exit_realized_drawdown"), pl.lit(0.0)).alias("_long_exit_realized_drawdown"),
            pl.coalesce(pl.col("_short_exit_realized_adverse"), pl.lit(0.0)).alias("_short_exit_realized_adverse"),
            pl.coalesce(pl.col("_long_exit_realized_horizon_bars"), pl.lit(0)).alias("_long_exit_realized_horizon_bars"),
            pl.coalesce(pl.col("_short_exit_realized_horizon_bars"), pl.lit(0)).alias("_short_exit_realized_horizon_bars"),
            pl.max_horizontal(pl.col("oracle_long_exit_score"), pl.col("_long_exit_realized_score")).alias("oracle_long_exit_score"),
            pl.max_horizontal(pl.col("oracle_short_exit_score"), pl.col("_short_exit_realized_score")).alias("oracle_short_exit_score"),
        )
        .with_columns(
            (
                (pl.col("_future_count") > 0)
                & (pl.col("_long_profit") >= 0.008)
                & (pl.col("oracle_long_enter_score") >= 65.0)
                & (pl.col("_long_best_index") >= 2)
                & (pl.col("oracle_long_enter_score") >= pl.col("oracle_short_enter_score") * 0.95)
            ).alias("_long_enter"),
            (
                (pl.col("_future_count") > 0)
                & (pl.col("_short_profit") >= 0.008)
                & (pl.col("oracle_short_enter_score") >= 65.0)
                & (pl.col("_short_best_index") >= 2)
                & (pl.col("oracle_short_enter_score") > pl.col("oracle_long_enter_score") * 1.05)
            ).alias("_short_enter"),
            (
                (pl.col("_long_exit_realized_profit") >= 0.008)
                & (pl.col("_long_exit_realized_score") >= 45.0)
            ).alias("_long_exit"),
            (
                (pl.col("_short_exit_realized_profit") >= 0.008)
                & (pl.col("_short_exit_realized_score") >= 45.0)
            ).alias("_short_exit"),
        )
        .with_columns(
            ((pl.col("oracle_short_enter_score") >= 35.0) & (pl.col("_short_profit") >= 0.003) & ~pl.col("_short_exit") & (pl.col("oracle_short_enter_score") > pl.col("oracle_long_enter_score") * 1.05)).alias("_short_hold"),
            ((pl.col("oracle_long_enter_score") >= 35.0) & (pl.col("_long_profit") >= 0.003) & ~pl.col("_long_exit")).alias("_long_hold"),
        )
        .with_columns(
            pl.when(pl.col("_long_exit"))
            .then(pl.lit("LONG"))
            .when(pl.col("_short_exit"))
            .then(pl.lit("SHORT"))
            .when(pl.col("_short_enter"))
            .then(pl.lit("SHORT"))
            .when(pl.col("_short_hold"))
            .then(pl.lit("SHORT"))
            .when(pl.col("_long_enter") | pl.col("_long_hold"))
            .then(pl.lit("LONG"))
            .otherwise(pl.lit("NONE"))
            .alias("desired_method"),
            pl.when(pl.col("_long_exit") | pl.col("_short_exit"))
            .then(pl.lit("EXIT"))
            .when(pl.col("_short_enter") | pl.col("_long_enter"))
            .then(pl.lit("ENTER"))
            .when(pl.col("_short_hold") | pl.col("_long_hold"))
            .then(pl.lit("HOLD"))
            .otherwise(pl.lit("AVOID"))
            .alias("signal"),
        )
        .with_columns(
            pl.when(pl.col("desired_method") == "LONG")
            .then(pl.when(pl.col("signal") == "EXIT").then(pl.col("_long_exit_realized_score")).otherwise(pl.col("oracle_long_enter_score")))
            .when(pl.col("desired_method") == "SHORT")
            .then(pl.when(pl.col("signal") == "EXIT").then(pl.col("_short_exit_realized_score")).otherwise(pl.col("oracle_short_enter_score")))
            .otherwise(pl.max_horizontal(pl.col("oracle_long_enter_score"), pl.col("oracle_short_enter_score")))
            .alias("score"),
            pl.when((pl.col("desired_method") == "LONG") & (pl.col("signal") == "EXIT")).then(pl.col("_long_exit_realized_profit"))
            .when((pl.col("desired_method") == "SHORT") & (pl.col("signal") == "EXIT")).then(pl.col("_short_exit_realized_profit"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_profit"))
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_profit"))
            .otherwise(0.0)
            .alias("expected_profit"),
            pl.when((pl.col("desired_method") == "LONG") & (pl.col("signal") == "EXIT")).then(pl.col("_long_exit_realized_drawdown"))
            .when((pl.col("desired_method") == "SHORT") & (pl.col("signal") == "EXIT")).then(pl.col("_short_exit_realized_adverse"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_drawdown"))
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_adverse"))
            .otherwise(0.0)
            .alias("expected_drawdown"),
            pl.when((pl.col("desired_method") == "LONG") & (pl.col("signal") == "EXIT")).then(pl.col("_long_exit_realized_horizon_bars"))
            .when((pl.col("desired_method") == "SHORT") & (pl.col("signal") == "EXIT")).then(pl.col("_short_exit_realized_horizon_bars"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_best_index") + 1)
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_best_index") + 1)
            .otherwise(None)
            .alias("horizon_bars"),
            pl.when(pl.col("signal") == "EXIT").then(pl.col("bar_id"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_best_bar_id"))
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_best_bar_id"))
            .otherwise(None)
            .alias("best_exit_bar_id"),
            pl.when(pl.col("signal") == "EXIT").then(pl.col("bar_time_utc"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_best_time"))
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_best_time"))
            .otherwise(None)
            .alias("best_exit_time_utc"),
            pl.when((pl.col("desired_method") == "LONG") & (pl.col("signal") == "EXIT")).then(pl.col("high"))
            .when((pl.col("desired_method") == "SHORT") & (pl.col("signal") == "EXIT")).then(pl.col("low"))
            .when(pl.col("desired_method") == "LONG").then(pl.col("_long_best_price"))
            .when(pl.col("desired_method") == "SHORT").then(pl.col("_short_best_price"))
            .otherwise(None)
            .alias("best_exit_price"),
        )
        .with_columns(
            (pl.col("horizon_bars") * step).alias("horizon_minutes"),
            (pl.col("_long_enter") | pl.col("_long_hold") | pl.col("_long_exit")).alias("oracle_long_supervision"),
            (pl.col("_short_enter") | pl.col("_short_hold") | pl.col("_short_exit")).alias("oracle_short_supervision"),
            pl.when(pl.col("_long_exit")).then(pl.col("_long_exit_realized_score")).otherwise(pl.col("oracle_long_enter_score")).alias("oracle_long_supervision_score"),
            pl.when(pl.col("_short_exit")).then(pl.col("_short_exit_realized_score")).otherwise(pl.col("oracle_short_enter_score")).alias("oracle_short_supervision_score"),
            pl.col("_long_enter").alias("oracle_long_enter_signal"),
            pl.col("_long_exit").alias("oracle_long_exit_signal"),
            pl.col("_short_enter").alias("oracle_short_enter_signal"),
            pl.col("_short_exit").alias("oracle_short_exit_signal"),
        )
        .with_columns(
            pl.when(pl.col("signal") == "ENTER").then(pl.lit("best_future_profit_after_risk_penalty"))
            .when(pl.col("signal") == "HOLD").then(pl.lit("continuation_upside_remaining"))
            .when(pl.col("signal") == "EXIT").then(pl.lit("best_horizon_reached_take_profit"))
            .otherwise(pl.lit("no_tradeable_oracle_edge"))
            .alias("reason")
        )
        .select(
            *BASE_COLUMNS,
            "desired_method",
            "signal",
            "horizon_bars",
            "horizon_minutes",
            "score",
            "expected_profit",
            "expected_drawdown",
            "best_exit_bar_id",
            "best_exit_time_utc",
            "best_exit_price",
            "reason",
            "oracle_long_supervision",
            "oracle_short_supervision",
            "oracle_long_supervision_score",
            "oracle_short_supervision_score",
            "oracle_long_enter_signal",
            "oracle_long_exit_signal",
            "oracle_short_enter_signal",
            "oracle_short_exit_signal",
            "oracle_long_enter_score",
            "oracle_long_exit_score",
            "oracle_short_enter_score",
            "oracle_short_exit_score",
            pl.col("_long_profit").alias("long_expected_profit"),
            pl.col("_short_profit").alias("short_expected_profit"),
            pl.col("_long_exit_realized_profit").alias("long_exit_realized_profit"),
            pl.col("_short_exit_realized_profit").alias("short_exit_realized_profit"),
            pl.col("_long_exit_entry_bar_id").alias("long_exit_entry_bar_id"),
            pl.col("_short_exit_entry_bar_id").alias("short_exit_entry_bar_id"),
            pl.col("_long_drawdown").alias("long_drawdown_before_best"),
            pl.col("_short_adverse").alias("short_adverse_before_best"),
            (pl.col("_long_best_index") + 1).alias("long_best_horizon_bars"),
            (pl.col("_short_best_index") + 1).alias("short_best_horizon_bars"),
            pl.col("_future_count").alias("future_bar_count"),
            (pl.col("_future_count") > 0).alias("valid_future_window"),
        )
    )


def iter_bar_supervision_frames(
    frame: pl.DataFrame,
    horizons_bars: Iterable[int] = FIXED_HORIZON_BARS,
    on_horizon_start: Callable[[int, int, int], None] | None = None,
    assume_sorted: bool = False,
) -> Iterator[tuple[int, pl.DataFrame]]:
    if frame.is_empty():
        return
    horizons = list(horizons_bars)
    sorted_frame = frame if assume_sorted else _sorted(frame)
    step = step_minutes_from_frame(sorted_frame)
    for horizon_index, horizon_bars in enumerate(horizons, start=1):
        if on_horizon_start:
            on_horizon_start(horizon_index, horizon_bars, len(horizons))
        yield horizon_bars, _bar_horizon_frame(sorted_frame, horizon_bars, step)


def _max_future_bars(frame: pl.DataFrame) -> int:
    return max(1, int(frame.group_by("ticker").len().select(pl.col("len").max()).item() or 1) - 1)


def _bool_future_list(column: str, offsets: range) -> pl.Expr:
    return pl.concat_list([pl.col(column).shift(-offset).over("ticker").fill_null(False) for offset in offsets])


def _method_frame(frame: pl.DataFrame, method: str, min_bars: int, max_bars: int, step: int, max_future_bars: int) -> pl.DataFrame:
    min_bars = max(1, int(min_bars))
    requested_max_bars = max(1, int(max_bars))
    max_bars = min(max_future_bars, requested_max_bars)
    if min_bars > max_bars:
        max_bars = min_bars
    min_minutes = min_bars * step
    max_minutes = max_bars * step
    offsets = range(min_bars, max_bars + 1)
    future_highs = _future_list("high", offsets)
    future_lows = _future_list("low", offsets)
    future_closes = _future_list("close", offsets)
    future_bar_ids = _future_list("bar_id", offsets)
    future_times = _future_list("bar_time_utc", offsets)
    future_price_shocks = _bool_future_list("price_shock", offsets)
    future_volume_shocks = _bool_future_list("volume_shock", offsets)

    return (
        frame.with_columns(
            future_highs.alias("_future_highs"),
            future_lows.alias("_future_lows"),
            future_closes.alias("_future_closes"),
            future_bar_ids.alias("_future_bar_ids"),
            future_times.alias("_future_times"),
            future_price_shocks.alias("_future_price_shocks"),
            future_volume_shocks.alias("_future_volume_shocks"),
        )
        .with_columns(
            pl.col("_future_closes").list.drop_nulls().list.len().alias("_future_count"),
            pl.coalesce(pl.col("_future_highs").list.max(), pl.col("close")).alias("_best_high"),
            pl.coalesce(pl.col("_future_highs").list.arg_max(), pl.lit(0)).alias("_best_index"),
            pl.col("_future_volume_shocks").list.any().alias("_has_future_volume_confirm"),
            pl.col("_future_price_shocks").list.any().alias("_has_future_price_confirm"),
            pl.col("_future_volume_shocks").list.arg_max().alias("_future_volume_confirm_index_raw"),
            pl.col("_future_price_shocks").list.arg_max().alias("_future_price_confirm_index_raw"),
        )
        .with_columns(
            pl.when(pl.col("_has_future_volume_confirm")).then(pl.col("_future_volume_confirm_index_raw")).otherwise(None).alias("_future_volume_confirm_index"),
            pl.when(pl.col("_has_future_price_confirm")).then(pl.col("_future_price_confirm_index_raw")).otherwise(None).alias("_future_price_confirm_index"),
            pl.col("_future_lows").list.slice(0, pl.col("_best_index") + 1).list.min().alias("_worst_before_best"),
        )
        .with_columns(
            ((pl.col("_best_high") / pl.col("close")) - 1.0).fill_nan(0.0).fill_null(0.0).alias("_best_return"),
            ((pl.col("_worst_before_best") / pl.col("close")) - 1.0).fill_nan(0.0).fill_null(0.0).alias("_mae_before_best"),
            pl.col("_future_bar_ids").list.get(pl.col("_best_index"), null_on_oob=True).alias("_best_bar_id"),
            pl.col("_future_times").list.get(pl.col("_best_index"), null_on_oob=True).alias("_best_time"),
        )
        .with_columns(
            _path_efficiency_expr(pl.col("close"), pl.col("_future_closes"), pl.col("_best_index"), pl.col("_best_high")).alias("_efficiency")
        )
        .with_columns(
            _quality_expr(pl.col("close"), pl.col("_best_return"), pl.col("_mae_before_best"), pl.col("_efficiency")).alias("_base_confidence")
        )
        .with_columns(
            pl.when((pl.col("_base_confidence") >= 0.65) & (pl.col("_best_return") > 0.005))
            .then(pl.lit("ENTER_NOW"))
            .when(pl.col("_base_confidence") >= 0.45)
            .then(pl.lit("WATCH"))
            .otherwise(pl.lit("IGNORE"))
            .alias("_base_action"),
            pl.coalesce(pl.col("shock_confirmation_type"), pl.lit("NONE")).alias("_shock_confirmation_type_base"),
            pl.coalesce(pl.col("shock_confirmation_delay_minutes"), pl.lit(None)).alias("_shock_delay_base"),
        )
        .with_columns(
            pl.when(pl.col("confirmed_price_volume_shock"))
            .then(pl.lit(-1))
            .when(pl.col("price_shock") & pl.col("_has_future_volume_confirm"))
            .then(pl.col("_future_volume_confirm_index"))
            .when(pl.col("volume_shock") & pl.col("_has_future_price_confirm"))
            .then(pl.col("_future_price_confirm_index"))
            .otherwise(None)
            .alias("_confirmation_index"),
        )
        .with_columns(
            pl.when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("price_shock") & pl.col("_has_future_volume_confirm"))
            .then(
                pl.when(pl.col("_future_volume_confirm_index") + min_bars <= 2)
                .then(pl.lit("PRICE_FIRST_IMMEDIATE_VOLUME"))
                .otherwise(pl.lit("PRICE_FIRST_DELAYED_VOLUME"))
            )
            .when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("volume_shock") & pl.col("_has_future_price_confirm"))
            .then(pl.lit("VOLUME_FIRST_BREAKOUT"))
            .when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("price_shock"))
            .then(pl.lit("PRICE_ONLY_UNCONFIRMED"))
            .when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("volume_shock"))
            .then(pl.lit("VOLUME_ONLY"))
            .otherwise(pl.col("_shock_confirmation_type_base"))
            .alias("_shock_confirmation_type"),
            pl.when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("price_shock") & pl.col("_has_future_volume_confirm"))
            .then((pl.col("_future_volume_confirm_index") + min_bars) * step)
            .when((method == "PRICE_VOLUME_SHOCK") & (pl.col("_shock_confirmation_type_base") == "NONE") & pl.col("volume_shock") & pl.col("_has_future_price_confirm"))
            .then((pl.col("_future_price_confirm_index") + min_bars) * step)
            .otherwise(pl.col("_shock_delay_base"))
            .alias("_shock_delay"),
        )
        .with_columns(
            pl.when(pl.col("_confirmation_index") == -1).then(pl.lit(0)).otherwise(pl.col("_confirmation_index")).alias("_post_start"),
            pl.when((pl.col("_confirmation_index").is_not_null()) & (pl.col("_confirmation_index") >= 0))
            .then(pl.col("_future_lows").list.slice(0, pl.col("_confirmation_index") + 1).list.min())
            .otherwise(None)
            .alias("_low_before_confirmation"),
        )
        .with_columns(
            pl.when(pl.col("_confirmation_index").is_not_null()).then(pl.col("_future_highs").list.slice(pl.col("_post_start")).list.max()).otherwise(None).alias("_post_best_high"),
            pl.when(pl.col("_confirmation_index").is_not_null()).then(pl.col("_future_highs").list.slice(pl.col("_post_start")).list.arg_max()).otherwise(None).alias("_post_best_index"),
            pl.when(pl.col("_confirmation_index").is_not_null()).then(pl.col("_future_bar_ids").list.slice(pl.col("_post_start"))).otherwise(None).alias("_post_bar_ids"),
            pl.when(pl.col("_confirmation_index").is_not_null()).then(pl.col("_future_times").list.slice(pl.col("_post_start"))).otherwise(None).alias("_post_times"),
            pl.when(pl.col("_confirmation_index") == -1).then(pl.col("close")).otherwise(pl.col("_future_closes").list.get(pl.col("_post_start"), null_on_oob=True)).alias("_confirmation_price"),
        )
        .with_columns(
            _safe_ratio(pl.col("_post_best_high"), pl.col("_confirmation_price")).sub(1.0).alias("_shock_return_after_confirmation"),
            _safe_ratio(pl.col("_low_before_confirmation"), pl.col("close")).sub(1.0).alias("_shock_drawdown_before_confirmation"),
            pl.max_horizontal(pl.col("price_volume_shock_score"), (pl.col("price_shock_score") * 0.55) + (pl.col("volume_shock_score") * 0.45)).alias("_shock_context_score"),
            pl.when(pl.col("_shock_delay").is_not_null())
            .then(_bounded(1.0 - (pl.col("_shock_delay").cast(pl.Float64) / max(float(max_minutes), 1.0))))
            .otherwise(0.0)
            .alias("_confirmation_speed"),
            pl.when(pl.col("_shock_confirmation_type").is_in(["SAME_BAR", "PRICE_FIRST_IMMEDIATE_VOLUME"]))
            .then(0.15)
            .when(pl.col("_shock_confirmation_type") == "PRICE_FIRST_DELAYED_VOLUME")
            .then(0.08)
            .otherwise(0.0)
            .alias("_confirmation_bonus"),
        )
        .with_columns(
            _bounded(
                (pl.col("_shock_context_score") * 0.45)
                + (pl.col("_base_confidence") * 0.35)
                + (pl.col("_confirmation_speed") * 0.12)
                + pl.col("_confirmation_bonus")
            ).alias("_shock_confidence")
        )
        .with_columns(
            pl.when(method != "PRICE_VOLUME_SHOCK")
            .then(pl.col("_base_confidence"))
            .otherwise(pl.col("_shock_confidence"))
            .alias("_method_confidence"),
            pl.when(method != "PRICE_VOLUME_SHOCK")
            .then(pl.col("_base_action"))
            .when((pl.col("_shock_context_score") < 0.35) | pl.col("_shock_confirmation_type").is_in(["NONE", "VOLUME_ONLY"]))
            .then(pl.lit("IGNORE"))
            .when((pl.col("_shock_confidence") >= 0.68) & (pl.col("_best_return") > 0.004))
            .then(pl.lit("ENTER_NOW"))
            .when(pl.col("_shock_confidence") >= 0.45)
            .then(pl.lit("WATCH"))
            .otherwise(pl.lit("IGNORE"))
            .alias("_oracle_action"),
        )
        .select(
            *BASE_COLUMNS,
            pl.lit(method).alias("trade_method"),
            pl.lit(min_bars).alias("method_min_horizon_bars"),
            pl.lit(max_bars).alias("method_max_horizon_bars"),
            pl.lit(min_minutes).alias("method_min_horizon_minutes"),
            pl.lit(max_minutes).cast(pl.Int64).alias("method_max_horizon_minutes"),
            (pl.col("_future_count") > 0).alias("valid_future_window"),
            pl.col("_best_bar_id").alias("method_best_exit_bar_id"),
            pl.col("_best_time").alias("method_best_exit_time_utc"),
            pl.when(pl.col("_future_count") > 0).then(pl.col("_best_index") + min_bars).otherwise(None).alias("method_best_horizon_bars"),
            pl.when(pl.col("_future_count") > 0).then((pl.col("_best_index") + min_bars) * step).otherwise(None).alias("method_best_horizon_minutes"),
            pl.col("_best_high").alias("method_best_price"),
            pl.col("_best_return").alias("method_best_return"),
            pl.col("_mae_before_best").alias("method_mae_before_best"),
            _safe_ratio(pl.col("_best_return"), pl.col("_mae_before_best").abs()).alias("method_mfe_mae_ratio"),
            pl.col("_efficiency").alias("method_path_efficiency"),
            (pl.col("_oracle_action") == "ENTER_NOW").alias("method_entry_signal"),
            (pl.col("_oracle_action") == "IGNORE").alias("method_exit_signal"),
            pl.col("_method_confidence").alias("method_confidence"),
            pl.col("_oracle_action").alias("oracle_action"),
            pl.col("price_shock").alias("current_price_shock"),
            pl.col("volume_shock").alias("current_volume_shock"),
            pl.col("confirmed_price_volume_shock").alias("current_confirmed_price_volume_shock"),
            pl.col("_shock_confirmation_type").alias("shock_confirmation_type"),
            pl.col("_shock_delay").alias("shock_confirmation_delay_minutes"),
            pl.col("price_shock_score").alias("shock_price_score"),
            pl.col("volume_shock_score").alias("shock_volume_score"),
            pl.col("price_volume_shock_score").alias("shock_score"),
            pl.when(method == "PRICE_VOLUME_SHOCK").then(pl.col("_shock_drawdown_before_confirmation")).otherwise(None).alias("shock_drawdown_before_confirmation"),
            pl.when(method == "PRICE_VOLUME_SHOCK").then(pl.col("_shock_return_after_confirmation")).otherwise(None).alias("shock_return_after_confirmation"),
            pl.when(method == "PRICE_VOLUME_SHOCK").then(pl.col("_post_bar_ids").list.get(pl.col("_post_best_index"), null_on_oob=True)).otherwise(None).alias("shock_best_exit_after_confirmation_bar_id"),
            pl.when(method == "PRICE_VOLUME_SHOCK").then(pl.col("_post_times").list.get(pl.col("_post_best_index"), null_on_oob=True)).otherwise(None).alias("shock_best_exit_after_confirmation_time_utc"),
        )
    )


def build_method_supervision(frame: pl.DataFrame, *, assume_sorted: bool = False) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame()
    sorted_frame = frame if assume_sorted else _sorted(frame)
    step = step_minutes_from_frame(sorted_frame)
    max_future_bars = _max_future_bars(sorted_frame)
    timeframe = str(sorted_frame["timeframe"][0]) if "timeframe" in sorted_frame.columns and sorted_frame.height else "1m"
    frames = [
        _method_frame(sorted_frame, method, min_minutes, max_minutes, step, max_future_bars)
        for method, (min_minutes, max_minutes) in method_windows_for_timeframe(timeframe).items()
    ]
    return pl.concat(frames, how="diagonal") if frames else pl.DataFrame()


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
            "current_price_shock",
            "current_volume_shock",
            "current_confirmed_price_volume_shock",
            "shock_confirmation_type",
            "shock_confirmation_delay_minutes",
            "shock_score",
            "shock_return_after_confirmation",
            "shock_drawdown_before_confirmation",
            "is_top_1",
            "is_top_3",
            "is_top_5",
            "is_top_10",
            "is_top_1pct",
            "is_top_5pct",
        )
    )
