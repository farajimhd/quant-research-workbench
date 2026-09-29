"""Sparse first-arrival quotes for first-eligible V6 entry intentions.

Each decision is a completed one-second close. Only the first following
100 ms bucket is read from the pinned ARTE broker attempt, and missing or
stale quotes remain explicit non-fills. No dense liquidity grid is stored.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import polars as pl

from research.rl_trading.v1 import arte_sql as sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.bracket_source import (assert_liquidity_storage,
                                                   broker_attempts)

NY = ZoneInfo('America/New_York')


def _midnight_us(day: date) -> int:
    """ARTE 100 ms bucket indices are day-relative, not session-relative."""
    return int(datetime.combine(day, time.min, NY).timestamp()*1_000_000)


def _keys(day: date, decisions: pl.DataFrame, attempts: dict[str, str],
          ) -> pl.DataFrame:
    if not {'ticker', 'time_us', 'episode_uid'} <= set(decisions.columns):
        raise ValueError('Missing first-entry decision identity')
    if decisions.select('episode_uid').n_unique() != decisions.height:
        raise ValueError('Expected one first-eligible decision per episode')
    origin = _midnight_us(day)
    keys = decisions.select('ticker', 'time_us', 'episode_uid').with_columns(
        ((pl.col('time_us') - origin) // 100_000)
            .cast(pl.Int64).alias('bucket_index'))
    if keys.filter((pl.col('time_us') <= origin) |
                    ((pl.col('time_us') - origin) % 1_000_000 != 0) |
                    ~pl.col('ticker').is_in(list(attempts))).height:
        raise ValueError('Invalid first-arrival quote bucket')
    return keys


def attach_quotes(keys: pl.DataFrame, quotes: pl.DataFrame,
                  day: date) -> pl.DataFrame:
    """Retain every candidate with an explicit quote availability flag."""
    needed = {'ticker', 'bucket_index', 'quote_timestamp_us', 'quote_valid',
              'bid_int', 'ask_int', 'bid_size', 'ask_size', 'event_count',
              'last_event_us'}
    if not needed <= set(quotes.columns):
        raise ValueError('Missing pinned quote columns')
    if (quotes.select('ticker', 'bucket_index').n_unique() != quotes.height or
            quotes.join(keys.select('ticker', 'bucket_index'),
                        on=['ticker', 'bucket_index'], how='anti').height):
        raise ValueError('Unexpected or duplicate pinned arrival quote')
    origin = _midnight_us(day)
    joined = keys.join(quotes, on=['ticker', 'bucket_index'],
                       how='left', validate='m:1')
    bucket_end = origin + (pl.col('bucket_index')+1)*100_000
    joined = joined.with_columns(bucket_end.alias('arrival_bucket_end_us'))
    available = (
        (pl.col('event_count') > 0) & (pl.col('quote_valid') == 1) &
        (pl.col('last_event_us') < pl.col('arrival_bucket_end_us')) &
        (pl.col('last_event_us') >= pl.col('time_us')) &
        (pl.col('quote_timestamp_us') > 0) &
        (pl.col('quote_timestamp_us') <= pl.col('last_event_us')) &
        (pl.col('arrival_bucket_end_us')-pl.col('quote_timestamp_us')
            <= 1_000_000) &
        (pl.col('bid_int') > 0) &
        (pl.col('ask_int') >= pl.col('bid_int')) &
        (pl.col('bid_size') > 0) & (pl.col('ask_size') > 0))
    return joined.with_columns(available.fill_null(False).alias('quote_available'))


def arrival_quotes(reader, source: dict, ledger, day: date,
                   decisions: pl.DataFrame) -> pl.DataFrame:
    """Read exactly one first-arrival bucket per selected episode, in batches."""
    if decisions.is_empty():
        return decisions
    attempts = broker_attempts(source, ledger, day,
                               set(decisions['ticker'].to_list()))
    assert_liquidity_storage(reader)
    keys = _keys(day, decisions, attempts)
    unique = keys.select('ticker', 'bucket_index').unique().sort(
        'ticker', 'bucket_index')
    tuples = [(row['ticker'], attempts[row['ticker']], row['bucket_index'])
              for row in unique.iter_rows(named=True)]
    parts = []
    for start in range(0, len(tuples), 500):
        ids = ','.join(f'({sql.literal(ticker)},toUUID({sql.literal(attempt)}),{bucket})'
                       for ticker, attempt, bucket in tuples[start:start+500])
        statement = (
            'SELECT ticker,bucket_index,quote_timestamp_us,quote_valid,'
            'bid_int,ask_int,bid_size,ask_size,event_count,last_event_us '
            'FROM arte.liquidity_100ms_v1 WHERE '
            f'build_id={sql.literal(source["build_id"])} AND '
            f'session_date=toDate({sql.literal(day)}) AND '
            f'(ticker,attempt_id,bucket_index) IN ({ids}) '
            'ORDER BY ticker,bucket_index')
        part = frame(reader, statement, {
            'ticker': pl.String, 'bucket_index': pl.Int64,
            'quote_timestamp_us': pl.Int64, 'quote_valid': pl.Int64,
            'bid_int': pl.Int64, 'ask_int': pl.Int64,
            'bid_size': pl.Float64, 'ask_size': pl.Float64,
            'event_count': pl.Int64, 'last_event_us': pl.Int64})
        if part.height:
            parts.append(part)
    quotes = (pl.concat(parts) if parts else pl.DataFrame(schema={
        'ticker': pl.String, 'bucket_index': pl.Int64,
        'quote_timestamp_us': pl.Int64, 'quote_valid': pl.Int64,
        'bid_int': pl.Int64, 'ask_int': pl.Int64,
        'bid_size': pl.Float64, 'ask_size': pl.Float64,
        'event_count': pl.Int64, 'last_event_us': pl.Int64}))
    return attach_quotes(keys, quotes, day)
