"""V5 hindsight episodes on the completed one-second decision-price clock.

This is label-side future information. It must never enter a causal observation.
"""
from __future__ import annotations

import polars as pl

from research.rl_trading.v1 import phase1_labels as legacy


VERSION = "hindsight-phase1-arte-close-episodes-v5"
MIN_ACTIONABLE_SECONDS = 2


def targets(bars: pl.DataFrame, episodes: pl.DataFrame, lookback: int = 2) -> dict:
    """Choose the cheapest close near MACD start and best later episode close.

    The entry may precede the MACD crossover by ``lookback`` completed seconds.
    Entry and exit are distinct one-second decision clocks. Every valid episode
    receives a consecutive ID within its listing and session.
    """
    if type(lookback) is not int or not 0 <= lookback <= 30:
        raise ValueError("lookback must be an integer from 0 through 30")
    prices = (bars.filter((pl.col("resolution_ms") == 1000) &
                          (pl.col("price_valid") == 1))
              .select("time_us", pl.col("close").alias("decision_price"))
              .sort("time_us"))
    if prices["time_us"].n_unique() != prices.height or prices.filter(
        (pl.col("decision_price") <= 0) | ~pl.col("decision_price").is_finite()
    ).height:
        raise ValueError("Invalid completed one-second close series")
    windows = episodes.with_columns(
        (pl.col("start_us") - lookback * 1_000_000).alias("left_us"))
    entries = prices.join_where(
        windows, pl.col("time_us") >= pl.col("left_us"),
        pl.col("time_us") <= pl.col("start_us"))
    entries = (entries.with_columns(
        pl.when(pl.col("direction") == 1).then(pl.col("decision_price"))
          .otherwise(-pl.col("decision_price")).alias("_entry_rank"))
        .sort("position_number", "_entry_rank", "time_us")
        .unique("position_number", keep="first", maintain_order=True)
        .select("position_number", pl.col("time_us").alias("entry_us"),
                pl.col("decision_price").alias("entry_price")))
    exits = (prices.join_asof(episodes.sort("start_us"), left_on="time_us",
                             right_on="start_us")
        .filter(pl.col("time_us") < pl.col("end_us"))
        .join(entries, on="position_number", how="inner")
        .filter(pl.col("time_us") >= pl.col("entry_us") + MIN_ACTIONABLE_SECONDS * 1_000_000)
        .with_columns(pl.when(pl.col("direction") == 1)
            .then(-pl.col("decision_price"))
            .otherwise(pl.col("decision_price")).alias("_exit_rank"))
        .sort("position_number", "_exit_rank", "time_us")
        .unique("position_number", keep="first", maintain_order=True))
    valid = (exits.filter((pl.col("decision_price") - pl.col("entry_price")) *
                          pl.col("direction") > 0)
        .sort("position_number")
        .with_row_index("episode_id", offset=1))
    positions = valid.select(
        pl.col("episode_id").alias("position_number"),
        pl.col("position_number").alias("macd_interval_id"),
        pl.when(pl.col("direction") == 1).then(pl.lit("long"))
          .otherwise(pl.lit("short")).alias("direction"),
        (pl.col("entry_us") / 1e6).alias("entry_time"),
        (pl.col("time_us") / 1e6).alias("exit_time"),
        "entry_price", pl.col("decision_price").alias("exit_price"),
        (pl.col("start_us") / 1e6).alias("macd_open"),
        (pl.col("end_us") / 1e6).alias("macd_close"),
        (pl.col("end_us") / 1e6).alias("label_available_at")
    ).to_dicts()
    return dict(positions=positions, interval_count=episodes.height,
        position_count=len(positions),
        interval_rejections=dict(no_entry_price=episodes.height-entries.height,
            no_actionable_exit=entries.height-exits.height,
            no_directional_move=exits.height-valid.height),
        target_clock="completed_1s_close")


def decision_values(day, bars, selected_targets, *, liquidation_us=None,
                    lookback_seconds=2):
    """Dense grid plus episode stage and an unambiguous per-session key."""
    grid = legacy.decision_values(day, bars, selected_targets,
                                  liquidation_us=liquidation_us,
                                  price_resolution_ms=1000)
    grid = grid.with_columns(
        (pl.col("price_us") == pl.col("time_us")).fill_null(False)
          .alias("decision_close_fresh"))
    grid = grid.with_columns(pl.col("decision_close_fresh").alias("price_valid"))
    for side in ("long", "short"):
        episode = pl.col(f"{side}_target_id")
        entry = pl.col(f"{side}_entry_us")
        remaining = pl.col(f"{side}_hold_seconds")
        stage = (pl.when(episode.is_null() | (episode == 0))
            .then(pl.lit("none"))
            .when(pl.col("time_us") < entry - lookback_seconds * 1_000_000)
            .then(pl.lit("none"))
            .when(pl.col("time_us") < entry).then(pl.lit(f"{side}_setup"))
            .when(remaining < MIN_ACTIONABLE_SECONDS)
            .then(pl.lit("too_late_to_open"))
            .when(pl.col("time_us") == entry).then(pl.lit(f"{side}_entry"))
            .otherwise(pl.lit(f"{side}_candidate")))
        grid = grid.with_columns(
            stage.alias(f"{side}_episode_stage"),
            pl.when(episode > 0).then(episode).alias(f"{side}_episode_id"))
    return grid
