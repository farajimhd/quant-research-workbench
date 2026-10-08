"""Native-bar schema research projection; caller must certify source authority."""
import polars as pl
from src.backend.backtest_market_data import FIXED_RESOLUTIONS_MS

IDENTITY = ('build_id','session_date','ticker','attempt_id','resolution_ms')
COLUMNS = (*IDENTITY,'bucket_index','open_int','high_int','low_int','close_int',
           'execution_volume','execution_notional','trade_count','price_valid','extremes_valid')


def native_channels(frame, *, resolutions_ms, through_day_boundary_ms, lookback_bars=5):
    """One or more identified units; preserve invalid rows and nullable features.

    The boundary is milliseconds since local source midnight, not the native
    Backtest offset since 04:00. No mutable current fundamentals are consumed.
    """
    if (type(resolutions_ms) is not tuple or not resolutions_ms or
            len(set(resolutions_ms))!=len(resolutions_ms) or
            any(type(r) is not int or r not in FIXED_RESOLUTIONS_MS for r in resolutions_ms)):
        raise ValueError('Declared native producer resolutions required')
    if type(through_day_boundary_ms) is not int or not 0<=through_day_boundary_ms<=86400000:
        raise ValueError('Explicit local source-day completed boundary required')
    if type(lookback_bars) is not int or lookback_bars<=0:
        raise ValueError('Positive integer trailing baseline required')
    raw = frame.select(COLUMNS)
    if any(raw[name].null_count() for name in COLUMNS):
        raise ValueError('Native source required fields contain nulls')
    if raw.select(*IDENTITY,'bucket_index').n_unique()!=raw.height:
        raise ValueError('Duplicate native source identity/bucket')
    if raw.filter(~pl.col('resolution_ms').is_in(resolutions_ms) |
                  ~pl.col('price_valid').is_in([0,1]) | ~pl.col('extremes_valid').is_in([0,1])).height:
        raise ValueError('Foreign native resolution or flags')
    raw = raw.with_columns(((pl.col('bucket_index').cast(pl.Int64)+1)*pl.col('resolution_ms'))
                           .alias('available_day_boundary_ms'))
    raw = raw.filter(pl.col('available_day_boundary_ms')<=through_day_boundary_ms).sort(*IDENTITY,'bucket_index')
    valid = ((pl.col('price_valid')==1)&(pl.col('extremes_valid')==1)&(pl.col('low_int')>0)&
             (pl.col('low_int')<=pl.col('open_int'))&(pl.col('open_int')<=pl.col('high_int'))&
             (pl.col('low_int')<=pl.col('close_int'))&(pl.col('close_int')<=pl.col('high_int')))
    if raw.filter(~pl.col('execution_volume').is_finite()|~pl.col('execution_notional').is_finite()|
                  (pl.col('execution_volume')<0)|(pl.col('execution_notional')<0)).height:
        raise ValueError('Native participation is invalid')
    bars = raw.with_columns(valid.alias('candle_available'),
                            pl.when(valid).then(pl.col('close_int')).alias('valid_close'))
    bars = bars.with_columns(pl.col('valid_close').shift(1).over(IDENTITY).alias('prior_close'),
        pl.col('bucket_index').shift(1).over(IDENTITY).alias('prior_bucket'),
        pl.col('bucket_index').shift(lookback_bars).over(IDENTITY).alias('baseline_first_bucket'),
        pl.col('execution_volume').shift(1).rolling_mean(lookback_bars,min_samples=lookback_bars)
          .over(IDENTITY).alias('prior_volume_mean'))
    prior = (pl.col('prior_bucket')==pl.col('bucket_index')-1)&(pl.col('prior_close')>0)&pl.col('candle_available')
    return bars.with_columns(*[
        pl.when(prior).then(pl.col(name)/pl.col('prior_close')-1).alias(channel)
        for name,channel in [('open_int','open_return'),('high_int','high_return'),
                             ('low_int','low_return'),('close_int','close_return')]
    ], pl.when((pl.col('baseline_first_bucket')==pl.col('bucket_index')-lookback_bars)&
               (pl.col('prior_volume_mean')>0)).then(pl.col('execution_volume')/pl.col('prior_volume_mean'))
          .alias('relative_execution_volume'))
