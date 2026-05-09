from __future__ import annotations

import polars as pl


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
        "day_high_so_far",
        "day_low_so_far",
        "day_volume_so_far",
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
        "macd_line",
        "macd_signal",
        "macd_hist",
        "rsi14",
        "roc10",
        "cci20",
        "stoch_k14",
        "stoch_d3",
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
        "volume_sma20",
        "relative_volume20",
        "dollar_volume_sma20",
        "relative_dollar_volume20",
        "obv",
        "mfi14",
        "cmf20",
        "volume_z20",
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
        "breaks_high20",
        "breaks_low20",
        "pullback_from_high20_pct",
        "reclaim_vwap",
        "breakdown_vwap",
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
        "bars_since_high20",
        "bars_since_low20",
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


def rolling_z(column: str, window: int, alias: str) -> pl.Expr:
    mean = pl.col(column).rolling_mean(window).over("ticker")
    std = pl.col(column).rolling_std(window).over("ticker")
    return pl.when(std > 0).then((pl.col(column) - mean) / std).otherwise(0.0).alias(alias)


def ema_expr(column: str, span: int, alias: str) -> pl.Expr:
    return pl.col(column).ewm_mean(span=span, adjust=False).over("ticker").alias(alias)


def add_tema(frame: pl.DataFrame, period: int, alias: str) -> pl.DataFrame:
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


def add_session_reference_features(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    keys = ["ticker", "session_date"]
    premarket = (
        frame.filter(pl.col("minute_of_day") < 9 * 60 + 30)
        .group_by(keys)
        .agg(
            pl.col("high").max().alias("premarket_high"),
            pl.col("low").min().alias("premarket_low"),
            pl.col("volume").sum().alias("premarket_volume"),
        )
        .with_columns((pl.col("premarket_high") - pl.col("premarket_low")).alias("premarket_range"))
    )
    result = frame.join(premarket, on=keys, how="left")
    for minutes in [5, 10, 15, 30]:
        end_minute = 9 * 60 + 30 + minutes
        opening = (
            frame.filter((pl.col("minute_of_day") >= 9 * 60 + 30) & (pl.col("minute_of_day") < end_minute))
            .group_by(keys)
            .agg(
                pl.col("high").max().alias(f"or_{minutes}m_high"),
                pl.col("low").min().alias(f"or_{minutes}m_low"),
            )
            .with_columns((pl.col(f"or_{minutes}m_high") - pl.col(f"or_{minutes}m_low")).alias(f"or_{minutes}m_range"))
        )
        result = result.join(opening, on=keys, how="left")
    return result


def add_feature_columns(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    frame = frame.sort(["ticker", "bar_time_utc"])
    prior_close = pl.col("close").shift(1).over("ticker")
    prev_day_close = pl.col("close").shift(1).over("ticker")
    frame = (
        frame.with_columns(
            ((pl.col("high") + pl.col("low") + pl.col("close")) / 3.0).alias("hlc3"),
            ((pl.col("open") + pl.col("high") + pl.col("low") + pl.col("close")) / 4.0).alias("ohlc4"),
            (pl.col("close") * pl.col("volume")).alias("dollar_volume"),
            ((pl.col("close") / prior_close) - 1.0).fill_null(0.0).alias("return_1"),
            (pl.col("close") / prior_close).log().fill_null(0.0).alias("log_return_1"),
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
            pl.col("high").cum_max().over(["ticker", "session_date"]).alias("day_high_so_far"),
            pl.col("low").cum_min().over(["ticker", "session_date"]).alias("day_low_so_far"),
            pl.col("volume").cum_sum().over(["ticker", "session_date"]).alias("day_volume_so_far"),
            prev_day_close.alias("prev_close"),
        )
        .with_columns(
            pl.when(pl.col("prev_close") > 0).then((pl.col("day_open") / pl.col("prev_close")) - 1.0).otherwise(0.0).alias("gap_pct"),
            pl.when(pl.col("day_open") > 0).then((pl.col("close") / pl.col("day_open")) - 1.0).otherwise(0.0).alias("distance_to_day_open_pct"),
            pl.when(pl.col("day_high_so_far") > 0).then((pl.col("close") / pl.col("day_high_so_far")) - 1.0).otherwise(0.0).alias("distance_to_day_high_pct"),
            pl.when(pl.col("day_low_so_far") > 0).then((pl.col("close") / pl.col("day_low_so_far")) - 1.0).otherwise(0.0).alias("distance_to_day_low_pct"),
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
        .with_columns((pl.col("macd_line") - pl.col("macd_signal")).alias("macd_hist"))
        .with_columns(
            pl.cum_count("close").over("ticker").alias("indicator_bar_count"),
            (pl.cum_count("close").over("ticker") >= 35).alias("macd_ready"),
            (pl.cum_count("close").over("ticker") >= 20).alias("tema_ready"),
        )
    )
    up = pl.when(pl.col("body") > 0).then(pl.col("body")).otherwise(0.0)
    down = pl.when(pl.col("body") < 0).then(-pl.col("body")).otherwise(0.0)
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
            pl.col("volume").rolling_mean(20).over("ticker").alias("volume_sma20"),
            pl.col("dollar_volume").rolling_mean(20).over("ticker").alias("dollar_volume_sma20"),
            rolling_z("return_1", 20, "return_z20"),
            rolling_z("bar_range", 20, "range_z20"),
            rolling_z("volume", 20, "volume_z20"),
        )
        .with_columns(
            (pl.col("bb_mid20") + 2.0 * pl.col("_bb_std20")).alias("bb_upper20"),
            (pl.col("bb_mid20") - 2.0 * pl.col("_bb_std20")).alias("bb_lower20"),
            pl.when(pl.col("bb_mid20") > 0).then((4.0 * pl.col("_bb_std20")) / pl.col("bb_mid20")).otherwise(0.0).alias("bb_width20"),
            ((pl.col("donchian_high20") + pl.col("donchian_low20")) / 2.0).alias("donchian_mid20"),
            pl.col("ema20").alias("keltner_mid20"),
            (pl.col("ema20") + 2.0 * pl.col("atr14")).alias("keltner_upper20"),
            (pl.col("ema20") - 2.0 * pl.col("atr14")).alias("keltner_lower20"),
            pl.when(pl.col("volume_sma20") > 0).then(pl.col("volume") / pl.col("volume_sma20")).otherwise(0.0).alias("relative_volume20"),
            pl.when(pl.col("dollar_volume_sma20") > 0).then(pl.col("dollar_volume") / pl.col("dollar_volume_sma20")).otherwise(0.0).alias("relative_dollar_volume20"),
        )
    )
    typical_money_flow = pl.col("hlc3") * pl.col("volume")
    positive_flow = pl.when(pl.col("hlc3") > pl.col("hlc3").shift(1).over("ticker")).then(typical_money_flow).otherwise(0.0)
    negative_flow = pl.when(pl.col("hlc3") < pl.col("hlc3").shift(1).over("ticker")).then(typical_money_flow).otherwise(0.0)
    money_flow_multiplier = pl.when(pl.col("high") > pl.col("low")).then(((pl.col("close") - pl.col("low")) - (pl.col("high") - pl.col("close"))) / (pl.col("high") - pl.col("low"))).otherwise(0.0)
    frame = (
        frame.with_columns(
            pl.when(pl.col("close") >= pl.col("close").shift(1).over("ticker")).then(pl.col("volume")).otherwise(-pl.col("volume")).cum_sum().over("ticker").alias("obv"),
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
            ((pl.col("high") < pl.col("high").shift(1).over("ticker")) & (pl.col("low") > pl.col("low").shift(1).over("ticker"))).alias("inside_bar"),
            ((pl.col("high") > pl.col("high").shift(1).over("ticker")) & (pl.col("low") < pl.col("low").shift(1).over("ticker"))).alias("outside_bar"),
            ((pl.col("close") > pl.col("open")) & (pl.col("open") < pl.col("close").shift(1).over("ticker")) & (pl.col("close") > pl.col("open").shift(1).over("ticker"))).alias("bullish_engulfing"),
            ((pl.col("close") < pl.col("open")) & (pl.col("open") > pl.col("close").shift(1).over("ticker")) & (pl.col("close") < pl.col("open").shift(1).over("ticker"))).alias("bearish_engulfing"),
            (pl.col("bar_range") <= pl.col("bar_range").rolling_min(4).over("ticker")).alias("nr4"),
            (pl.col("bar_range") <= pl.col("bar_range").rolling_min(7).over("ticker")).alias("nr7"),
            (pl.col("is_green").cast(pl.Int32)).cum_sum().over("ticker").alias("consecutive_green"),
            (pl.col("is_red").cast(pl.Int32)).cum_sum().over("ticker").alias("consecutive_red"),
            (pl.col("high") >= pl.col("high").rolling_max(20).over("ticker")).alias("breaks_high20"),
            (pl.col("low") <= pl.col("low").rolling_min(20).over("ticker")).alias("breaks_low20"),
            pl.when(pl.col("donchian_high20") > 0).then((pl.col("close") / pl.col("donchian_high20")) - 1.0).otherwise(0.0).alias("pullback_from_high20_pct"),
            ((pl.col("close") > pl.col("vwap")) & (pl.col("close").shift(1).over("ticker") <= pl.col("vwap").shift(1).over("ticker"))).alias("reclaim_vwap"),
            ((pl.col("close") < pl.col("vwap")) & (pl.col("close").shift(1).over("ticker") >= pl.col("vwap").shift(1).over("ticker"))).alias("breakdown_vwap"),
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
            (pl.col("high") >= pl.col("high").rolling_max(3, center=True).over("ticker")).alias("swing_high_3"),
            (pl.col("low") <= pl.col("low").rolling_min(3, center=True).over("ticker")).alias("swing_low_3"),
            (pl.col("high") >= pl.col("high").rolling_max(5, center=True).over("ticker")).alias("swing_high_5"),
            (pl.col("low") <= pl.col("low").rolling_min(5, center=True).over("ticker")).alias("swing_low_5"),
            (pl.col("high") > pl.col("high").shift(1).over("ticker")).alias("higher_high"),
            (pl.col("low") < pl.col("low").shift(1).over("ticker")).alias("lower_low"),
            (pl.col("close") > pl.col("high").shift(1).rolling_max(20).over("ticker")).alias("bos_up"),
            (pl.col("close") < pl.col("low").shift(1).rolling_min(20).over("ticker")).alias("bos_down"),
            pl.when(pl.col("ema20") > pl.col("ema50")).then(pl.lit("up")).when(pl.col("ema20") < pl.col("ema50")).then(pl.lit("down")).otherwise(pl.lit("range")).alias("trend_regime"),
            pl.lit(None).cast(pl.Int32).alias("bars_since_high20"),
            pl.lit(None).cast(pl.Int32).alias("bars_since_low20"),
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
    return frame.drop([col for col in frame.columns if col.startswith("_")])


def select_feature_group(frame: pl.DataFrame, group: str) -> pl.DataFrame:
    columns = [column for column in FEATURE_COLUMNS[group] if column in frame.columns]
    return frame.select(columns)
