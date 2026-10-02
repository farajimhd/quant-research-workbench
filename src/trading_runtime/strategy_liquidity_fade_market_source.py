"""Bounded native observation verification for the prepared liquidity exit.

The caller supplies an independently certified market plan. This module reads
only its pinned ARTE producer attempts, never raw SIP or a historical fallback.
It neither certifies that plan nor installs a runtime/writer/release route.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from collections.abc import Mapping

from .arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
from .strategy_liquidity_fade_exit import validate_liquidity_fade_witness


def _source_units(plan, source, session_date, ticker):
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit
    validate_liquidity_observation_source(source)
    if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
            or type(ticker) is not str or not ticker
            or source['source_build_id'] != plan.build_id
            or source['source_market_plan_token'] != plan.token
            or session_date.isoformat() not in plan.sessions or ticker not in plan.tickers
            or not {100, 5000}.issubset(plan.required_resolutions_ms)
            or type(plan.units) is not tuple or len(plan.units) > 65_536
            or any(type(u) is not MarketDayUnit for u in plan.units)):
        raise ValueError('Liquidity fade lacks its exact certified market-plan identity')
    result = {}
    for stage, field in (('bars', 'source_bars_attempt_id'),
                         ('technical', 'source_indicators_attempt_id'),
                         ('broker_100ms', 'source_liquidity_attempt_id')):
        matches = [u for u in plan.units if u.session_date == session_date.isoformat()
                   and u.ticker == ticker and u.stage == stage]
        if (len(matches) != 1 or type(matches[0]) is not MarketDayUnit
                or matches[0].build_id != plan.build_id
                or matches[0].attempt_id != source[field]):
            raise ValueError('Liquidity fade producer attempt differs from its certified plan')
        result[stage] = matches[0]
    return result


def validate_liquidity_fade_market_observations(
    witness, source, bars, indicator, quote, *, plan, session_date, ticker,
):
    """Bind four exact native bars, one indicator and the current 100ms quote.

    Native bucket indices determine completion clocks. The witness cannot
    supply a fabricated producer boundary or cross attempts/dates/tickers.
    Zero counts remain real observations; missing/duplicate rows reject.
    This checks producer values, not native financial state or commit ancestry.
    """
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS, market_day_boundary
    validate_liquidity_fade_witness(witness)
    units = _source_units(plan, source, session_date, ticker)
    if type(bars) is not tuple or len(bars) != 4:
        raise ValueError('Liquidity fade requires exactly four native completed bars')

    def identity(row, stage, resolution, boundary):
        if (not isinstance(row, Mapping)
                or row.get('build_id') != plan.build_id
                or row.get('session_date') != session_date.isoformat()
                or row.get('ticker') != ticker
                or row.get('attempt_id') != units[stage].attempt_id
                or type(row.get('resolution_ms')) is not int or row['resolution_ms'] != resolution
                or type(row.get('bucket_index')) is not int
                or row['bucket_index'] < 0
                or (row['bucket_index'] + 1)*resolution - SESSION_OPEN_OFFSET_MS != boundary):
            raise ValueError('Liquidity fade native observation identity or completion clock differs')

    for bar, candle in zip(bars, witness.candles):
        identity(bar, 'bars', 5000, candle.boundary_ms)
        if type(bar.get('trade_count')) is not int or bar['trade_count'] != candle.trade_count:
            raise ValueError('Liquidity fade native trade count differs')
    latest = bars[-1]
    if (type(latest.get('price_valid')) is not int or latest['price_valid'] != 1
            or type(latest.get('close_int')) is not int
            or latest['close_int'] != witness.completed_close_int):
        raise ValueError('Liquidity fade native completed close differs')
    identity(indicator, 'technical', 5000, witness.completed_five_second_boundary_ms)
    if (any(type(indicator.get(k)) not in (int, float) for k in ('macd_line', 'macd_signal'))
            or indicator['macd_line'] != witness.macd_line
            or indicator['macd_signal'] != witness.macd_signal):
        raise ValueError('Liquidity fade native completed MACD differs')
    identity(quote, 'broker_100ms', 100, witness.boundary_ms)
    if (type(quote.get('quote_valid')) is not int or quote['quote_valid'] != 1
            or any(type(quote.get(k)) is not int for k in ('bid_int', 'ask_int', 'quote_timestamp_us'))
            or Decimal(quote['bid_int']) != Decimal(str(witness.bid))*10_000
            or Decimal(quote['ask_int']) != Decimal(str(witness.ask))*10_000):
        raise ValueError('Liquidity fade native quote differs')
    instant = market_day_boundary(session_date, witness.boundary_ms).astimezone(timezone.utc)
    elapsed = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    now_us = (elapsed.days*86_400 + elapsed.seconds)*1_000_000 + elapsed.microseconds
    if now_us - quote['quote_timestamp_us'] != witness.quote_age_us:
        raise ValueError('Liquidity fade native exchange quote age differs')
    return witness


def load_liquidity_fade_market_observations(client, witness, source, *, plan, session_date, ticker):
    """Cold-check one proposed exit with three bounded, read-only ARTE queries.

    This belongs at publication/cold recovery, not in a 100ms management loop.
    Each LIMIT includes an extra ambiguity sentinel; excess rows reject instead
    of silently truncating. The runtime must use cached/vectorized observations.
    """
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS, _literal, assert_select_only
    import json
    validate_liquidity_fade_witness(witness)
    units = _source_units(plan, source, session_date, ticker)
    def read(stage, table, fields, resolution, first, last, limit):
        low = (first + SESSION_OPEN_OFFSET_MS)//resolution - 1
        high = (last + SESSION_OPEN_OFFSET_MS)//resolution - 1
        resolution_clause = '' if stage == 'broker_100ms' else f' AND resolution_ms={resolution}'
        # Broker liquidity has a fixed 100ms contract and no resolution column.
        # Bars/indicators must select their actual column: a same-name constant
        # alias is substituted into WHERE by ClickHouse and erases the filter.
        resolution_projection = (f'toUInt32({resolution}) AS resolution_ms'
                                 if stage == 'broker_100ms' else 'resolution_ms')
        # Keep the UUID column's name/type intact for the same alias reason.
        query = (f'SELECT build_id,session_date,ticker,attempt_id,'
                 f'{resolution_projection},bucket_index,{fields} FROM arte.{table} '
                 f'WHERE build_id={_literal(plan.build_id)} '
                 f'AND session_date=toDate({_literal(session_date.isoformat())}) '
                 f'AND ticker={_literal(ticker)} AND attempt_id=toUUID({_literal(units[stage].attempt_id)})'
                 f'{resolution_clause} AND bucket_index>={low} AND bucket_index<={high} '
                 f'ORDER BY bucket_index LIMIT {limit} '
                 'SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')
        return tuple(json.loads(line) for line in client.execute(assert_select_only(query)).splitlines() if line.strip())
    bars = read('bars', 'bars_v1', 'trade_count,price_valid,close_int', 5000,
                witness.candles[0].boundary_ms, witness.completed_five_second_boundary_ms, 5)
    indicators = read('technical', 'indicators_v1', 'macd_line,macd_signal', 5000,
                      witness.completed_five_second_boundary_ms, witness.completed_five_second_boundary_ms, 2)
    quotes = read('broker_100ms', 'liquidity_100ms_v1', 'bid_int,ask_int,quote_valid,quote_timestamp_us', 100,
                  witness.boundary_ms, witness.boundary_ms, 2)
    if len(indicators) != 1 or len(quotes) != 1:
        raise ValueError('Liquidity fade native MACD or quote is missing or ambiguous')
    return validate_liquidity_fade_market_observations(
        witness, source, bars, indicators[0], quotes[0], plan=plan, session_date=session_date, ticker=ticker)
