"""Small pinned-count preflight for exact-size packed one-second banks."""
from __future__ import annotations

from datetime import date

from research.rl_trading.v1 import arte_sql


def one_second_counts(reader, source: dict, day: date,
                      tickers: list[str]) -> dict[str, int]:
    """Count exact pinned one-second bars without fetching their wide rows."""
    if not tickers or len(set(tickers)) != len(tickers):
        raise ValueError('Empty or duplicate ticker census')
    units = source['units'][str(day)]
    if set(tickers) - set(units):
        raise ValueError('Ticker census escapes the certified market population')
    counts = dict.fromkeys(tickers, 0)
    for start in range(0, len(tickers), 256):
        batch = tickers[start:start+256]
        scope = ','.join(
            f'({arte_sql.literal(ticker)},toUUID('
            f'{arte_sql.literal(units[ticker]["bars"]["attempt_id"])}))'
            for ticker in batch)
        statement = (
            'SELECT ticker,count() AS n FROM arte.bars_v1 '
            f'WHERE build_id={arte_sql.literal(source["build_id"])} '
            f'AND session_date=toDate({arte_sql.literal(day)}) '
            'AND resolution_ms=1000 '
            f'AND (ticker,attempt_id) IN ({scope}) GROUP BY ticker')
        for row in arte_sql.query(reader, statement):
            ticker = str(row['ticker'])
            count = int(row['n'])
            if ticker not in batch or count < 0 or count > 57_600:
                raise ValueError('Pinned one-second count is outside session scope')
            counts[ticker] = count
    return counts
