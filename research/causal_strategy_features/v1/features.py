"""Vectorized development research channels, not certified trading admission.

Each call represents one source-identified ticker partition of ONE session.
Minute offsets use the source session clock. Higher timeframe bars are aligned
on that clock and available only at their end. Missing minutes stay missing.
Future labels, split adjustments and fundamentals are never inferred here.
"""
import polars as pl

BASE_COLUMNS = ('ticker', 'minute', 'decision_minute', 'close_int', 'high_int', 'low_int', 'execution_volume', 'execution_notional', 'trade_count', 'valid_rows', 'bucket_rows')

def completed_channels(frame, *, timeframe_minutes, lookback_bars=5):
    """Return completed bars; select only declared source columns, never labels."""
    if type(timeframe_minutes) is not int or timeframe_minutes <= 0:
        raise ValueError('Positive integer timeframe required')
    if type(lookback_bars) is not int or lookback_bars <= 0:
        raise ValueError('Positive integer trailing lookback required')
    raw = frame.select(BASE_COLUMNS)
    if raw.select('ticker','minute').n_unique() != raw.height:
        raise ValueError('Duplicate ticker/minute source rows')
    if raw.filter(pl.col('minute').is_null() | pl.col('ticker').is_null() |
                  (pl.col('decision_minute') != pl.col('minute')+1) |
                  pl.col('decision_minute').is_null()).height:
        raise ValueError('Source minute availability differs')
    timeframe = timeframe_minutes
    features = raw.filter((pl.col('valid_rows')==2)&(pl.col('bucket_rows')==2)&
                          (pl.col('close_int')>0)).sort('ticker','minute')
    if features.filter((pl.col('low_int')>pl.col('close_int')) |
                       (pl.col('close_int')>pl.col('high_int')) |
                       ~pl.col('execution_volume').is_finite() |
                       ~pl.col('execution_notional').is_finite() |
                       (pl.col('execution_volume')<0) |
                       (pl.col('execution_notional')<0)).height:
        raise ValueError('Valid source candle or participation differs')
    bars = features.with_columns((pl.col('minute')//timeframe).alias('bar_id')).group_by('ticker','bar_id',maintain_order=True).agg(
      pl.len().alias('observed_minutes'),pl.col('minute').min().alias('first_minute'),
      pl.col('minute').max().alias('last_minute'),pl.col('close_int').last().alias('close'),
      pl.col('high_int').max().alias('high'),pl.col('low_int').min().alias('low'),
      pl.col('execution_volume').sum().alias('volume'),pl.col('execution_notional').sum().alias('dollar_volume'),
      pl.col('trade_count').sum().alias('trades')).filter(pl.col('observed_minutes')==timeframe).sort('ticker','bar_id')
    bars = bars.with_columns(((pl.col('bar_id')+1)*timeframe).alias('available_minute'),
      pl.col('close').shift(1).over('ticker').alias('prior_close'),
      pl.col('bar_id').shift(1).over('ticker').alias('prior_bar'),
      pl.col('bar_id').shift(lookback_bars).over('ticker').alias('fifth_prior_bar'),
      pl.col('volume').shift(1).rolling_mean(lookback_bars,min_samples=lookback_bars).over('ticker').alias('prior_volume_mean'),
      pl.col('trades').shift(1).rolling_mean(lookback_bars,min_samples=lookback_bars).over('ticker').alias('prior_trades_mean'))
    assert bars.filter(pl.col('last_minute')>=pl.col('available_minute')).is_empty()
    consecutive = (pl.col('prior_bar')==pl.col('bar_id')-1)&(pl.col('prior_close')>0)
    volume_known = (pl.col('fifth_prior_bar')==pl.col('bar_id')-lookback_bars)&(pl.col('prior_volume_mean')>0)
    bars = bars.with_columns(
      pl.when(consecutive).then(pl.col('close')/pl.col('prior_close')-1).alias('return'),
      ((pl.col('high')-pl.col('low'))/pl.col('close')).alias('range_fraction'),
      pl.when(pl.col('high')>pl.col('low')).then((pl.col('close')-pl.col('low'))/(pl.col('high')-pl.col('low'))).alias('close_location'),
      pl.when(volume_known).then(pl.col('volume')/pl.col('prior_volume_mean')).alias('relative_volume'),
      pl.when(volume_known&(pl.col('prior_trades_mean')>0)).then(pl.col('trades')/pl.col('prior_trades_mean')).alias('relative_trades'))
    return bars
