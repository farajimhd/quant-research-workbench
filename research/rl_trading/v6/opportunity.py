"""Ticker-local, vectorized episode discovery and sparse action candidates.

This merges the old ticker-local Phase 1 and Phase 2 into one pass. Future
prices are label-only. No account sizing or portfolio comparison happens here.
"""
from __future__ import annotations

from datetime import date
import math

import polars as pl

from research.rl_trading.v1.common import bounds


VERSION = 'rl-trading-ticker-opportunities-v6'
MIN_HOLD_SECONDS = 3
ENTRY_LOOKBACK_SECONDS = 2
HALF_LIFE_SECONDS = 30.
FEE_PROXY_PER_SHARE = .005


def compile_ticker(day: date, ticker: str, listing_id: str,
                   bars: pl.DataFrame, indicators: pl.DataFrame,
                   *, min_score: float = .01,
                   ) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """Return both-side episode ledger and filtered per-second opportunities.

    Indicator sign changes define non-overlapping MACD intervals. The best
    *close* within two seconds before the interval is a hindsight entry hint;
    the best subsequent close at least three seconds later is the exit hint.
    These are not executable fills. Candidate rows are emitted only while the
    fee-aware discounted score meets the market-teacher threshold.
    """
    if not ticker or not listing_id or not math.isfinite(min_score) or min_score < 0:
        raise ValueError('Invalid ticker, listing identity, or score threshold')
    expected_bars = {'bucket_index', 'resolution_ms', 'close_int', 'price_valid'}
    expected_indicators = {'bucket_index', 'macd_line', 'macd_signal'}
    if not expected_bars <= set(bars.columns) or not expected_indicators <= set(indicators.columns):
        raise ValueError('Missing certified ticker-local opportunity inputs')
    if bars.is_empty() or not (bars['resolution_ms'] == 1000).all() or (
            bars['bucket_index'].n_unique() != bars.height or
            indicators['bucket_index'].n_unique() != indicators.height):
        raise ValueError('Empty, duplicate, or non-one-second ticker inputs')
    session_start, session_end = bounds(day)
    bars = bars.sort('bucket_index').with_columns(
        (pl.lit(session_start) +
         (pl.col('bucket_index') - 14_400 + 1) * 1_000_000).alias('time_us'),
        (pl.col('close_int') / 10_000.).alias('close'))
    valid = bars.filter((pl.col('price_valid') == 1) &
                        (pl.col('close') > 0) & pl.col('close').is_finite())
    signs = (indicators.sort('bucket_index')
        .filter(pl.col('macd_line').is_finite() &
                pl.col('macd_signal').is_finite())
        .with_columns((pl.lit(session_start) +
            (pl.col('bucket_index') - 14_400 + 1) * 1_000_000).alias('time_us'))
        .with_columns((pl.col('macd_line') - pl.col('macd_signal'))
            .sign().cast(pl.Int8).alias('direction')))
    changes = signs.filter((pl.col('direction') != pl.col('direction').shift(1))
                           .fill_null(True))
    intervals = (changes.select(pl.col('time_us').alias('start_us'),
        pl.col('time_us').shift(-1).fill_null(session_end).alias('end_us'),
        'direction')
        .filter((pl.col('direction') != 0) &
                (pl.col('end_us') - pl.col('start_us') >=
                 MIN_HOLD_SECONDS * 1_000_000))
        .with_row_index('macd_interval_id', offset=1))
    if intervals.is_empty():
        empty = pl.DataFrame()
        return empty, empty, {'version': VERSION, 'ticker': ticker,
                              'intervals': 0, 'episodes': 0, 'candidates': 0}
    entry_windows = intervals.with_columns(
        (pl.col('start_us') - ENTRY_LOOKBACK_SECONDS * 1_000_000)
            .alias('left_us'))
    entries = (valid.select('time_us', 'close').join_where(
        entry_windows, pl.col('time_us') >= pl.col('left_us'),
        pl.col('time_us') <= pl.col('start_us'))
        .with_columns(pl.when(pl.col('direction') == 1)
            .then(pl.col('close')).otherwise(-pl.col('close'))
            .alias('_rank'))
        .sort('macd_interval_id', '_rank', 'time_us')
        .unique('macd_interval_id', keep='first', maintain_order=True)
        .select('macd_interval_id', pl.col('time_us').alias('entry_hint_us'),
                pl.col('close').alias('entry_hint_close')))
    exit_windows = intervals.join(entries, on='macd_interval_id', how='inner')
    exit_rows = (valid.select('time_us', 'close').join_where(
        exit_windows,
        pl.col('time_us') >= pl.col('entry_hint_us') +
            MIN_HOLD_SECONDS * 1_000_000,
        pl.col('time_us') < pl.col('end_us'))
        .with_columns(pl.when(pl.col('direction') == 1)
            .then(-pl.col('close')).otherwise(pl.col('close'))
            .alias('_rank'))
        .sort('macd_interval_id', '_rank', 'time_us')
        .unique('macd_interval_id', keep='first', maintain_order=True)
        .filter((pl.col('close') - pl.col('entry_hint_close')) *
                pl.col('direction') > 0)
        .sort('macd_interval_id')
        .with_row_index('episode_id', offset=1))
    episodes = exit_rows.select(
        pl.lit(day.isoformat()).alias('session_date'),
        pl.lit(ticker).alias('ticker'), pl.lit(listing_id).alias('listing_id'),
        'episode_id', 'macd_interval_id', 'direction', 'start_us', 'end_us',
        'entry_hint_us', 'entry_hint_close',
        pl.col('time_us').alias('exit_hint_us'),
        pl.col('close').alias('exit_hint_close'),
        pl.col('end_us').alias('label_available_us'))
    if episodes.is_empty():
        return episodes, pl.DataFrame(), {'version': VERSION, 'ticker': ticker,
            'intervals': intervals.height, 'episodes': 0, 'candidates': 0}
    episode_range = episodes.with_columns(
        (pl.col('exit_hint_us') - MIN_HOLD_SECONDS * 1_000_000)
            .alias('last_open_us'))
    candidates = (valid.select('time_us', 'close').join_where(
        episode_range, pl.col('time_us') >= pl.col('entry_hint_us'),
        pl.col('time_us') <= pl.col('last_open_us'))
        .with_columns(((pl.col('exit_hint_us') - pl.col('time_us')) /
                       1_000_000).alias('hold_seconds'))
        .with_columns((pl.col('direction') *
            (pl.col('exit_hint_close') - pl.col('close')) *
            (math.pow(.5, 1 / HALF_LIFE_SECONDS) ** pl.col('hold_seconds')) -
            2 * FEE_PROXY_PER_SHARE).alias('net_value_per_share'))
        .with_columns((pl.col('net_value_per_share') /
            (pl.col('close') + FEE_PROXY_PER_SHARE)).alias('score'))
        .filter(pl.col('score').is_finite() &
                (pl.col('score') >= min_score))
        .with_columns((pl.col('session_date') + ':' +
            pl.col('listing_id') + ':' +
            pl.col('episode_id').cast(pl.String)).alias('episode_uid'))
        .select('time_us', 'ticker', 'listing_id', 'direction', 'episode_uid',
                'episode_id', 'exit_hint_us', 'exit_hint_close',
                pl.col('close').alias('decision_close'), 'hold_seconds',
                'net_value_per_share', 'score', 'label_available_us')
        .sort('time_us', 'score', descending=[False, True]))
    report = {'version': VERSION, 'ticker': ticker,
              'intervals': intervals.height, 'episodes': episodes.height,
              'candidates': candidates.height,
              'score_threshold': min_score,
              'min_hold_seconds': MIN_HOLD_SECONDS,
              'entry_lookback_seconds': ENTRY_LOOKBACK_SECONDS,
              'half_life_seconds': HALF_LIFE_SECONDS,
              'fee_proxy_per_share_each_side': FEE_PROXY_PER_SHARE,
              'candidate_scope': 'ticker_local_hindsight_not_executable'}
    return episodes, candidates, report
