"""Sparse reads of certified execution prices for touched bracket buckets."""
from __future__ import annotations

from datetime import date

import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.bracket_source import broker_attempts
from src.backend.backtest_liquidity_price import PriceLevelPlan


SCHEMA = {'ticker': pl.String, 'boundary_us': pl.Int64,
          'price_int': pl.Int64, 'execution_volume': pl.Float64}


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
