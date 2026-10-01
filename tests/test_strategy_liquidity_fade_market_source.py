"""Native producer binding and bounded cold reads, independent of admission."""
from dataclasses import replace
from datetime import date
import json

import pytest

from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.trading_runtime.strategy_liquidity_fade_failure import liquidity_fade_failure
from src.trading_runtime.strategy_liquidity_fade_market_source import (
    load_liquidity_fade_market_observations, validate_liquidity_fade_market_observations,
)
from test_strategy_liquidity_fade_failure import observed_case

BAR = '325dcc8d-1171-52c5-b944-a92ac772d865'
TECH = '2fd70bbd-7a4d-575d-9b09-36be1cc921b4'
QUOTE = '00000000-0000-0000-0000-000000000001'


def native_case():
    w = liquidity_fade_failure(observed_case())
    day = date(2026, 8, 10)
    source = dict(source_build_id='a'*64, source_market_plan_token='b'*64,
                  source_bars_attempt_id=BAR, source_indicators_attempt_id=TECH,
                  source_liquidity_attempt_id=QUOTE)
    units = tuple(MarketDayUnit('a'*64, day.isoformat(), 'PLUG', stage, attempt, 'c'*64, 4, 'd'*64)
                  for stage, attempt in (('bars', BAR), ('technical', TECH), ('broker_100ms', QUOTE)))
    plan = CertifiedMarketDayPlan(ExecutionInterval.parse('100ms'), 'a'*64, 'e'*64,
                                 (day.isoformat(),), ('PLUG',), units, (100, 5000), 'b'*64)
    def identity(at, resolution, attempt):
        return dict(build_id='a'*64, session_date=day.isoformat(), ticker='PLUG',
                    attempt_id=attempt, resolution_ms=resolution,
                    bucket_index=(at+14_400_000)//resolution-1)
    bars = tuple(dict(identity(c.boundary_ms, 5000, BAR), trade_count=c.trade_count,
                      price_valid=1, close_int=w.completed_close_int) for c in w.candles)
    indicator = dict(identity(w.completed_five_second_boundary_ms, 5000, TECH),
                     macd_line=w.macd_line, macd_signal=w.macd_signal)
    quote = dict(identity(w.boundary_ms, 100, QUOTE), bid_int=23100, ask_int=23200,
                 quote_valid=1, quote_timestamp_us=1_786_393_607_400_000-w.quote_age_us)
    return w, source, bars, indicator, quote, dict(plan=plan, session_date=day, ticker='PLUG')


def test_native_counts_distinct_attempts_clocks_and_exchange_quote_age_bind():
    w, source, bars, indicator, quote, args = native_case()
    assert validate_liquidity_fade_market_observations(w, source, bars, indicator, quote, **args) == w


@pytest.mark.parametrize('field,value', [('build_id', 'f'*64), ('session_date', '2026-08-11'),
    ('ticker', 'WAFU'), ('attempt_id', TECH), ('resolution_ms', 100), ('bucket_index', 0),
    ('trade_count', None), ('trade_count', True), ('trade_count', 58)])
def test_foreign_missing_or_altered_native_candle_cannot_attest_same_witness(field, value):
    w, source, bars, indicator, quote, args = native_case()
    with pytest.raises(ValueError):
        validate_liquidity_fade_market_observations(w, source, (dict(bars[0], **{field: value}), *bars[1:]), indicator, quote, **args)


@pytest.mark.parametrize('family,changes', [
    ('bar', {'price_valid': 0}), ('bar', {'close_int': 23200}),
    ('indicator', {'attempt_id': BAR}), ('indicator', {'macd_line': .01}),
    ('indicator', {'macd_signal': float('nan')}), ('indicator', {'bucket_index': 0}),
    ('quote', {'attempt_id': BAR}), ('quote', {'quote_timestamp_us': 1_786_393_607_400_001}),
    ('quote', {'bid_int': 23200}), ('quote', {'quote_valid': True}), ('quote', {'bucket_index': 0}),
])
def test_price_momentum_and_quote_need_actual_native_values(family, changes):
    w, source, bars, indicator, quote, args = native_case()
    if family == 'bar': bars = (*bars[:-1], dict(bars[-1], **changes))
    elif family == 'indicator': indicator = dict(indicator, **changes)
    else: quote = dict(quote, **changes)
    with pytest.raises(ValueError):
        validate_liquidity_fade_market_observations(w, source, bars, indicator, quote, **args)


def test_certified_plan_token_and_unique_attempts_are_required():
    w, source, bars, indicator, quote, args = native_case()
    for plan in (replace(args['plan'], token='f'*64),
                 replace(args['plan'], units=args['plan'].units[:2]),
                 replace(args['plan'], units=args['plan'].units + args['plan'].units[:1])):
        with pytest.raises(ValueError):
            validate_liquidity_fade_market_observations(w, source, bars, indicator, quote, **dict(args, plan=plan))


class Reader:
    def __init__(self, rows): self.rows, self.queries = iter(rows), []
    def execute(self, query):
        self.queries.append(query)
        return '\n'.join(json.dumps(row) for row in next(self.rows))


def test_cold_reader_is_three_bounded_pinned_selects_and_rejects_ambiguity():
    w, source, bars, indicator, quote, args = native_case()
    reader = Reader((bars, (indicator,), (quote,)))
    assert load_liquidity_fade_market_observations(reader, w, source, **args) == w
    assert len(reader.queries) == 3
    for query, attempt, limit in zip(reader.queries, (BAR, TECH, QUOTE), (5, 2, 2)):
        assert query.startswith('SELECT ') and attempt in query and f'LIMIT {limit}' in query
        assert 'toString(attempt_id) AS attempt_id' not in query
        assert 'raw' not in query and 'file(' not in query
    for groups in ((bars+bars[:1], (indicator,), (quote,)),
                   (bars, (indicator, indicator), (quote,)),
                   (bars, (indicator,), ())):
        with pytest.raises(ValueError):
            load_liquidity_fade_market_observations(Reader(groups), w, source, **args)
