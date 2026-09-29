"""Sparse reads of certified execution prices for touched bracket buckets."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.bracket_source import broker_attempts
from src.backend.backtest_liquidity_price import PriceLevelPlan


SCHEMA = {'ticker': pl.String, 'boundary_us': pl.Int64,
          'price_int': pl.Int64, 'execution_volume': pl.Float64}
BUCKET_SCHEMA = {'ticker': pl.String, 'boundary_us': pl.Int64,
                 'high': pl.Float64, 'low': pl.Float64,
                 'extremes_valid': pl.Int64}
NY = ZoneInfo('America/New_York')


def _read_held_extrema(reader, source: dict, day: date,
                       positions: pl.DataFrame, *, resolution_us: int,
                       lookback_us: int = 0) -> pl.DataFrame:
    """Project pinned bars only inside requested held or pre-entry intervals.

    Future buckets may be fetched for historical simulation, but are never
    supplied to the policy before their completed boundary. The caller must
    retain invalid/gap buckets as missing evidence, not as a non-trigger.
    Source population and stage attempts are pinned by ``load_build``.
    """
    required = {'ticker', 'entry_us', 'exit_us'}
    if not required <= set(positions.columns):
        raise ValueError('Missing confirmed held interval')
    if positions.is_empty():
        return pl.DataFrame(schema=BUCKET_SCHEMA)
    if (positions.select('ticker', 'entry_us').n_unique() != positions.height or
            positions.filter(pl.col('exit_us') <= pl.col('entry_us')).height):
        raise ValueError('Duplicate or invalid held interval')
    midnight = int(datetime.combine(day, time.min, NY).timestamp()*1_000_000)
    intervals = []
    units = source['units'][str(day)]
    for ticker, entry_us, exit_us in positions.select(
            'ticker', 'entry_us', 'exit_us').iter_rows():
        if (ticker not in units or entry_us < midnight+14_400_000_000 or
                exit_us > midnight+72_000_000_000):
            raise ValueError('Held interval is outside the certified day')
        first = max(14_400_000_000//resolution_us,
                    (int(entry_us)-lookback_us-midnight)//resolution_us)
        last = (int(exit_us)-midnight)//resolution_us-1
        if first <= last:
            intervals.append((ticker, units[ticker]['bars']['attempt_id'],
                              first, last))
    if not intervals:
        return pl.DataFrame(schema=BUCKET_SCHEMA)
    parts = []
    boundary = (f'toInt64({arte_sql.bounds(day)})+'
                f'(toInt64(bucket_index)+1)*{resolution_us}')
    for start in range(0, len(intervals), 100):
        scoped = intervals[start:start+100]
        predicate = ' OR '.join(
            f'(ticker={arte_sql.literal(ticker)} AND '
            f'attempt_id=toUUID({arte_sql.literal(attempt)}) AND '
            f'bucket_index BETWEEN {first} AND {last})'
            for ticker, attempt, first, last in scoped)
        statement = (
            f'SELECT ticker,{boundary} AS boundary_us,high_int/10000. AS high,'
            'low_int/10000. AS low,extremes_valid FROM arte.bars_v1 '
            f'WHERE build_id={arte_sql.literal(source["build_id"])} AND '
            f'session_date=toDate({arte_sql.literal(day)}) AND '
            f'resolution_ms={resolution_us//1000} AND ({predicate}) '
            'ORDER BY ticker,bucket_index')
        part = frame(reader, statement, BUCKET_SCHEMA)
        if part.height:
            parts.append(part)
    rows = pl.concat(parts) if parts else pl.DataFrame(schema=BUCKET_SCHEMA)
    if rows.height:
        conflicting = rows.group_by('ticker', 'boundary_us').agg(
            pl.col('high').n_unique().alias('high_values'),
            pl.col('low').n_unique().alias('low_values'),
            pl.col('extremes_valid').n_unique().alias('valid_values'))
        if conflicting.filter(
                (pl.col('high_values') != 1) |
                (pl.col('low_values') != 1) |
                (pl.col('valid_values') != 1)).height:
            raise ValueError('Pinned extrema bucket has conflicting values')
        # Overlapping held intervals can request the same certified row twice.
        rows = rows.unique(['ticker', 'boundary_us']).sort(
            'ticker', 'boundary_us')
    if (rows.select('ticker', 'boundary_us').n_unique() != rows.height or
            rows.filter((pl.col('boundary_us') % resolution_us != 0) |
                (pl.col('low') < 0) | (pl.col('high') < pl.col('low'))).height):
        raise ValueError('Pinned held extrema are malformed')
    return rows


def read_held_bucket_extrema(reader, source: dict, day: date,
                             positions: pl.DataFrame) -> pl.DataFrame:
    """Read only persisted post-fill 100 ms bars for bracket triggers."""
    return _read_held_extrema(reader, source, day, positions,
                              resolution_us=100_000)


def read_oracle_one_second_extrema(reader, source: dict, day: date,
                                   positions: pl.DataFrame) -> pl.DataFrame:
    """Read 3 s pre-fill lows and held one-second highs/lows, label-side only."""
    return _read_held_extrema(reader, source, day, positions,
                              resolution_us=1_000_000,
                              lookback_us=3_000_000)


def read_touched_price_levels(reader, source: dict, ledger: str,
                              plan: PriceLevelPlan, day: date,
                              touched: pl.DataFrame) -> pl.DataFrame:
    """Read price-volume rows only for target-touched, unambiguous buckets.

    The price plan must already have passed Backtest's coverage/hash audit.
    Aggregate 100 ms liquidity volume cannot substitute for this sidecar.
    Returned rows bound possible fills; queue priority remains unobserved.
    """
    needed = {'ticker', 'boundary_us', 'target_touched', 'stop_touched'}
    if not needed <= set(touched.columns) or plan.source_build_id != source['build_id']:
        raise ValueError('Bracket buckets or certified price-level build mismatch')
    desired = touched.filter(pl.col('target_touched') &
                             ~pl.col('stop_touched')).select(
                                 'ticker', 'boundary_us').unique()
    if desired.is_empty():
        return pl.DataFrame(schema=SCHEMA)
    if desired.select('ticker', 'boundary_us').n_unique() != desired.height:
        raise ValueError('Duplicate touched bracket bucket')
    tickers = set(desired['ticker'].to_list())
    broker = broker_attempts(source, ledger, day, tickers)
    units = {(unit.session_date, unit.ticker): unit for unit in plan.units}
    for ticker in tickers:
        unit = units.get((str(day), ticker))
        if unit is None or unit.source_attempt_id != broker[ticker]:
            raise ValueError(f'Price-level certificate missing pinned broker: {day} {ticker}')
    from research.rl_trading.v1.common import bounds
    # ARTE 100 ms bucket indices are relative to local midnight. The returned
    # boundary is the close of a completed bucket, never its start.
    midnight_us = bounds(day)[0] - 14_400_000_000
    keys = []
    for ticker, boundary in desired.iter_rows():
        shifted = int(boundary) - midnight_us
        if shifted <= 0 or shifted % 100_000:
            raise ValueError('Touched bucket is not an exact 100 ms close')
        unit = units[(str(day), ticker)]
        keys.append((ticker, unit.source_attempt_id,
                     unit.derivation_attempt_id, shifted // 100_000 - 1))
    rows = []
    boundary_sql = (f'toInt64({arte_sql.bounds(day)})+'
                    '(toInt64(bucket_index)+1)*100000')
    for start in range(0, len(keys), 500):
        selected = keys[start:start+500]
        scope = ','.join(
            f'({arte_sql.literal(ticker)},toUUID({arte_sql.literal(source_id)}),'
            f'toUUID({arte_sql.literal(derived_id)}),{bucket})'
            for ticker, source_id, derived_id, bucket in selected)
        statement = (
            f'SELECT ticker,{boundary_sql} AS boundary_us,price_int,'
            'execution_volume FROM arte.liquidity_execution_price_100ms_v1 '
            f'WHERE source_build_id={arte_sql.literal(source["build_id"])} '
            f'AND session_date=toDate({arte_sql.literal(day)}) AND '
            '(ticker,source_attempt_id,derivation_attempt_id,bucket_index) '
            f'IN ({scope}) ORDER BY ticker,bucket_index,price_int')
        part = frame(reader, statement, SCHEMA)
        if part.height:
            rows.append(part)
    result = pl.concat(rows) if rows else pl.DataFrame(schema=SCHEMA)
    if (result.select('ticker', 'boundary_us', 'price_int').n_unique() !=
            result.height or result.filter(
                (pl.col('price_int') <= 0) |
                (pl.col('execution_volume') <= 0) |
                ~pl.col('execution_volume').is_finite()).height):
        raise ValueError('Certified execution-price rows are malformed')
    if result.join(desired, on=['ticker', 'boundary_us'], how='anti').height:
        raise ValueError('Execution price row escaped requested bucket scope')
    return result
