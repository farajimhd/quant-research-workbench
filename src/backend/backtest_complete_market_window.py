"""Complete bounded native market windows, prepared before clocked release.

These helpers consume caller-certified immutable plans; they do not certify
plans, mint source authority or admit orders. No historical fallback, retry,
spool or financial mutation occurs. Original stream consumers are unchanged.
"""
from heapq import merge
from math import isfinite

from src.backend.backtest_complete_market_response import (
    CompleteMarketResponseBounds, read_complete_market_response,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, market_day_source_sqls, iter_market_boundary_groups,
)
from src.backend.backtest_liquidity_price import PriceLevelPlan


def _key(row):
    return row['session_date'], _boundary(row), row['ticker'], row['resolution_ms']


def _boundary(row):
    value = row.get('boundary_ms')
    if type(value) is int:
        return value
    # UInt64 JSONEachRow fields use canonical quoted decimal wire values.
    # Retain the original row spelling, as the existing stream does.
    if (type(value) is str and value.isascii() and value.isdecimal()
            and (value == '0' or not value.startswith('0'))):
        return int(value)
    raise ValueError('Complete market window has an invalid boundary wire value')


def read_complete_market_window(plan, *, prices, after_boundary_ms,
                               through_boundary_ms, client, response_bounds,
                               max_window_rows):
    """Validate every response and merged key before returning any window row.

    Response budgets apply per query, with at most four queries. The aggregate
    row budget applies to the whole window. Expected source coverage remains
    the pinned producer/preflight authority, not an inferred dense candle grid.
    """
    if (type(plan) is not CertifiedMarketDayPlan or type(prices) is not PriceLevelPlan
            or len(plan.sessions) != 1 or len(plan.tickers) != 1
            or plan.execution_interval.milliseconds != 100
            or type(after_boundary_ms) is not int or type(through_boundary_ms) is not int
            or not 0 <= after_boundary_ms < through_boundary_ms <= 57600000
            or after_boundary_ms % 100 or through_boundary_ms % 100
            or through_boundary_ms - after_boundary_ms > 300000
            or type(response_bounds) is not CompleteMarketResponseBounds
            or type(max_window_rows) is not int or not 0 < max_window_rows <= 100000):
        raise ValueError('Complete market window lacks exact bounded native scope')
    sqls = market_day_source_sqls(plan, after_boundary_ms=after_boundary_ms,
                                 through_boundary_ms=through_boundary_ms, price_plan=prices)
    if not 1 <= len(sqls) <= 4:
        raise ValueError('Complete market window exceeds bounded source response count')
    packets = []
    count = 0
    for sql in sqls:
        packet = read_complete_market_response(client, sql, bounds=response_bounds)
        count += len(packet)
        if count > max_window_rows:
            raise ValueError('Complete market window exceeds aggregate row budget; no truncation')
        previous = None
        for row in packet:
            boundary = _boundary(row)
            if (row.get('session_date') != plan.sessions[0]
                    or row.get('ticker') != plan.tickers[0]
                    or not after_boundary_ms < boundary <= through_boundary_ms
                    or boundary % 100
                    or type(row.get('resolution_ms')) is not int
                    or row['resolution_ms'] not in {100, *plan.required_resolutions_ms}
                    or boundary % row['resolution_ms']):
                raise ValueError('Complete market window row escaped its pinned completed scope')
            key = _key(row)
            if previous is not None and key <= previous:
                raise ValueError('Complete market response keys are duplicated or moved backward')
            previous = key
            levels = row.get('execution_price_levels')
            if levels is not None:
                normalized = []
                for price, volume in levels:
                    if (type(price) not in (int, str) or type(volume) not in (int, float)
                            or (type(price) is str and not price.isdecimal())
                            or int(price) <= 0 or not isfinite(volume) or volume < 0):
                        raise ValueError('Complete market window has invalid execution price levels')
                    normalized.append({'price_int': int(price), 'volume': float(volume)})
                row['execution_price_levels'] = tuple(normalized)
        packets.append(packet)
    # Every HTTP response is closed here. Reject cross-query duplicate keys
    # before exposing the window; grouping still follows the existing contract.
    rows = tuple(merge(*packets, key=_key))
    if any(_key(left) >= _key(right) for left, right in zip(rows, rows[1:])):
        raise ValueError('Complete market window merged keys are duplicated or moved backward')
    return rows


def iter_complete_market_windows(plan, *, prices, after_boundary_ms,
                                 through_boundary_ms, window_span_ms, client,
                                 response_bounds, max_window_rows):
    """Read ahead within one bounded window; release groups in causal order.

    A later failed window cannot release any of its rows. Previously completed
    windows stay delivered; the caller aborts that run without partial retry.
    """
    if (type(window_span_ms) is not int or not 100 <= window_span_ms <= 300000
            or window_span_ms % 100
            or type(after_boundary_ms) is not int or type(through_boundary_ms) is not int
            or not 0 <= after_boundary_ms <= through_boundary_ms <= 57600000
            or after_boundary_ms % 100 or through_boundary_ms % 100):
        raise ValueError('Complete market iterator needs exact ordered window clocks')
    cursor = after_boundary_ms
    while cursor < through_boundary_ms:
        end = min(cursor + window_span_ms, through_boundary_ms)
        rows = read_complete_market_window(plan, prices=prices,
            after_boundary_ms=cursor, through_boundary_ms=end, client=client,
            response_bounds=response_bounds, max_window_rows=max_window_rows)
        for day, boundary, ticker, resolutions in iter_market_boundary_groups(rows):
            yield boundary, resolutions
        cursor = end
