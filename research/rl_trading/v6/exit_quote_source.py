"""Page only the pinned ARTE quote buckets needed by a pending exit.

The caller stops iteration when the OMS closes the position. Missing buckets
do not become quotes; an incomplete exit stays open. Each query selects only
one ticker/attempt, a bounded bucket range, and the six quote fields needed
by the OMS plus event timing/validity evidence.
"""
from __future__ import annotations

from datetime import date
from collections.abc import Iterator

import polars as pl

from research.rl_trading.v1 import arte_sql as sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.bracket_source import (assert_liquidity_storage,
                                                   broker_attempts)
from research.rl_trading.v6.entry_source import _midnight_us
from research.rl_trading.v6.oms import Quote


QUOTE_SCHEMA = {
    'bucket_index': pl.Int64, 'quote_timestamp_us': pl.Int64,
    'quote_valid': pl.Int64, 'bid_int': pl.Int64, 'ask_int': pl.Int64,
    'bid_size': pl.Float64, 'ask_size': pl.Float64,
    'event_count': pl.Int64, 'last_event_us': pl.Int64,
}


def iter_exit_quotes(reader, source: dict, ledger, day: date, ticker: str, *,
                     decision_us: int, end_us: int,
                     page_rows: int = 256) -> Iterator[Quote]:
    """Yield observed later quote buckets in increasing clock order.

    `end_us` is an explicit certified session boundary, not an arbitrary
    retry timeout. Pagination bounds host memory; it does not truncate a
    still-open order. The source attempt is pinned by the day ledger.
    """
    origin = _midnight_us(day)
    if (not ticker or type(decision_us) is not int or
            type(end_us) is not int or
            not origin <= decision_us < end_us or
            type(page_rows) is not int or not 1 <= page_rows <= 1000):
        raise ValueError('Invalid pending-exit source boundary')
    attempts = broker_attempts(source, ledger, day, {ticker})
    assert_liquidity_storage(reader)
    attempt = attempts[ticker]
    cursor = (decision_us-origin)//100_000 - 1
    last_bucket = (end_us-origin)//100_000 - 1
    while cursor < last_bucket:
        query = (
            'SELECT bucket_index,quote_timestamp_us,quote_valid,bid_int,'
            'ask_int,bid_size,ask_size,event_count,last_event_us '
            'FROM arte.liquidity_100ms_v1 WHERE '
            f'build_id={sql.literal(source["build_id"])} AND '
            f'session_date=toDate({sql.literal(day)}) AND '
            f'ticker={sql.literal(ticker)} AND '
            f'attempt_id=toUUID({sql.literal(attempt)}) AND '
            f'bucket_index>{cursor} AND bucket_index<={last_bucket} '
            f'ORDER BY bucket_index LIMIT {page_rows}')
        rows = frame(reader, query, QUOTE_SCHEMA)
        if not rows.height:
            break
        for row in rows.iter_rows(named=True):
            bucket = int(row['bucket_index'])
            if not cursor < bucket <= last_bucket:
                raise ValueError('ARTE exit quote page violates bucket range')
            cursor = bucket
            bucket_end = origin+(bucket+1)*100_000
            event_us = int(row['last_event_us'] or 0)
            quoted_us = int(row['quote_timestamp_us'] or 0)
            valid = (int(row['event_count'] or 0) > 0 and
                     int(row['quote_valid'] or 0) == 1 and
                     decision_us <= event_us < bucket_end and
                     0 < quoted_us <= event_us)
            yield Quote(bucket_end, quoted_us,
                float(row['bid_int'] or 0)/10_000,
                float(row['ask_int'] or 0)/10_000,
                float(row['bid_size'] or 0),
                float(row['ask_size'] or 0), valid)
        if rows.height < page_rows:
            break
