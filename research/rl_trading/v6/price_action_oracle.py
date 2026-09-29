"""Price-action-only oracle stop and target for idealized V6 long episodes.

This teacher sees future one-second highs/lows only on the *label* side. It
does not read quotes, displayed size, spread, or broker execution. The stop
and target are hypothetical orders; replay separately measures executable
fills and rewards. Sparse paths with an unobserved second are not called
perfectly protected episodes.
"""
from __future__ import annotations

import math

import polars as pl


VERSION = 'rl-trading-price-action-bracket-oracle-v6-1'
MIN_HOLD_SECONDS = 3


def labels(positions: pl.DataFrame, bars: pl.DataFrame, *,
           tick_size: float, offset_ticks: int = 1) -> pl.DataFrame:
    """Return oracle geometry from only the episode's one-second candles.

    The hypothetical entry occurs at the completed candle close `entry_us`.
    Three prior completed candles define the swing-low candidate. Active
    extrema run from the next completed candle through `exit_us`, inclusive.
    All expected one-second bars must be present and valid for a label.
    """
    required_positions = {'ticker', 'episode_uid', 'entry_us', 'exit_us',
                          'entry_price'}
    required_bars = {'ticker', 'time_us', 'high', 'low', 'extremes_valid'}
    if (not required_positions <= set(positions.columns) or
            not required_bars <= set(bars.columns) or
            not math.isfinite(tick_size) or tick_size <= 0 or
            type(offset_ticks) is not int or offset_ticks < 1):
        raise ValueError('Invalid price-action bracket source or tick policy')
    if (positions['episode_uid'].n_unique() != positions.height or
            positions.select('ticker', 'entry_us').n_unique() != positions.height or
            bars.select('ticker', 'time_us').n_unique() != bars.height or
            positions.filter((pl.col('exit_us') <= pl.col('entry_us')) |
                ((pl.col('exit_us')-pl.col('entry_us')) % 1_000_000 != 0) |
                (pl.col('entry_price') <= 0)).height or
            positions['entry_price'].null_count() or
            bars['extremes_valid'].null_count() or
            bars.filter(~pl.col('extremes_valid').cast(pl.Int8)
                        .is_in([0, 1])).height):
        raise ValueError('Duplicate, malformed, or nonsecond oracle input')
    valid = bars.filter((pl.col('extremes_valid') == 1) &
        pl.col('high').is_finite() & pl.col('low').is_finite() &
        (pl.col('low') > 0) & (pl.col('high') >= pl.col('low')))
    path = (positions.select('ticker', 'episode_uid', 'entry_us', 'exit_us')
        .join_where(valid,
            pl.col('ticker') == pl.col('ticker_right'),
            pl.col('time_us') >= pl.col('entry_us')-3_000_000,
            pl.col('time_us') <= pl.col('exit_us')))
    before = path.filter(pl.col('time_us') < pl.col('entry_us'))
    active = path.filter(pl.col('time_us') > pl.col('entry_us'))
    swing = before.group_by('episode_uid').agg(
        pl.col('low').min().alias('swing_low_3s'),
        pl.len().alias('pre_entry_bars'))
    outcome = active.group_by('episode_uid').agg(
        pl.col('low').min().alias('held_min_low'),
        pl.col('high').max().alias('held_max_high'),
        pl.len().alias('held_bars'))
    result = (positions.select('ticker', 'episode_uid', 'entry_us',
                               'exit_us', 'entry_price')
        .join(swing, on='episode_uid', how='left', validate='1:1')
        .join(outcome, on='episode_uid', how='left', validate='1:1')
        .with_columns(((pl.col('exit_us')-pl.col('entry_us'))//1_000_000)
                      .alias('expected_held_bars'))
        .with_columns((
            (pl.col('pre_entry_bars') == 3) &
            (pl.col('held_bars') == pl.col('expected_held_bars')) &
            (pl.col('held_bars') >= MIN_HOLD_SECONDS)
        ).fill_null(False).alias('path_complete')))
    stop = ((pl.min_horizontal('swing_low_3s', 'held_min_low')-
             offset_ticks*tick_size)/tick_size).floor()*tick_size
    target = (pl.col('held_max_high')/tick_size).floor()*tick_size
    result = result.with_columns(
        pl.when(pl.col('path_complete')).then(stop).alias('oracle_stop'),
        pl.when(pl.col('path_complete')).then(target).alias('oracle_target'))
    return result.with_columns((
        pl.col('path_complete') &
        (pl.col('oracle_stop') > 0) &
        (pl.col('oracle_stop') < pl.col('entry_price')) &
        (pl.col('oracle_target') > pl.col('entry_price'))
    ).fill_null(False).alias('label_available'))
