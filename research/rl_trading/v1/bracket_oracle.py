"""Sparse, versioned hindsight bracket labels for completed long episodes.

Only the entry-time quote is causal. Future highs and lows are label-side
outcomes and must never be included in the policy observation.
"""
from __future__ import annotations

import math

import polars as pl


VERSION = 'rl-trading-long-bracket-oracle-v1'


def labels(positions: pl.DataFrame, bars: pl.DataFrame,
           quotes: pl.DataFrame, *, tick_size: float,
           offset_ticks: int = 1, max_quote_age_us: int = 1_000_000,
           ) -> pl.DataFrame:
    """Join only held paths, three pre-entry seconds, and entry-time quotes.

    ``entry_us`` is the confirmed entry-fill boundary, not the order-decision
    clock. The bracket becomes active strictly after that boundary. A spread is never substituted
    with zero when the quote is absent, crossed, or stale.
    """
    if (not math.isfinite(tick_size) or tick_size <= 0 or
            type(offset_ticks) is not int or offset_ticks < 1 or
            type(max_quote_age_us) is not int or max_quote_age_us < 0):
        raise ValueError('Invalid bracket tick, offset, or quote freshness')
    required_positions = {'ticker', 'episode_uid', 'entry_us', 'exit_us',
                          'entry_price', 'entry_fill_confirmed'}
    required_bars = {'ticker', 'time_us', 'high', 'low', 'extremes_valid'}
    required_quotes = {'ticker', 'entry_us', 'quote_timestamp_us',
                       'quote_valid', 'bid_int', 'ask_int', 'bid_size', 'ask_size'}
    for frame, required in ((positions, required_positions),
                            (bars, required_bars), (quotes, required_quotes)):
        if not required <= set(frame.columns):
            raise ValueError(f'Missing bracket fields: {sorted(required-set(frame.columns))}')
    if (positions.select('episode_uid').n_unique() != positions.height or
            positions.select('ticker', 'entry_us').n_unique() != positions.height or
            bars.select('ticker', 'time_us').n_unique() != bars.height or
            quotes.select('ticker', 'entry_us').n_unique() != quotes.height or
            positions.filter((pl.col('exit_us') <= pl.col('entry_us')) |
                             (pl.col('entry_price') <= 0) |
                             (pl.col('entry_fill_confirmed') != True)).height or
            positions['entry_fill_confirmed'].null_count()):
        raise ValueError('Duplicate or invalid bracket input identity and clocks')
    if (bars['extremes_valid'].null_count() or
            bars.filter(~pl.col('extremes_valid').cast(pl.Int8)
                        .is_in([0, 1])).height):
        raise ValueError('Bar extrema validity must be zero or one')
    valid_bars = bars.filter(
        (pl.col('extremes_valid') == 1) & pl.col('high').is_finite() &
        pl.col('low').is_finite() & (pl.col('low') > 0) &
        (pl.col('high') >= pl.col('low')))
    # The inequality join applies the clock predicates during the join. A
    # ticker-only join would materialize every bar for every episode first.
    path = (positions.select('ticker', 'episode_uid', 'entry_us', 'exit_us')
        .join_where(valid_bars,
            pl.col('ticker') == pl.col('ticker_right'),
            pl.col('time_us') >= pl.col('entry_us') - 3_000_000,
            pl.col('time_us') <= pl.col('exit_us')))
    lows_before = (path.filter(pl.col('time_us').is_between(
            pl.col('entry_us') - 3_000_000, pl.col('entry_us')))
        .group_by('episode_uid').agg(pl.col('low').min().alias('pre_entry_low')))
    active = (path.filter(pl.col('time_us') > pl.col('entry_us'))
        .group_by('episode_uid').agg(
            pl.col('low').min().alias('active_min_low'),
            pl.col('high').max().alias('active_max_high'),
            pl.col('time_us').min().alias('first_active_bar_us'),
            pl.col('time_us').max().alias('last_active_bar_us')))
    quote = quotes.with_columns(
        ((pl.col('ask_int') - pl.col('bid_int')) / 10_000.)
            .alias('quoted_spread'),
        (pl.col('entry_us') - pl.col('quote_timestamp_us'))
            .alias('quote_age_us'))
    quote = quote.with_columns((
        pl.col('quote_valid') & (pl.col('bid_int') > 0) &
        (pl.col('ask_int') >= pl.col('bid_int')) &
        (pl.col('bid_size') > 0) & (pl.col('ask_size') > 0) &
        pl.col('quote_age_us').is_between(0, max_quote_age_us))
        .alias('spread_available'))
    out = (positions.select('ticker', 'episode_uid', 'entry_us', 'exit_us',
                            'entry_price')
        .join(lows_before, on='episode_uid', how='left', validate='1:1')
        .join(active, on='episode_uid', how='left', validate='1:1')
        .join(quote.select('ticker', 'entry_us', 'quote_timestamp_us',
                           'quote_age_us', 'quoted_spread', 'spread_available'),
              on=['ticker', 'entry_us'], how='left', validate='1:1')
        .with_columns(pl.col('spread_available').fill_null(False)))
    available = (pl.col('spread_available') &
                 pl.col('pre_entry_low').is_not_null() &
                 pl.col('active_min_low').is_not_null() &
                 pl.col('active_max_high').is_not_null())
    stop = ((pl.min_horizontal('pre_entry_low', 'active_min_low') -
             pl.col('quoted_spread') - offset_ticks * tick_size) /
            tick_size).floor() * tick_size
    target = (pl.col('active_max_high') / tick_size).floor() * tick_size
    out = out.with_columns(
        pl.when(available).then(stop).alias('oracle_stop'),
        pl.when(pl.col('active_max_high').is_not_null())
          .then(target).alias('oracle_target'))
    out = out.with_columns((
        pl.col('oracle_stop').is_not_null() &
        (pl.col('oracle_stop') > 0) &
        (pl.col('oracle_stop') < pl.col('entry_price')) &
        (pl.col('oracle_target') > pl.col('entry_price'))
    ).fill_null(False).alias('label_available'))
    return out
