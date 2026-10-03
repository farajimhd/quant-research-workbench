"""Projection-only reads of pinned ARTE one-second products for V6."""
from __future__ import annotations

from datetime import date

import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.arte_source import frame
from pipelines.market_sip.events.trade_reporting_flags import REVISION


def require_reporting_coverage(source, day):
    """V6 cannot consume legacy bars whose reporting evidence was uncertified."""
    eligibility = source['definition']['trade_eligibility']
    rows = [r for r in eligibility.get('verified_coverage', []) if r['source_date'] == str(day)]
    if (eligibility.get('coverage_authority') != 'q_live.historical_trade_reporting_coverage_v1'
            or eligibility.get('reporting_revision') != REVISION or len(rows) != 1
            or rows[0]['status'] != 'complete' or rows[0]['revision'] != REVISION
            or rows[0]['counts']['bad'] != 0):
        raise ValueError(f'{day}: V6 requires bars built with verified trade reporting coverage; rebuild legacy inputs')


BAR_COLUMNS = (
    'resolution_ms', 'bucket_index', 'open_int', 'high_int', 'low_int',
    'close_int', 'notional', 'volume', 'trade_count', 'price_valid',
    'extremes_valid',
)
INDICATOR_COLUMNS = (
    'bucket_index', 'macd_line', 'macd_signal', 'rsi_14', 'atr_14',
    'ema_7', 'ema_26',
)
BAR_SCHEMA = dict.fromkeys(BAR_COLUMNS, pl.Int64) | {
    'notional': pl.Float64, 'volume': pl.Float64,
}
INDICATOR_SCHEMA = {'bucket_index': pl.Int64} | dict.fromkeys(
    INDICATOR_COLUMNS[1:], pl.Float64)


def _attempt(source: dict, day: date, ticker: str, stage: str) -> str:
    try:
        unit = source['units'][str(day)][ticker][stage]
        attempt = unit['attempt_id']
        if unit.get('status') not in (None, 'certified', 'complete'):
            raise ValueError(f'ARTE {stage} attempt is not certified')
        return str(attempt)
    except (KeyError, TypeError) as error:
        raise ValueError(f'Missing pinned ARTE {stage} attempt for {day} {ticker}') from error


def read_candles(client, source: dict, day: date, ticker: str
                 ) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Fetch exactly the columns used by features and episode extraction."""
    bars_where = arte_sql.selection(source['build_id'], day, ticker,
                                    _attempt(source, day, ticker, 'bars'))
    indicator_where = arte_sql.selection(source['build_id'], day, ticker,
                                         _attempt(source, day, ticker, 'technical'))
    bars = frame(client, f"SELECT {','.join(BAR_COLUMNS)} FROM arte.bars_v1 "
                 f"WHERE {bars_where} AND resolution_ms=1000 ORDER BY bucket_index",
                 BAR_SCHEMA)
    indicators = frame(client, f"SELECT {','.join(INDICATOR_COLUMNS)} "
                       f"FROM arte.indicators_v1 WHERE {indicator_where} "
                       "AND resolution_ms=1000 ORDER BY bucket_index",
                       INDICATOR_SCHEMA)
    if bars['bucket_index'].n_unique() != bars.height:
        raise ValueError(f'ARTE one-second bars duplicate: {day} {ticker}')
    if indicators['bucket_index'].n_unique() != indicators.height:
        raise ValueError(f'ARTE one-second indicators duplicate: {day} {ticker}')
    return bars, indicators


def read_previous_volume(client, previous_source: dict, previous_day: date,
                         ticker: str) -> pl.DataFrame:
    """Only prior same-clock volume is needed for the RVOL denominator."""
    where = arte_sql.selection(previous_source['build_id'], previous_day, ticker,
                               _attempt(previous_source, previous_day, ticker, 'bars'))
    return frame(client, 'SELECT bucket_index,volume FROM arte.bars_v1 '
                 f'WHERE {where} AND resolution_ms=1000 ORDER BY bucket_index',
                 {'bucket_index': pl.Int64, 'volume': pl.Float64})
