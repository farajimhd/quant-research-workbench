"""V7 cost-aware coefficients for completed-second MACD close episodes."""
from __future__ import annotations

import polars as pl

from research.rl_trading.v1 import phase2_values as legacy
from research.rl_trading.v1.phase1_close_labels import MIN_ACTIONABLE_SECONDS


VERSION = "hindsight-greedy-close-episodes-v7"
DEFAULT_FIXED_FEE_PER_SHARE = 0.005


def coefficients(frame: pl.DataFrame, gamma: float, *,
                 fee_per_share: float = DEFAULT_FIXED_FEE_PER_SHARE,
                 min_volume_60s: float = 0., min_trades_60s: int = 0) -> pl.DataFrame:
    """Vectorized Phase 2 values; small-order minimum remains a Phase 3 check.

    ``open_value_per_share`` is discounted gross value. The ranking subtracts
    a buy and a sale fee. The published fixed order minimum/cap depends on
    quantity, so the teacher must recompute exact order-level net value.
    """
    if not 0 <= fee_per_share <= 1:
        raise ValueError("Invalid per-share fee proxy")
    values = legacy.coefficients(frame, gamma, 0., valuation_basis="price_action",
        min_volume_60s=min_volume_60s, min_trades_60s=min_trades_60s)
    episode_rows = pl.concat([
        frame.select("time_us", "ticker", "listing_id", pl.lit(side).alias("side"),
            pl.col(f"{side}_episode_id").alias("episode_id"),
            pl.col(f"{side}_episode_stage").alias("episode_stage"))
        for side in ("long", "short")])
    values = values.join(episode_rows,
        on=["time_us", "ticker", "listing_id", "side"], how="left",
        validate="1:1",maintain_order="left")
    eligible = (pl.col("can_open") & pl.col("open_value_available") &
                (pl.col("hold_seconds") >= MIN_ACTIONABLE_SECONDS) &
                pl.col("episode_stage").is_in(("long_entry", "long_candidate",
                                                    "short_entry", "short_candidate")))
    values = values.with_columns(
        pl.lit(fee_per_share).alias("buy_fee_per_share_proxy"),
        pl.lit(fee_per_share).alias("sell_fee_per_share_proxy"),
        pl.when(eligible).then(pl.col("open_value_per_share") - 2 * fee_per_share)
          .alias("open_net_value_per_share"),
        eligible.alias("can_open"),
        eligible.alias("open_value_available"),
        eligible.alias("value_available"),
    ).with_columns(
        pl.when(pl.col("can_open") & (pl.col("open_net_value_per_share") > 0))
          .then(pl.col("open_net_value_per_share") /
                (pl.col("capital_per_share") + fee_per_share))
          .alias("open_value_per_dollar"),
    ).with_columns(
        (pl.col("ticker") + pl.lit("_") +
         pl.col("episode_id").cast(pl.String)).alias("episode_key"),
        (pl.lit(str(frame["time_us"][0])) + pl.lit(":") +
         pl.col("listing_id") + pl.lit(":") +
         pl.col("episode_id").cast(pl.String)).alias("episode_uid"),
    )
    return values
