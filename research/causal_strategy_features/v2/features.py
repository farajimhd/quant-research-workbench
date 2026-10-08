"""Past-volatility normalized research channels; no trading authority."""
import polars as pl
from research.causal_strategy_features.v1.features import (
    BASE_COLUMNS, completed_channels as completed_base_channels,
)


def completed_channels(frame, *, timeframe_minutes, lookback_bars=5):
    bars = completed_base_channels(frame, timeframe_minutes=timeframe_minutes,
                                   lookback_bars=lookback_bars)
    bars = bars.with_columns(
        pl.col('return').shift(1).rolling_std(
            lookback_bars, min_samples=lookback_bars, ddof=0
        ).over('ticker').alias('prior_return_volatility'),
        pl.col('return').is_not_null().cast(pl.Int32).shift(1).rolling_sum(
            lookback_bars, min_samples=lookback_bars
        ).over('ticker').alias('prior_return_count'),
        pl.lit(timeframe_minutes).alias('bar_duration_minutes'),
    )
    known = ((pl.col('prior_return_count') == lookback_bars) &
             (pl.col('fifth_prior_bar') == pl.col('bar_id')-lookback_bars) &
             pl.col('prior_return_volatility').is_finite() &
             (pl.col('prior_return_volatility') > 0))
    return bars.with_columns(
        pl.when(known).then(pl.col('return')/pl.col('prior_return_volatility'))
          .alias('return_volatility_units'),
        pl.when(known).then(pl.col('range_fraction')/pl.col('prior_return_volatility'))
          .alias('range_volatility_units'),
        known.fill_null(False).alias('volatility_baseline_available'),
    )
