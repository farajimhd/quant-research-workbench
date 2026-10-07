"""Read only bracket-relevant completed ARTE liquidity buckets.

The certified broker attempt is the authority. The result contains one
entry-clock quote per selected episode; raw 100 ms rows are not persisted.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
import sqlite3
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import polars as pl

from . import arte_sql as sql
from .arte_source import frame


NY = ZoneInfo('America/New_York')
QUOTE_BUCKET_US = 100_000
MAX_QUOTE_AGE_US = 1_000_000
VERSION = 'rl-trading-bracket-liquidity-source-v1'


def broker_attempts(source: dict, ledger: str | Path, day: date,
                    tickers: set[str]) -> dict[str, str]:
    """Bind selected listings to completed broker units of the same build."""
    expected = set(source['units'][str(day)])
    if not tickers or not tickers <= expected:
        raise ValueError('Bracket listings differ from certified population')
    path = Path(ledger).resolve()
    if not path.is_file():
        raise ValueError('Missing certified market-day ledger')
    uri = path.as_uri()
    if str(path).startswith('\\\\'):
        uri = 'file:////' + path.as_posix().lstrip('/')
    with closing(sqlite3.connect(uri + '?mode=ro', uri=True, timeout=10)) as db:
        rows = db.execute(
            'SELECT ticker,attempt_id,status FROM units WHERE build_id=? '
            'AND session_date=? AND stage=?',
            (source['build_id'], str(day), 'broker_100ms')).fetchall()
    by_ticker = {ticker: (attempt, status) for ticker, attempt, status in rows}
    if len(by_ticker) != len(rows):
        raise ValueError('Duplicate certified broker attempts')
    selected = {}
    for ticker in sorted(tickers):
        record = by_ticker.get(ticker)
        if record is None or record[1] != 'complete':
            raise ValueError(f'{day} {ticker}: broker liquidity is not certified')
        selected[ticker] = str(UUID(record[0]))
    return selected


def assert_liquidity_storage(reader) -> None:
    table = sql.query(reader, "SELECT name,storage_policy FROM system.tables "
                      "WHERE database='arte' AND name='liquidity_100ms_v1'")
    parts = sql.query(reader, "SELECT disk_name,sum(rows) AS rows FROM system.parts "
                      "WHERE database='arte' AND table='liquidity_100ms_v1' "
                      "AND active GROUP BY disk_name")
    if (len(table) != 1 or table[0]['storage_policy'] != sql.POLICY or
            any(row['disk_name'] != sql.POLICY for row in parts)):
        raise ValueError('ARTE liquidity is not wholly on live_market_ssd')


def _bucket_ids(day: date, clocks_us: list[int]) -> list[int]:
    origin = round(datetime.combine(day, datetime.min.time(), NY)
                   .astimezone(timezone.utc).timestamp() * 1_000_000)
    result = set()
    for clock in clocks_us:
        if clock <= origin or (clock-origin) % 1_000_000:
            raise ValueError('Entry clock is not a completed whole second')
        last = (clock-origin) // QUOTE_BUCKET_US - 1
        result.update(range(last-9, last+1))
    return sorted(result)


def select_entry_quotes(positions: pl.DataFrame,
                        candidates: pl.DataFrame) -> pl.DataFrame:
    """Vectorized latest completed, fresh, valid quote per entry clock."""
    if candidates.select('ticker', 'boundary_us').n_unique() != candidates.height:
        raise ValueError('Duplicate pinned liquidity bucket')
    candidates = candidates.filter(
        (pl.col('event_count') > 0) & (pl.col('quote_valid') == 1) &
        (pl.col('last_event_us') >= pl.col('boundary_us')-QUOTE_BUCKET_US) &
        (pl.col('last_event_us') < pl.col('boundary_us')) &
        (pl.col('quote_timestamp_us') > 0) &
        (pl.col('quote_timestamp_us') <= pl.col('last_event_us')) &
        (pl.col('bid_int') > 0) & (pl.col('ask_int') >= pl.col('bid_int')) &
        (pl.col('bid_size') > 0) & (pl.col('ask_size') > 0))
    # Match on completed bucket time. The oracle performs a second freshness
    # check on quote_timestamp_us, since a cached quote can predate its bucket.
    return (positions.select('ticker', 'entry_us').sort('entry_us')
        .join_asof(candidates.sort('boundary_us'),
                   left_on='entry_us', right_on='boundary_us',
                   by='ticker', strategy='backward', tolerance=MAX_QUOTE_AGE_US,
                   check_sortedness=False)
        .select('ticker', 'entry_us', 'quote_timestamp_us', 'quote_valid',
                'bid_int', 'ask_int', 'bid_size', 'ask_size'))


def entry_quotes(reader, source: dict, ledger: str | Path, day: date,
                 positions: pl.DataFrame) -> pl.DataFrame:
    """Fetch at most ten pinned buckets per selected entry, then as-of join.

    Query projection is the minimum needed to verify a completed quote and
    compute the observed spread. A missing/stale quote remains an unavailable
    label rather than acquiring a synthetic zero spread.
    """
    required = {'ticker', 'entry_us', 'episode_uid'}
    if not required <= set(positions.columns):
        raise ValueError('Missing bracket position identity')
    if positions.select('episode_uid').n_unique() != positions.height:
        raise ValueError('Duplicate bracket episode')
    tickers = set(positions['ticker'].to_list())
    attempts = broker_attempts(source, ledger, day, tickers)
    assert_liquidity_storage(reader)
    rows = []
    end = f'toInt64({sql.bounds(day)})+(toInt64(bucket_index)+1)*100000'
    keys = []
    for ticker in sorted(tickers):
        clocks = positions.filter(pl.col('ticker') == ticker)['entry_us'].to_list()
        keys.extend((ticker, attempts[ticker], bucket)
                    for bucket in _bucket_ids(day, clocks))
    # One bounded tuple-key read per 500 exact buckets, across all listings.
    # This avoids thousands of per-ticker round trips and a dense quote grid.
    for start in range(0, len(keys), 500):
        selected = keys[start:start+500]
        ids = ','.join(f'({sql.literal(ticker)},toUUID({sql.literal(attempt)}),{bucket})'
                       for ticker, attempt, bucket in selected)
        statement = (
            f'SELECT ticker,{end} AS boundary_us,quote_timestamp_us,quote_valid,'
            'bid_int,ask_int,bid_size,ask_size,event_count,last_event_us '
            'FROM arte.liquidity_100ms_v1 WHERE '
            f'build_id={sql.literal(source["build_id"])} AND '
            f'session_date=toDate({sql.literal(day)}) AND '
            f'(ticker,attempt_id,bucket_index) IN ({ids}) '
            'ORDER BY ticker,bucket_index')
        part = frame(reader, statement, {
            'ticker': pl.String, 'boundary_us': pl.Int64,
            'quote_timestamp_us': pl.Int64, 'quote_valid': pl.Int64,
            'bid_int': pl.Int64, 'ask_int': pl.Int64,
            'bid_size': pl.Float64, 'ask_size': pl.Float64,
            'event_count': pl.Int64, 'last_event_us': pl.Int64})
        if part.height:
            rows.append(part)
    candidates = pl.concat(rows) if rows else pl.DataFrame(schema={
        'ticker': pl.String, 'boundary_us': pl.Int64,
        'quote_timestamp_us': pl.Int64, 'quote_valid': pl.Int64,
        'bid_int': pl.Int64, 'ask_int': pl.Int64,
        'bid_size': pl.Float64, 'ask_size': pl.Float64,
        'event_count': pl.Int64, 'last_event_us': pl.Int64})
    return select_entry_quotes(positions, candidates)
