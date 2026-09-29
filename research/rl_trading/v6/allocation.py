"""Sparse market-wide episode scores and 15-second reservation inputs.

This prepares a portfolio decision, not an executable trade or label. Actual
cash, holdings, confirmed fills, and bracket exits remain sequential state.
"""
from __future__ import annotations

import numpy as np
import polars as pl


WINDOW_SECONDS = 15
MIN_SCORE = .01


def first_eligible(candidates: pl.DataFrame) -> pl.DataFrame:
    """Retain one first-qualified row per episode after a sparse predicate.

    The input has already discarded unavailable and sub-threshold ticker rows.
    This is deliberately episode-granular: a continuing signal must not enter
    the future reservation sum once per second.
    """
    columns = {'time_us', 'ticker', 'listing_id', 'episode_uid', 'score'}
    if not columns <= set(candidates.columns):
        raise ValueError('Missing sparse opportunity identity or score')
    if candidates.is_empty():
        return candidates
    eligible = candidates.filter(pl.col('score').is_finite() &
                                 (pl.col('score') >= MIN_SCORE))
    if eligible.is_empty():
        return eligible
    if (eligible.select('episode_uid', 'listing_id').unique().height !=
            eligible['episode_uid'].n_unique()):
        raise ValueError('Episode UID maps to multiple listings')
    return (eligible.sort('episode_uid', 'time_us')
            .unique('episode_uid', keep='first', maintain_order=True)
            .sort('time_us', 'score', 'episode_uid',
                  descending=[False, True, False]))


def window_scores(first: pl.DataFrame, *, seconds: int = WINDOW_SECONDS,
                  ) -> pl.DataFrame:
    """Add score first appearing in the next `seconds` clock seconds.

    Searchsorted handles gaps directly; no dense second-by-listing matrix or
    per-second Python loop is materialized. Current-second score is separate.
    """
    if not 0 <= seconds <= 300:
        raise ValueError('Invalid future reservation horizon')
    if first.is_empty():
        return pl.DataFrame(schema={'time_us': pl.Int64,
                                    'current_score': pl.Float64,
                                    'future_score': pl.Float64})
    grouped = (first.group_by('time_us')
               .agg(pl.col('score').sum().alias('current_score'))
               .sort('time_us'))
    times = grouped['time_us'].to_numpy().astype(np.int64)
    scores = grouped['current_score'].to_numpy().astype(np.float64)
    if (np.any(np.diff(times) <= 0) or not np.isfinite(scores).all() or
            np.any(scores < 0)):
        raise ValueError('Invalid sparse future-score timeline')
    prefix = np.concatenate(([0.], np.cumsum(scores)))
    right = np.searchsorted(times, times + seconds * 1_000_000,
                            side='right')
    future = prefix[right] - prefix[np.arange(len(times)) + 1]
    return grouped.with_columns(pl.Series('future_score',
                                          np.maximum(future, 0.)))


def intended_budgets(first: pl.DataFrame, scores: pl.DataFrame,
                     *, initial_cash: float = 10_000.) -> pl.DataFrame:
    """Normalize first-eligible scores against same-clock and near-future rows.

    `desired_budget` is an upper intent against the original bankroll. A
    stateful teacher must intersect it with available cash and fills; realized
    profit cannot increase this denominator or the desired budget.
    """
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError('Invalid original teacher bankroll')
    if first.is_empty():
        return first
    rows = first.join(scores, on='time_us', how='left', validate='m:1')
    if rows['future_score'].null_count():
        raise ValueError('Missing future score for an eligible second')
    return rows.with_columns(
        (pl.lit(initial_cash) * pl.col('score') /
         (pl.col('current_score') + pl.col('future_score')))
            .alias('desired_budget'),
        (pl.lit(initial_cash) * pl.col('future_score') /
         (pl.col('current_score') + pl.col('future_score')))
            .alias('future_reservation'))
