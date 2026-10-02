"""Prepared columnar necessary condition; no Strategy39 execution admission."""
import polars as pl

from .backtest_strategy_liquidity_fade import compile_liquidity_fade_observations

HALF_RISK_ACTIVITY_FADE = 'half_risk_activity_fade'


def compile_half_risk_liquidity_observations(frame: pl.DataFrame) -> pl.DataFrame:
    """Add an activity bit (N,) over the parent's certified-shape observations.

    The parent owns source identity, ordering, contiguous four-bar windows and
    missing-value handling. Decimal integer sums already cover all UInt64
    counts. One additional native Polars expression produces the more sensitive
    activity condition without changing the parent's quarter-rate flag. Price,
    negative MACD, quote freshness, first-held and OMS checks remain scalar
    position-dependent requirements; this flag alone cannot propose an exit.
    """
    if type(frame) is not pl.DataFrame or HALF_RISK_ACTIVITY_FADE in frame.columns:
        raise ValueError('Half-risk compiler cannot overwrite producer evidence')
    compiled = compile_liquidity_fade_observations(frame)
    return compiled.with_columns(
        (pl.col('activity_window_complete')
         & (pl.col('prior_10s_trade_count') > 0)
         & (2 * pl.col('recent_10s_trade_count') <= pl.col('prior_10s_trade_count')))
        .fill_null(False).alias(HALF_RISK_ACTIVITY_FADE),
    )
