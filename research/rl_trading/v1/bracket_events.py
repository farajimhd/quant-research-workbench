"""Sparse 100 ms bracket-event evidence for one confirmed long fill.

This bounds *possible* target fills with certified executions at or above the
limit. It does not prove queue priority or a stop-market execution price.
Those require an explicit replay fill scenario before booking account P&L.
"""
from __future__ import annotations

import math

import polars as pl


VERSION = 'rl-trading-sparse-bracket-events-v1'


def bracket_events(*, ticker: str, fill_us: int, exit_us: int,
                   shares: float, stop: float, target: float,
                   buckets: pl.DataFrame, price_levels: pl.DataFrame,
                   ) -> pl.DataFrame:
    """Return at most one row per touched 100 ms bucket.

    ``buckets`` contains only the selected ticker's held-period extrema;
    ``price_levels`` contains only execution levels for those buckets. The
    caller must bind both to pinned ARTE attempts. Target caps are cumulative
    and cannot exceed the filled entry size. Stop/target overlap in one bucket
    is unresolved, since their within-bucket order is not observable.
    """
    if (not ticker or type(fill_us) is not int or type(exit_us) is not int or
            exit_us <= fill_us or not all(math.isfinite(x) for x in
            (shares, stop, target)) or shares <= 0 or stop <= 0 or
            target <= stop):
        raise ValueError('Invalid bracket position or levels')
    needed_bars = {'boundary_us', 'high', 'low', 'extremes_valid'}
    needed_levels = {'boundary_us', 'price_int', 'execution_volume'}
    if not needed_bars <= set(buckets.columns) or not needed_levels <= set(price_levels.columns):
        raise ValueError('Missing certified bracket evidence columns')
    if (buckets.select('boundary_us').n_unique() != buckets.height or
            price_levels.select('boundary_us', 'price_int').n_unique() != price_levels.height or
            buckets.filter((pl.col('boundary_us') <= fill_us) |
                           (pl.col('boundary_us') > exit_us) |
                           ((pl.col('boundary_us') % 100_000) != 0) |
                           ~pl.col('extremes_valid') |
                           ~pl.col('high').is_finite() |
                           ~pl.col('low').is_finite() |
                           (pl.col('low') <= 0) |
                           (pl.col('high') < pl.col('low'))).height or
            price_levels.filter((pl.col('boundary_us') <= fill_us) |
                                (pl.col('boundary_us') > exit_us) |
                                (pl.col('price_int') <= 0) |
                                ~pl.col('execution_volume').is_finite() |
                                (pl.col('execution_volume') <= 0)).height):
        raise ValueError('Invalid or duplicate certified bracket evidence')
    if price_levels.join(buckets.select('boundary_us'), on='boundary_us', how='anti').height:
        raise ValueError('Price level has no matching certified 100 ms bucket')
    volume = (price_levels.filter(pl.col('price_int') >= math.ceil(target*10_000-1e-7))
        .group_by('boundary_us').agg(
            pl.col('execution_volume').sum().alias('target_volume_cap')))
    touched = (buckets.join(volume, on='boundary_us', how='left')
        .with_columns(pl.col('target_volume_cap').fill_null(0.0))
        .with_columns((pl.col('high') >= target).alias('target_touched'),
                      (pl.col('low') <= stop).alias('stop_touched'))
        .filter(pl.col('target_touched') | pl.col('stop_touched'))
        .sort('boundary_us'))
    if not touched.height:
        return pl.DataFrame(schema={'ticker': pl.String, 'boundary_us': pl.Int64,
            'event': pl.String, 'target_fill_cap': pl.Float64,
            'remaining_cap': pl.Float64})
    # Once a stop touches or ordering becomes ambiguous, later target bars
    # cannot be credited. The stop's market execution remains a separate step.
    ending = touched.filter(pl.col('stop_touched'))
    if ending.height:
        touched = touched.filter(pl.col('boundary_us') <= ending['boundary_us'][0])
    touched = touched.with_columns(
        pl.when(pl.col('stop_touched') & pl.col('target_touched'))
          .then(pl.lit('ambiguous_stop_target'))
          .when(pl.col('stop_touched')).then(pl.lit('stop_trigger'))
          .otherwise(pl.lit('target_touch')).alias('event'))
    # No hypothetical target fill is booked in an ambiguous bucket.
    touched = touched.with_columns(
        pl.when(pl.col('event') == 'target_touch')
          .then(pl.col('target_volume_cap')).otherwise(0.0)
          .alias('_usable_cap'))
    touched = touched.with_columns(pl.col('_usable_cap').cum_sum().alias('_cum_cap'))
    touched = touched.with_columns(
        (pl.col('_cum_cap').clip(upper_bound=shares) -
         (pl.col('_cum_cap') - pl.col('_usable_cap')).clip(upper_bound=shares))
        .alias('target_fill_cap'),
        (shares - pl.col('_cum_cap').clip(upper_bound=shares))
        .alias('remaining_cap'))
    fully_filled = touched.filter(pl.col('remaining_cap') <= 0)
    if fully_filled.height:
        touched = touched.filter(pl.col('boundary_us') <= fully_filled['boundary_us'][0])
    return touched.select(pl.lit(ticker).alias('ticker'), 'boundary_us',
                          'event', 'target_fill_cap', 'remaining_cap')
