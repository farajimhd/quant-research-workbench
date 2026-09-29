"""Price-action-only oracle stop and target for idealized V6 long episodes.

This teacher sees future one-second highs/lows only on the *label* side. It
does not read quotes, displayed size, spread, or broker execution. The stop
and target are hypothetical orders; replay separately measures executable
fills and rewards. An absent one-second trade candle contributes no price;
the output reports that clock gap rather than inventing a high or low.
"""
from __future__ import annotations

import polars as pl


VERSION = 'rl-trading-price-action-bracket-oracle-v6-3'
MIN_HOLD_SECONDS = 3


def geometry(positions: pl.DataFrame, bars: pl.DataFrame) -> pl.DataFrame:
    """Aggregate observed price-action extrema without execution assumptions.

    The hypothetical entry occurs at the completed candle close `entry_us`.
    Observed candles in the three prior clock seconds define the optional
    swing-low candidate. Active extrema run from the next observed completed
    candle through `exit_us`, inclusive. Missing trade candles are not
    forward-filled; their count remains visible for uncertainty review. The
    This can be certified before a price-grid policy is chosen; it is not a
    complete stop/target label or a teacher trajectory.
    """
    required_positions = {'ticker', 'episode_uid', 'entry_us', 'exit_us',
                          'entry_price'}
    required_bars = {'ticker', 'time_us', 'high', 'low', 'extremes_valid'}
    if (not required_positions <= set(positions.columns) or
            not required_bars <= set(bars.columns)):
        raise ValueError('Invalid price-action bracket source')
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
        # A child target cannot act on an earlier intrabar high. Keep the
        # last observed occurrence of the maximum for activation auditing.
        pl.col('time_us').sort_by(['high', 'time_us'],
            descending=[True, True]).first().alias('held_last_max_high_us'),
        pl.len().alias('held_bars'))
    return (positions.select('ticker', 'episode_uid', 'entry_us',
                             'exit_us', 'entry_price')
        .join(swing, on='episode_uid', how='left', validate='1:1')
        .join(outcome, on='episode_uid', how='left', validate='1:1')
        .with_columns(((pl.col('exit_us')-pl.col('entry_us'))//1_000_000)
                      .alias('expected_held_bars'))
        .with_columns((
            (pl.col('pre_entry_bars') == 3) &
            (pl.col('held_bars') == pl.col('expected_held_bars')) &
            (pl.col('held_bars') >= MIN_HOLD_SECONDS)
        ).fill_null(False).alias('clock_complete'))
        .with_columns((pl.col('expected_held_bars')-
                       pl.col('held_bars').fill_null(0))
                      .alias('unobserved_held_seconds')))


def labels(positions: pl.DataFrame, bars: pl.DataFrame,
           tick_policy: pl.DataFrame, *, offset_ticks: int = 1
           ) -> pl.DataFrame:
    """Return tick-rounded hypothetical brackets from one-second candles."""
    return round_geometry(geometry(positions, bars), tick_policy,
                          offset_ticks=offset_ticks)


def round_geometry(result: pl.DataFrame, tick_policy: pl.DataFrame, *,
                   offset_ticks: int = 1) -> pl.DataFrame:
    """Round previously certified bar extrema without another market read."""
    required = {'ticker', 'episode_uid', 'entry_price', 'held_min_low',
                'held_max_high', 'swing_low_3s', 'expected_held_bars',
                'held_bars', 'held_last_max_high_us'}
    if (set(tick_policy.columns) != {'ticker', 'tick_size'} or
            not required <= set(result.columns) or
            tick_policy['ticker'].n_unique() != tick_policy.height or
            tick_policy['tick_size'].null_count() or
            tick_policy.filter(~pl.col('tick_size').is_finite() |
                               (pl.col('tick_size') <= 0)).height or
            type(offset_ticks) is not int or offset_ticks < 1):
        raise ValueError('Invalid price-action bracket source or tick policy')
    if tick_policy.height != result['ticker'].n_unique() or (
            result.select('ticker').unique().join(
                tick_policy.select('ticker'), on='ticker', how='anti').height):
        raise ValueError('Missing or extra per-listing tick authority')
    result = result.join(tick_policy, on='ticker', how='left', validate='m:1')
    # Remove the tick offset in integer tick space. Subtracting two binary
    # floats before floor can spuriously move an on-grid low down two ticks.
    stop = ((pl.min_horizontal('swing_low_3s', 'held_min_low')/
             pl.col('tick_size') + 1e-9).floor() - offset_ticks)*pl.col('tick_size')
    target = (pl.col('held_max_high')/pl.col('tick_size') + 1e-9).floor()*pl.col('tick_size')
    result = result.with_columns(
        pl.when(pl.col('held_bars').is_not_null()).then(stop)
          .alias('oracle_stop'),
        pl.when(pl.col('held_bars').is_not_null()).then(target)
          .alias('oracle_target'))
    return result.with_columns((
        (pl.col('expected_held_bars') >= MIN_HOLD_SECONDS) &
        (pl.col('held_bars') >= 1) &
        (pl.col('oracle_stop') > 0) &
        (pl.col('oracle_stop') < pl.col('entry_price')) &
        (pl.col('oracle_target') > pl.col('entry_price'))
    ).fill_null(False).alias('label_available'))
