from __future__ import annotations

import polars as pl

FeatureFrame = pl.DataFrame | pl.LazyFrame


FEATURE_COLUMNS: dict[str, list[str]] = {
    "core": [
        "bar_id",
        "ticker",
        "timeframe",
        "bar_time_utc",
        "bar_time_market",
        "session_date",
        "session_month",
        "minute_of_day",
        "hlc3",
        "ohlc4",
        "dollar_volume",
        "return_1",
        "log_return_1",
        "bar_range",
        "body",
        "body_abs",
        "upper_wick",
        "lower_wick",
        "close_location",
        "is_green",
        "is_red",
        "vwap",
    ],
    "session": [
        "bar_id",
        "day_open",
        "session_bar_count",
        "minutes_since_premarket_start",
        "ideal_bars_since_premarket_start",
        "session_bar_coverage_ratio",
        "premarket_open",
        "change_since_premarket_open",
        "change_since_premarket_open_pct",
        "day_high_so_far",
        "day_low_so_far",
        "day_volume_so_far",
        "day_dollar_volume_so_far",
        "prev_close",
        "gap_pct",
        "premarket_high",
        "premarket_low",
        "premarket_range",
        "premarket_volume",
        "or_5m_high",
        "or_5m_low",
        "or_5m_range",
        "or_10m_high",
        "or_10m_low",
        "or_10m_range",
        "or_15m_high",
        "or_15m_low",
        "or_15m_range",
        "or_30m_high",
        "or_30m_low",
        "or_30m_range",
        "distance_to_day_open_pct",
        "distance_to_day_high_pct",
        "distance_to_day_low_pct",
    ],
    "momentum": [
        "bar_id",
        "sma9",
        "sma20",
        "sma50",
        "sma200",
        "ema9",
        "ema20",
        "ema50",
        "ema200",
        "tema9",
        "tema20",
        "tema_open",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "macd_hist_z_since_open",
        "rsi14",
        "roc10",
        "indicator_bar_count",
        "macd_ready",
        "tema_ready",
    ],
    "volatility": [
        "bar_id",
        "true_range",
        "atr14",
        "bb_mid20",
        "bb_upper20",
        "bb_lower20",
        "bb_width20",
        "donchian_high20",
        "donchian_low20",
        "donchian_mid20",
        "keltner_mid20",
        "keltner_upper20",
        "keltner_lower20",
        "return_z20",
        "range_z20",
    ],
    "volume_liquidity": [
        "bar_id",
        "volume_sma10",
        "relative_volume10",
        "volume_sma20",
        "relative_volume20",
        "dollar_volume_sma20",
        "relative_dollar_volume20",
        "recent_dollar_volume_5",
        "recent_transactions_5",
        "tick_floor_bps",
        "bar_range_bps",
        "range_proxy_bps",
        "illiquidity_proxy_bps",
        "estimated_spread_bps",
        "actual_spread",
        "actual_spread_bps",
        "actual_spread_bps_abs",
        "actual_spread_bps_avg",
        "actual_spread_bps_median",
        "actual_spread_bps_max",
        "quote_valid_ratio",
        "locked_or_crossed_count",
        "quoted_share_depth",
        "quoted_dollar_depth",
        "actual_vs_estimated_spread_bps",
        "tod_cum_volume_avg13",
        "intraday_rvol13",
        "tod_cum_dollar_volume_avg13",
        "intraday_dollar_rvol13",
        "obv",
        "mfi14",
        "cmf20",
        "volume_z20",
        "transactions_sma20",
        "transactions_z20",
        "liquidity_band_25bp_volume",
        "liquidity_band_50bp_volume",
        "liquidity_band_100bp_volume",
        "hvn_price_proxy20",
        "lvn_price_proxy20",
    ],
    "price_action": [
        "bar_id",
        "inside_bar",
        "outside_bar",
        "bullish_engulfing",
        "bearish_engulfing",
        "nr4",
        "nr7",
        "consecutive_green",
        "consecutive_red",
        "green_bar_count_so_far",
        "red_bar_count_so_far",
        "green_bars_occurrence",
        "green_body_sum_so_far",
        "red_body_sum_so_far",
        "green_body_avg",
        "red_body_avg",
        "green_range_sum_so_far",
        "red_range_sum_so_far",
        "net_body_sum_so_far",
        "breaks_high20",
        "breaks_low20",
        "pullback_from_high20_pct",
        "reclaim_vwap",
        "breakdown_vwap",
    ],
    "shock": [
        "bar_id",
        "return_shock",
        "range_shock",
        "structure_break_shock",
        "price_shock",
        "price_shock_score",
        "relative_volume_shock",
        "dollar_volume_shock",
        "transactions_shock",
        "volume_shock",
        "volume_shock_score",
        "bars_since_price_shock",
        "bars_since_volume_shock",
        "minutes_since_price_shock",
        "minutes_since_volume_shock",
        "price_shock_recent",
        "volume_shock_recent",
        "price_shock_before_volume_shock",
        "confirmed_price_volume_shock",
        "shock_confirmation_delay_minutes",
        "shock_confirmation_type",
        "price_volume_shock_score",
    ],
    "fvg": [
        "bar_id",
        "bullish_fvg",
        "bearish_fvg",
        "fvg_high",
        "fvg_low",
        "fvg_mid",
        "fvg_size",
        "fvg_size_pct",
    ],
    "market_structure": [
        "bar_id",
        "swing_high_3",
        "swing_low_3",
        "swing_high_5",
        "swing_low_5",
        "higher_high",
        "lower_low",
        "bos_up",
        "bos_down",
        "trend_regime",
    ],
    "order_blocks": [
        "bar_id",
        "bullish_displacement",
        "bearish_displacement",
        "bullish_order_block_high",
        "bullish_order_block_low",
        "bearish_order_block_high",
        "bearish_order_block_low",
        "distance_to_demand_pct",
        "distance_to_supply_pct",
    ],
}


def is_lazy_frame(frame: FeatureFrame) -> bool:
    return isinstance(frame, pl.LazyFrame)


def frame_columns(frame: FeatureFrame) -> list[str]:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect_schema().names()
    return list(frame.columns)


def rolling_z(column: str, window: int, alias: str) -> pl.Expr:
    mean = pl.col(column).rolling_mean(window).over("ticker")
    std = pl.col(column).rolling_std(window).over("ticker")
    return pl.when(std > 0).then((pl.col(column) - mean) / std).otherwise(0.0).alias(alias)


def ema_expr(column: str, span: int, alias: str) -> pl.Expr:
    return pl.col(column).ewm_mean(span=span, adjust=False).over("ticker").alias(alias)


def timeframe_step_minutes_expr() -> pl.Expr:
    return (
        pl.when(pl.col("timeframe").str.ends_with("m"))
        .then(pl.col("timeframe").str.strip_suffix("m").cast(pl.Int32, strict=False))
        .when(pl.col("timeframe").str.ends_with("h"))
        .then(pl.col("timeframe").str.strip_suffix("h").cast(pl.Int32, strict=False) * 60)
        .when(pl.col("timeframe") == "1d")
        .then(pl.lit(390))
        .when(pl.col("timeframe") == "1mo")
        .then(pl.lit(8190))
        .otherwise(pl.lit(1))
        .fill_null(1)
    )


def add_tema(frame: FeatureFrame, period: int, alias: str) -> FeatureFrame:
    ema1 = f"_{alias}_ema1"
    ema2 = f"_{alias}_ema2"
    ema3 = f"_{alias}_ema3"
    return (
        frame.with_columns(ema_expr("close", period, ema1))
        .with_columns(pl.col(ema1).ewm_mean(span=period, adjust=False).over("ticker").alias(ema2))
        .with_columns(pl.col(ema2).ewm_mean(span=period, adjust=False).over("ticker").alias(ema3))
        .with_columns(((3.0 * pl.col(ema1)) - (3.0 * pl.col(ema2)) + pl.col(ema3)).alias(alias))
        .drop([ema1, ema2, ema3])
    )


def add_session_reference_features(frame: FeatureFrame) -> FeatureFrame:
    if isinstance(frame, pl.DataFrame) and frame.is_empty():
        return frame
    keys = ["ticker", "session_date"]
    regular_open_minute = 9 * 60 + 30
    premarket = (
        frame.filter(pl.col("minute_of_day") < regular_open_minute)
        .group_by(keys)
        .agg(
            pl.col("high").max().alias("premarket_high"),
            pl.col("low").min().alias("premarket_low"),
            pl.col("volume").sum().alias("premarket_volume"),
        )
        .with_columns((pl.col("premarket_high") - pl.col("premarket_low")).alias("premarket_range"))
    )
    result = frame.join(premarket, on=keys, how="left").with_columns(
        pl.when(pl.col("minute_of_day") >= regular_open_minute).then(pl.col("premarket_high")).otherwise(None).alias("premarket_high"),
        pl.when(pl.col("minute_of_day") >= regular_open_minute).then(pl.col("premarket_low")).otherwise(None).alias("premarket_low"),
        pl.when(pl.col("minute_of_day") >= regular_open_minute).then(pl.col("premarket_volume")).otherwise(None).alias("premarket_volume"),
        pl.when(pl.col("minute_of_day") >= regular_open_minute).then(pl.col("premarket_range")).otherwise(None).alias("premarket_range"),
    )
    for minutes in [5, 10, 15, 30]:
        end_minute = regular_open_minute + minutes
        opening = (
            frame.filter((pl.col("minute_of_day") >= regular_open_minute) & (pl.col("minute_of_day") < end_minute))
            .group_by(keys)
            .agg(
                pl.col("high").max().alias(f"or_{minutes}m_high"),
                pl.col("low").min().alias(f"or_{minutes}m_low"),
            )
            .with_columns((pl.col(f"or_{minutes}m_high") - pl.col(f"or_{minutes}m_low")).alias(f"or_{minutes}m_range"))
        )
        result = result.join(opening, on=keys, how="left").with_columns(
            pl.when(pl.col("minute_of_day") >= end_minute).then(pl.col(f"or_{minutes}m_high")).otherwise(None).alias(f"or_{minutes}m_high"),
            pl.when(pl.col("minute_of_day") >= end_minute).then(pl.col(f"or_{minutes}m_low")).otherwise(None).alias(f"or_{minutes}m_low"),
            pl.when(pl.col("minute_of_day") >= end_minute).then(pl.col(f"or_{minutes}m_range")).otherwise(None).alias(f"or_{minutes}m_range"),
        )
    return result


def add_previous_session_close(frame: FeatureFrame) -> FeatureFrame:
    session_close = (
        frame.group_by(["ticker", "session_date"])
        .agg(pl.col("close").last().alias("_session_close"))
        .sort(["ticker", "session_date"])
        .with_columns(pl.col("_session_close").shift(1).over("ticker").alias("prev_close"))
        .drop("_session_close")
    )
    return frame.join(session_close, on=["ticker", "session_date"], how="left")


def add_feature_columns(frame: FeatureFrame) -> FeatureFrame:
    if isinstance(frame, pl.DataFrame) and frame.is_empty():
        return frame
    premarket_start_minute = 4 * 60
    frame = frame.sort(["ticker", "bar_time_utc"])
    prior_bar_close = pl.col("close").shift(1).over("ticker")
    frame = (
        frame.with_columns(
            ((pl.col("high") + pl.col("low") + pl.col("close")) / 3.0).alias("hlc3"),
            ((pl.col("open") + pl.col("high") + pl.col("low") + pl.col("close")) / 4.0).alias("ohlc4"),
            (pl.col("close") * pl.col("volume")).alias("dollar_volume"),
            ((pl.col("close") / prior_bar_close) - 1.0).fill_null(0.0).alias("return_1"),
            (pl.col("close") / prior_bar_close).log().fill_null(0.0).alias("log_return_1"),
            (pl.col("high") - pl.col("low")).alias("bar_range"),
            (pl.col("close") - pl.col("open")).alias("body"),
            (pl.col("close") - pl.col("open")).abs().alias("body_abs"),
            (pl.col("high") - pl.max_horizontal("open", "close")).alias("upper_wick"),
            (pl.min_horizontal("open", "close") - pl.col("low")).alias("lower_wick"),
            pl.when(pl.col("high") > pl.col("low"))
            .then((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low")))
            .otherwise(0.0)
            .alias("close_location"),
            (pl.col("close") > pl.col("open")).alias("is_green"),
            (pl.col("close") < pl.col("open")).alias("is_red"),
        )
        .with_columns(
            (pl.col("dollar_volume").cum_sum().over(["ticker", "session_date"]) / pl.col("volume").cum_sum().over(["ticker", "session_date"])).alias("vwap"),
            pl.col("open").first().over(["ticker", "session_date"]).alias("day_open"),
            pl.col("open").first().over(["ticker", "session_date"]).alias("premarket_open"),
            pl.cum_count("close").over(["ticker", "session_date"]).alias("session_bar_count"),
            timeframe_step_minutes_expr().alias("_timeframe_step_minutes"),
            pl.max_horizontal(pl.col("minute_of_day") - premarket_start_minute, pl.lit(0)).alias("minutes_since_premarket_start"),
            pl.col("high").cum_max().over(["ticker", "session_date"]).alias("day_high_so_far"),
            pl.col("low").cum_min().over(["ticker", "session_date"]).alias("day_low_so_far"),
            pl.col("volume").cum_sum().over(["ticker", "session_date"]).alias("day_volume_so_far"),
            pl.col("dollar_volume").cum_sum().over(["ticker", "session_date"]).alias("day_dollar_volume_so_far"),
        )
        .pipe(add_previous_session_close)
        .with_columns(
            ((pl.col("minutes_since_premarket_start") / pl.col("_timeframe_step_minutes")).floor() + 1)
            .cast(pl.Int32)
            .alias("ideal_bars_since_premarket_start"),
            pl.when(pl.col("prev_close") > 0).then((pl.col("day_open") / pl.col("prev_close")) - 1.0).otherwise(0.0).alias("gap_pct"),
            pl.when(pl.col("premarket_open") > 0).then(pl.col("close") - pl.col("premarket_open")).otherwise(None).alias("change_since_premarket_open"),
            pl.when(pl.col("premarket_open") > 0).then((pl.col("close") / pl.col("premarket_open")) - 1.0).otherwise(None).alias("change_since_premarket_open_pct"),
            pl.when(pl.col("day_open") > 0).then((pl.col("close") / pl.col("day_open")) - 1.0).otherwise(0.0).alias("distance_to_day_open_pct"),
            pl.when(pl.col("day_high_so_far") > 0).then((pl.col("close") / pl.col("day_high_so_far")) - 1.0).otherwise(0.0).alias("distance_to_day_high_pct"),
            pl.when(pl.col("day_low_so_far") > 0).then((pl.col("close") / pl.col("day_low_so_far")) - 1.0).otherwise(0.0).alias("distance_to_day_low_pct"),
        )
        .with_columns(
            pl.when(pl.col("ideal_bars_since_premarket_start") > 0)
            .then(pl.col("session_bar_count") / pl.col("ideal_bars_since_premarket_start"))
            .otherwise(0.0)
            .alias("session_bar_coverage_ratio")
        )
    )
    frame = add_session_reference_features(frame)
    frame = (
        frame.with_columns(
            pl.col("close").rolling_mean(9).over("ticker").alias("sma9"),
            pl.col("close").rolling_mean(20).over("ticker").alias("sma20"),
            pl.col("close").rolling_mean(50).over("ticker").alias("sma50"),
            pl.col("close").rolling_mean(200).over("ticker").alias("sma200"),
            ema_expr("close", 9, "ema9"),
            ema_expr("close", 20, "ema20"),
            ema_expr("close", 50, "ema50"),
            ema_expr("close", 200, "ema200"),
            ema_expr("close", 12, "_macd_fast"),
            ema_expr("close", 26, "_macd_slow"),
        )
        .pipe(add_tema, 9, "tema9")
        .pipe(add_tema, 20, "tema20")
        .with_columns((pl.col("_macd_fast") - pl.col("_macd_slow")).alias("macd_line"))
        .with_columns(pl.col("macd_line").ewm_mean(span=9, adjust=False).over("ticker").alias("macd_signal"))
        .with_columns(
            (pl.col("tema9") > pl.col("tema20")).alias("tema_open"),
            (pl.col("macd_line") - pl.col("macd_signal")).alias("macd_hist"),
        )
        .with_columns(
            pl.col("macd_hist").fill_null(0.0).alias("_macd_hist_value"),
            pl.cum_count("close").over(["ticker", "session_date"]).cast(pl.Float64).alias("_macd_hist_session_n"),
        )
        .with_columns(
            pl.col("_macd_hist_value").cum_sum().over(["ticker", "session_date"]).alias("_macd_hist_session_sum"),
            (pl.col("_macd_hist_value") * pl.col("_macd_hist_value")).cum_sum().over(["ticker", "session_date"]).alias("_macd_hist_session_sum_sq"),
        )
        .with_columns((pl.col("_macd_hist_session_sum") / pl.col("_macd_hist_session_n")).alias("_macd_hist_session_mean"))
        .with_columns(
            (
                (pl.col("_macd_hist_session_sum_sq") / pl.col("_macd_hist_session_n"))
                - (pl.col("_macd_hist_session_mean") * pl.col("_macd_hist_session_mean"))
            )
            .clip(0.0)
            .sqrt()
            .alias("_macd_hist_session_std")
        )
        .with_columns(
            pl.when(pl.col("_macd_hist_session_std") > 0)
            .then((pl.col("_macd_hist_value") - pl.col("_macd_hist_session_mean")) / pl.col("_macd_hist_session_std"))
            .otherwise(0.0)
            .alias("macd_hist_z_since_open")
        )
        .with_columns(
            pl.cum_count("close").over("ticker").alias("indicator_bar_count"),
            (pl.cum_count("close").over("ticker") >= 35).alias("macd_ready"),
            (pl.cum_count("close").over("ticker") >= 20).alias("tema_ready"),
        )
    )
    close_delta = pl.col("close") - pl.col("close").shift(1).over("ticker")
    up = pl.when(close_delta > 0).then(close_delta).otherwise(0.0)
    down = pl.when(close_delta < 0).then(-close_delta).otherwise(0.0)
    prev_close_expr = pl.col("close").shift(1).over("ticker")
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close_expr).abs(),
        (pl.col("low") - prev_close_expr).abs(),
    )
    frame = (
        frame.with_columns(
            up.rolling_mean(14).over("ticker").alias("_avg_gain14"),
            down.rolling_mean(14).over("ticker").alias("_avg_loss14"),
            true_range.alias("true_range"),
            pl.col("return_1").rolling_sum(10).over("ticker").alias("roc10"),
        )
        .with_columns(
            pl.when(pl.col("_avg_loss14") > 0)
            .then(100.0 - (100.0 / (1.0 + (pl.col("_avg_gain14") / pl.col("_avg_loss14")))))
            .otherwise(100.0)
            .alias("rsi14"),
            pl.col("true_range").rolling_mean(14).over("ticker").alias("atr14"),
            pl.col("close").rolling_mean(20).over("ticker").alias("bb_mid20"),
            pl.col("close").rolling_std(20).over("ticker").alias("_bb_std20"),
            pl.col("high").rolling_max(20).over("ticker").alias("donchian_high20"),
            pl.col("low").rolling_min(20).over("ticker").alias("donchian_low20"),
            pl.col("volume").rolling_mean(10).over("ticker").alias("volume_sma10"),
            pl.col("volume").rolling_mean(20).over("ticker").alias("volume_sma20"),
            pl.col("dollar_volume").rolling_mean(20).over("ticker").alias("dollar_volume_sma20"),
            pl.col("transactions").rolling_mean(20).over("ticker").alias("transactions_sma20"),
            rolling_z("return_1", 20, "return_z20"),
            rolling_z("bar_range", 20, "range_z20"),
            rolling_z("volume", 20, "volume_z20"),
            rolling_z("transactions", 20, "transactions_z20"),
        )
        .with_columns(
            (pl.col("bb_mid20") + 2.0 * pl.col("_bb_std20")).alias("bb_upper20"),
            (pl.col("bb_mid20") - 2.0 * pl.col("_bb_std20")).alias("bb_lower20"),
            pl.when(pl.col("bb_mid20") > 0).then((4.0 * pl.col("_bb_std20")) / pl.col("bb_mid20")).otherwise(0.0).alias("bb_width20"),
            ((pl.col("donchian_high20") + pl.col("donchian_low20")) / 2.0).alias("donchian_mid20"),
            pl.col("ema20").alias("keltner_mid20"),
            (pl.col("ema20") + 2.0 * pl.col("atr14")).alias("keltner_upper20"),
            (pl.col("ema20") - 2.0 * pl.col("atr14")).alias("keltner_lower20"),
            pl.when(pl.col("volume_sma10") > 0).then(pl.col("volume") / pl.col("volume_sma10")).otherwise(0.0).alias("relative_volume10"),
            pl.when(pl.col("volume_sma20") > 0).then(pl.col("volume") / pl.col("volume_sma20")).otherwise(0.0).alias("relative_volume20"),
            pl.when(pl.col("dollar_volume_sma20") > 0).then(pl.col("dollar_volume") / pl.col("dollar_volume_sma20")).otherwise(0.0).alias("relative_dollar_volume20"),
            pl.col("dollar_volume").rolling_sum(5, min_samples=1).over("ticker").alias("recent_dollar_volume_5"),
            pl.col("transactions").rolling_sum(5, min_samples=1).over("ticker").alias("recent_transactions_5"),
            pl.when(pl.col("close") > 0).then(0.01 / pl.col("close") * 10_000.0).otherwise(10_000.0).alias("tick_floor_bps"),
            pl.when(pl.col("close") > 0).then(pl.col("bar_range") / pl.col("close") * 10_000.0).otherwise(10_000.0).alias("bar_range_bps"),
            pl.when(pl.col("dollar_volume") > 0)
            .then(pl.col("return_1").abs() * 10_000.0 * (100_000.0 / pl.col("dollar_volume")))
            .otherwise(10_000.0)
            .alias("illiquidity_proxy_bps"),
            pl.col("day_volume_so_far")
            .shift(1)
            .rolling_mean(13, min_samples=1)
            .over(["ticker", "minute_of_day"])
            .alias("tod_cum_volume_avg13"),
            pl.col("day_dollar_volume_so_far")
            .shift(1)
            .rolling_mean(13, min_samples=1)
            .over(["ticker", "minute_of_day"])
            .alias("tod_cum_dollar_volume_avg13"),
        )
        .with_columns(
            pl.when(pl.col("tod_cum_volume_avg13") > 0)
            .then(pl.col("day_volume_so_far") / pl.col("tod_cum_volume_avg13"))
            .otherwise(None)
            .alias("intraday_rvol13"),
            pl.when(pl.col("tod_cum_dollar_volume_avg13") > 0)
            .then(pl.col("day_dollar_volume_so_far") / pl.col("tod_cum_dollar_volume_avg13"))
            .otherwise(None)
            .alias("intraday_dollar_rvol13"),
        )
        .with_columns(pl.col("bar_range_bps").rolling_median(5, min_samples=1).over("ticker").alias("range_proxy_bps"))
        .with_columns(
            pl.max_horizontal("tick_floor_bps", "range_proxy_bps", "illiquidity_proxy_bps").alias("estimated_spread_bps")
        )
    )
    if "actual_spread_bps_abs" in frame.columns:
        frame = frame.with_columns((pl.col("actual_spread_bps_abs") - pl.col("estimated_spread_bps")).alias("actual_vs_estimated_spread_bps"))
    typical_money_flow = pl.col("hlc3") * pl.col("volume")
    positive_flow = pl.when(pl.col("hlc3") > pl.col("hlc3").shift(1).over("ticker")).then(typical_money_flow).otherwise(0.0)
    negative_flow = pl.when(pl.col("hlc3") < pl.col("hlc3").shift(1).over("ticker")).then(typical_money_flow).otherwise(0.0)
    money_flow_multiplier = pl.when(pl.col("high") > pl.col("low")).then(((pl.col("close") - pl.col("low")) - (pl.col("high") - pl.col("close"))) / (pl.col("high") - pl.col("low"))).otherwise(0.0)
    obv_delta = pl.col("close") - pl.col("close").shift(1).over("ticker")
    frame = (
        frame.with_columns(
            pl.when(obv_delta > 0).then(pl.col("volume")).when(obv_delta < 0).then(-pl.col("volume")).otherwise(0.0).cum_sum().over("ticker").alias("obv"),
            positive_flow.rolling_sum(14).over("ticker").alias("_mfi_pos14"),
            negative_flow.rolling_sum(14).over("ticker").alias("_mfi_neg14"),
            (money_flow_multiplier * pl.col("volume")).rolling_sum(20).over("ticker").alias("_cmf_num20"),
            pl.col("volume").rolling_sum(20).over("ticker").alias("_cmf_den20"),
        )
        .with_columns(
            pl.when(pl.col("_mfi_neg14") > 0).then(100.0 - (100.0 / (1.0 + pl.col("_mfi_pos14") / pl.col("_mfi_neg14")))).otherwise(100.0).alias("mfi14"),
            pl.when(pl.col("_cmf_den20") > 0).then(pl.col("_cmf_num20") / pl.col("_cmf_den20")).otherwise(0.0).alias("cmf20"),
            pl.col("volume").rolling_sum(20).over("ticker").alias("liquidity_band_25bp_volume"),
            pl.col("volume").rolling_sum(50).over("ticker").alias("liquidity_band_50bp_volume"),
            pl.col("volume").rolling_sum(100).over("ticker").alias("liquidity_band_100bp_volume"),
            pl.col("close").rolling_mean(20).over("ticker").alias("hvn_price_proxy20"),
            pl.col("close").rolling_median(20).over("ticker").alias("lvn_price_proxy20"),
        )
    )
    frame = (
        frame.with_columns(
            (pl.col("is_green") == False).cast(pl.Int32).cum_sum().over("ticker").alias("_green_reset"),
            (pl.col("is_red") == False).cast(pl.Int32).cum_sum().over("ticker").alias("_red_reset"),
        )
        .with_columns(
            ((pl.col("high") < pl.col("high").shift(1).over("ticker")) & (pl.col("low") > pl.col("low").shift(1).over("ticker"))).alias("inside_bar"),
            ((pl.col("high") > pl.col("high").shift(1).over("ticker")) & (pl.col("low") < pl.col("low").shift(1).over("ticker"))).alias("outside_bar"),
            ((pl.col("close") > pl.col("open")) & (pl.col("open") < pl.col("close").shift(1).over("ticker")) & (pl.col("close") > pl.col("open").shift(1).over("ticker"))).alias("bullish_engulfing"),
            ((pl.col("close") < pl.col("open")) & (pl.col("open") > pl.col("close").shift(1).over("ticker")) & (pl.col("close") < pl.col("open").shift(1).over("ticker"))).alias("bearish_engulfing"),
            (pl.col("bar_range") <= pl.col("bar_range").rolling_min(4).over("ticker")).alias("nr4"),
            (pl.col("bar_range") <= pl.col("bar_range").rolling_min(7).over("ticker")).alias("nr7"),
            pl.when(pl.col("is_green")).then(pl.col("is_green").cast(pl.Int32).cum_sum().over(["ticker", "_green_reset"])).otherwise(0).alias("consecutive_green"),
            pl.when(pl.col("is_red")).then(pl.col("is_red").cast(pl.Int32).cum_sum().over(["ticker", "_red_reset"])).otherwise(0).alias("consecutive_red"),
            pl.when(pl.col("is_green")).then(1).otherwise(0).cum_sum().over(["ticker", "session_date"]).alias("green_bar_count_so_far"),
            pl.when(pl.col("is_red")).then(1).otherwise(0).cum_sum().over(["ticker", "session_date"]).alias("red_bar_count_so_far"),
            pl.when(pl.col("is_green")).then(pl.col("body_abs")).otherwise(0.0).cum_sum().over(["ticker", "session_date"]).alias("green_body_sum_so_far"),
            pl.when(pl.col("is_red")).then(pl.col("body_abs")).otherwise(0.0).cum_sum().over(["ticker", "session_date"]).alias("red_body_sum_so_far"),
            pl.when(pl.col("is_green")).then(pl.col("bar_range")).otherwise(0.0).cum_sum().over(["ticker", "session_date"]).alias("green_range_sum_so_far"),
            pl.when(pl.col("is_red")).then(pl.col("bar_range")).otherwise(0.0).cum_sum().over(["ticker", "session_date"]).alias("red_range_sum_so_far"),
            pl.col("body").cum_sum().over(["ticker", "session_date"]).alias("net_body_sum_so_far"),
            (pl.col("high") > pl.col("high").shift(1).rolling_max(20).over("ticker")).fill_null(False).alias("breaks_high20"),
            (pl.col("low") < pl.col("low").shift(1).rolling_min(20).over("ticker")).fill_null(False).alias("breaks_low20"),
            pl.when(pl.col("donchian_high20") > 0).then((pl.col("close") / pl.col("donchian_high20")) - 1.0).otherwise(0.0).alias("pullback_from_high20_pct"),
            ((pl.col("close") > pl.col("vwap")) & (pl.col("close").shift(1).over("ticker") <= pl.col("vwap").shift(1).over("ticker"))).alias("reclaim_vwap"),
            ((pl.col("close") < pl.col("vwap")) & (pl.col("close").shift(1).over("ticker") >= pl.col("vwap").shift(1).over("ticker"))).alias("breakdown_vwap"),
        )
        .with_columns(
            pl.when(pl.col("session_bar_count") > 0)
            .then(pl.col("green_bar_count_so_far") / pl.col("session_bar_count"))
            .otherwise(0.0)
            .alias("green_bars_occurrence"),
            pl.when(pl.col("green_bar_count_so_far") > 0)
            .then(pl.col("green_body_sum_so_far") / pl.col("green_bar_count_so_far"))
            .otherwise(0.0)
            .alias("green_body_avg"),
            pl.when(pl.col("red_bar_count_so_far") > 0)
            .then(pl.col("red_body_sum_so_far") / pl.col("red_bar_count_so_far"))
            .otherwise(0.0)
            .alias("red_body_avg"),
        )
        .with_columns(
            ((pl.col("low") > pl.col("high").shift(2).over("ticker"))).alias("bullish_fvg"),
            ((pl.col("high") < pl.col("low").shift(2).over("ticker"))).alias("bearish_fvg"),
        )
        .with_columns(
            pl.when(pl.col("bullish_fvg")).then(pl.col("low")).when(pl.col("bearish_fvg")).then(pl.col("low").shift(2).over("ticker")).otherwise(None).alias("fvg_high"),
            pl.when(pl.col("bullish_fvg")).then(pl.col("high").shift(2).over("ticker")).when(pl.col("bearish_fvg")).then(pl.col("high")).otherwise(None).alias("fvg_low"),
        )
        .with_columns(
            ((pl.col("fvg_high") + pl.col("fvg_low")) / 2.0).alias("fvg_mid"),
            (pl.col("fvg_high") - pl.col("fvg_low")).abs().alias("fvg_size"),
            pl.when(pl.col("close") > 0).then((pl.col("fvg_high") - pl.col("fvg_low")).abs() / pl.col("close")).otherwise(0.0).alias("fvg_size_pct"),
        )
    )
    frame = (
        frame.with_columns(
            (pl.col("high") >= pl.col("high").rolling_max(3).over("ticker")).fill_null(False).alias("swing_high_3"),
            (pl.col("low") <= pl.col("low").rolling_min(3).over("ticker")).fill_null(False).alias("swing_low_3"),
            (pl.col("high") >= pl.col("high").rolling_max(5).over("ticker")).fill_null(False).alias("swing_high_5"),
            (pl.col("low") <= pl.col("low").rolling_min(5).over("ticker")).fill_null(False).alias("swing_low_5"),
            (pl.col("high") > pl.col("high").shift(1).over("ticker")).alias("higher_high"),
            (pl.col("low") < pl.col("low").shift(1).over("ticker")).alias("lower_low"),
            (pl.col("close") > pl.col("high").shift(1).rolling_max(20).over("ticker")).fill_null(False).alias("bos_up"),
            (pl.col("close") < pl.col("low").shift(1).rolling_min(20).over("ticker")).fill_null(False).alias("bos_down"),
            pl.when(pl.col("ema20") > pl.col("ema50")).then(pl.lit("up")).when(pl.col("ema20") < pl.col("ema50")).then(pl.lit("down")).otherwise(pl.lit("range")).alias("trend_regime"),
            ((pl.col("bar_range") > pl.col("atr14") * 1.5) & (pl.col("close") > pl.col("open"))).alias("bullish_displacement"),
            ((pl.col("bar_range") > pl.col("atr14") * 1.5) & (pl.col("close") < pl.col("open"))).alias("bearish_displacement"),
        )
        .with_columns(
            pl.when(pl.col("bullish_displacement")).then(pl.col("high").shift(1).over("ticker")).otherwise(None).alias("bullish_order_block_high"),
            pl.when(pl.col("bullish_displacement")).then(pl.col("low").shift(1).over("ticker")).otherwise(None).alias("bullish_order_block_low"),
            pl.when(pl.col("bearish_displacement")).then(pl.col("high").shift(1).over("ticker")).otherwise(None).alias("bearish_order_block_high"),
            pl.when(pl.col("bearish_displacement")).then(pl.col("low").shift(1).over("ticker")).otherwise(None).alias("bearish_order_block_low"),
        )
        .with_columns(
            pl.when(pl.col("bullish_order_block_high") > 0).then((pl.col("close") / pl.col("bullish_order_block_high")) - 1.0).otherwise(None).alias("distance_to_demand_pct"),
            pl.when(pl.col("bearish_order_block_low") > 0).then((pl.col("close") / pl.col("bearish_order_block_low")) - 1.0).otherwise(None).alias("distance_to_supply_pct"),
        )
    )
    prior_day_high = pl.col("day_high_so_far").shift(1).over(["ticker", "session_date"])
    frame = (
        frame.with_columns(
            ((pl.col("return_z20") >= 2.5) & (pl.col("return_1") > 0)).alias("return_shock"),
            ((pl.col("range_z20") >= 2.5) & (pl.col("body") > 0)).alias("range_shock"),
            (
                pl.col("breaks_high20").fill_null(False)
                | ((prior_day_high > 0) & (pl.col("close") > prior_day_high)).fill_null(False)
                | ((pl.col("premarket_high") > 0) & (pl.col("close") > pl.col("premarket_high"))).fill_null(False)
                | ((pl.col("or_5m_high") > 0) & (pl.col("close") > pl.col("or_5m_high"))).fill_null(False)
                | pl.col("reclaim_vwap").fill_null(False)
            ).alias("structure_break_shock"),
            (pl.col("relative_volume20") >= 3.0).alias("relative_volume_shock"),
            (pl.col("relative_dollar_volume20") >= 3.0).alias("dollar_volume_shock"),
            (pl.col("transactions_z20") >= 2.5).alias("transactions_shock"),
        )
        .with_columns(
            (
                (pl.col("return_shock") | pl.col("range_shock") | pl.col("bullish_displacement") | pl.col("structure_break_shock"))
                & (pl.col("close_location") >= 0.55)
            ).alias("price_shock"),
            (
                pl.col("relative_volume_shock")
                | pl.col("dollar_volume_shock")
                | pl.col("transactions_shock")
                | (pl.col("volume_z20") >= 2.5)
            ).alias("volume_shock"),
        )
        .with_columns(
            pl.min_horizontal(
                pl.lit(1.0),
                (pl.max_horizontal(pl.col("return_z20"), pl.lit(0.0)) / 5.0 * 0.30)
                + (pl.max_horizontal(pl.col("range_z20"), pl.lit(0.0)) / 5.0 * 0.25)
                + (pl.col("close_location").clip(0.0, 1.0) * 0.15)
                + (pl.col("structure_break_shock").cast(pl.Float64) * 0.15)
                + (pl.col("bullish_displacement").cast(pl.Float64) * 0.15),
            ).alias("price_shock_score"),
            pl.min_horizontal(
                pl.lit(1.0),
                (pl.max_horizontal(pl.col("volume_z20"), pl.lit(0.0)) / 5.0 * 0.30)
                + (pl.min_horizontal(pl.col("relative_volume20"), pl.lit(5.0)) / 5.0 * 0.25)
                + (pl.min_horizontal(pl.col("relative_dollar_volume20"), pl.lit(5.0)) / 5.0 * 0.25)
                + (pl.max_horizontal(pl.col("transactions_z20"), pl.lit(0.0)) / 5.0 * 0.20),
            ).alias("volume_shock_score"),
            pl.cum_count("close").over("ticker").cast(pl.Int32).alias("_bar_seq"),
            timeframe_step_minutes_expr().alias("_timeframe_step_minutes"),
        )
        .with_columns(
            pl.when(pl.col("price_shock")).then(pl.col("_bar_seq")).otherwise(None).forward_fill().over("ticker").alias("_last_price_shock_seq"),
            pl.when(pl.col("volume_shock")).then(pl.col("_bar_seq")).otherwise(None).forward_fill().over("ticker").alias("_last_volume_shock_seq"),
        )
        .with_columns(
            pl.when(pl.col("_last_price_shock_seq").is_not_null())
            .then(pl.col("_bar_seq") - pl.col("_last_price_shock_seq"))
            .otherwise(None)
            .alias("bars_since_price_shock"),
            pl.when(pl.col("_last_volume_shock_seq").is_not_null())
            .then(pl.col("_bar_seq") - pl.col("_last_volume_shock_seq"))
            .otherwise(None)
            .alias("bars_since_volume_shock"),
        )
        .with_columns(
            (pl.col("bars_since_price_shock") * pl.col("_timeframe_step_minutes")).alias("minutes_since_price_shock"),
            (pl.col("bars_since_volume_shock") * pl.col("_timeframe_step_minutes")).alias("minutes_since_volume_shock"),
            (pl.col("bars_since_price_shock").is_between(0, 15)).alias("price_shock_recent"),
            (pl.col("bars_since_volume_shock").is_between(0, 15)).alias("volume_shock_recent"),
        )
        .with_columns(
            (pl.col("volume_shock") & (pl.col("bars_since_price_shock") > 0) & (pl.col("bars_since_price_shock") <= 15)).alias("price_shock_before_volume_shock"),
            (pl.col("volume_shock") & pl.col("price_shock_recent")).alias("confirmed_price_volume_shock"),
        )
        .with_columns(
            pl.when(pl.col("confirmed_price_volume_shock"))
            .then(pl.col("minutes_since_price_shock"))
            .otherwise(None)
            .alias("shock_confirmation_delay_minutes"),
            pl.when(pl.col("price_shock") & pl.col("volume_shock"))
            .then(pl.lit("SAME_BAR"))
            .when(pl.col("confirmed_price_volume_shock") & (pl.col("bars_since_price_shock") <= 2))
            .then(pl.lit("PRICE_FIRST_IMMEDIATE_VOLUME"))
            .when(pl.col("confirmed_price_volume_shock"))
            .then(pl.lit("PRICE_FIRST_DELAYED_VOLUME"))
            .when(pl.col("price_shock") & pl.col("volume_shock_recent"))
            .then(pl.lit("VOLUME_FIRST_BREAKOUT"))
            .when(pl.col("price_shock"))
            .then(pl.lit("PRICE_ONLY_UNCONFIRMED"))
            .when(pl.col("volume_shock"))
            .then(pl.lit("VOLUME_ONLY"))
            .otherwise(pl.lit("NONE"))
            .alias("shock_confirmation_type"),
        )
        .with_columns(
            pl.min_horizontal(
                pl.lit(1.0),
                (pl.col("price_shock_score") * 0.45)
                + (pl.col("volume_shock_score") * 0.45)
                + (
                    pl.when(pl.col("confirmed_price_volume_shock"))
                    .then(1.0 - pl.min_horizontal(pl.col("bars_since_price_shock") / 15.0, pl.lit(1.0)))
                    .otherwise(0.0)
                    * 0.10
                ),
            ).alias("price_volume_shock_score")
        )
    )
    return frame.drop([col for col in frame_columns(frame) if col.startswith("_")])


def select_feature_group(frame: FeatureFrame, group: str) -> FeatureFrame:
    source_columns = set(frame_columns(frame))
    columns = [column for column in FEATURE_COLUMNS[group] if column in source_columns]
    return frame.select(columns)
