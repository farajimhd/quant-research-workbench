"""Causal native candle shape and participation channels for research."""
import polars as pl

from research.causal_strategy_features.v3.native_bars import (
    IDENTITY, native_channels as base_channels,
)


def native_channels(frame, *, resolutions_ms, through_day_boundary_ms,
                    lookback_bars=5, volatility_lookback_bars=5):
    """Retain source identity and absolute values; normalize against past bars.

    This projection grants no certification or strategy execution authority.
    Each rolling baseline belongs to one build/day/ticker/attempt/resolution.
    """
    if type(volatility_lookback_bars) is not int or volatility_lookback_bars < 2:
        raise ValueError('Volatility baseline requires at least two prior bars')
    bars = base_channels(frame, resolutions_ms=resolutions_ms,
                         through_day_boundary_ms=through_day_boundary_ms,
                         lookback_bars=lookback_bars)
    n = volatility_lookback_bars
    bars = bars.with_columns(
        pl.col('close_return').shift(1).rolling_std(n, min_samples=n, ddof=0)
          .over(IDENTITY).alias('prior_return_volatility'),
        pl.col('close_return').is_not_null().cast(pl.Int32).shift(1)
          .rolling_sum(n, min_samples=n).over(IDENTITY).alias('prior_return_count'),
        pl.col('bucket_index').shift(n).over(IDENTITY).alias('volatility_first_bucket'),
        pl.col('trade_count').shift(1).rolling_mean(lookback_bars, min_samples=lookback_bars)
          .over(IDENTITY).alias('prior_trade_count_mean'),
        (pl.col('resolution_ms') / 1000).alias('bar_duration_seconds'),
        (pl.col('available_day_boundary_ms') / 86400000).alias('source_day_fraction'),
        (pl.col('close_return') - pl.col('open_return')).alias('body_return'),
        (pl.col('high_return') - pl.max_horizontal('open_return', 'close_return'))
          .alias('upper_wick_return'),
        (pl.min_horizontal('open_return', 'close_return') - pl.col('low_return'))
          .alias('lower_wick_return'),
    )
    known = ((pl.col('prior_return_count') == n) &
             (pl.col('volatility_first_bucket') == pl.col('bucket_index') - n) &
             pl.col('prior_return_volatility').is_finite() &
             (pl.col('prior_return_volatility') > 0)).fill_null(False)
    return bars.with_columns(
        known.alias('volatility_baseline_available'),
        *[pl.when(known).then(pl.col(channel) / pl.col('prior_return_volatility'))
          .alias(channel.replace('_return', '_volatility_units'))
          for channel in ('open_return', 'high_return', 'low_return', 'close_return',
                          'body_return', 'upper_wick_return', 'lower_wick_return')],
        pl.when((pl.col('baseline_first_bucket') == pl.col('bucket_index') - lookback_bars) &
                (pl.col('prior_trade_count_mean') > 0))
          .then(pl.col('trade_count') / pl.col('prior_trade_count_mean'))
          .alias('relative_trade_count'),
    )
