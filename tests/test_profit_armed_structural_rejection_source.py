from dataclasses import replace
from datetime import date
import math

import polars as pl
import pytest

from src.backend import backtest_profit_armed_structural_rejection_source as source
from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval
from src.trading_runtime.profit_armed_structural_rejection import StructuralRejectionPolicy

DAY = date(2026, 1, 1)
RUN = '00000000-0000-0000-0000-000000000010'


def plan():
    units = tuple(MarketDayUnit('build-owned-string', DAY.isoformat(), 'TEST', stage,
        f'00000000-0000-0000-0000-00000000000{i}', 'a'*64, 4, 'b'*64)
        for i, stage in enumerate(('bars', 'technical', 'broker_100ms'), 1))
    return CertifiedMarketDayPlan(ExecutionInterval('time', 100), 'build-owned-string',
        'c'*64, (DAY.isoformat(),), ('TEST',), units, (100, 5000, 10000), 'd'*64)


def kwargs(**changes):
    return dict(plan=plan(), session_date=DAY, tickers=('TEST',),
        policy=StructuralRejectionPolicy(), run_id=RUN, interval_plan_token='e'*64,
        through_boundary_ms=30000, **changes)


def frame(clocks=(5000, 10000, 15000, 20000), resolution=5000):
    p = plan()
    rows = [dict(source_build_id=p.build_id, source_market_plan_token=p.token,
        source_bars_attempt_id=p.units[0].attempt_id,
        source_indicators_attempt_id=p.units[1].attempt_id,
        source_liquidity_attempt_id=p.units[2].attempt_id, session_date=DAY.isoformat(),
        ticker='TEST', resolution_ms=resolution, boundary_ms=c, close_int=100000,
        high_int=110000, trade_count=12, price_valid=True, extremes_valid=True,
        macd_line=.1, macd_signal=.2) for c in clocks]
    return pl.DataFrame(rows, schema=source.SCHEMA)


class Stream:
    def __init__(self, batches): self.batches, self.closed = batches, False
    def __iter__(self): return iter(self.batches)
    def close(self): self.closed = True


class Client:
    def __init__(self, f): self.stream = Stream(f.to_arrow().to_batches()); self.queries = []
    def iter_arrow_record_batches(self, sql): self.queries.append(sql); return self.stream


def test_real_query_dispatch_identity_and_no_future():
    client = Client(frame())
    lookup = source.load_completed_structural_rejection_lookup(client, **kwargs())
    sql = client.queries[0]
    source.assert_select_only(sql)
    assert 'LIMIT 2000001' in sql and 'SETTINGS join_use_nulls=1' in sql
    assert 'b.resolution_ms=5000' in sql and 'b.bucket_index<2886' in sql
    assert 'i.attempt_id' in sql and 'b.attempt_id' in sql
    assert 'b.extremes_valid' in sql and 'b.high_int' in sql and 'b.trade_count' in sql
    assert 'low_int' not in sql and 'file(' not in sql
    assert client.stream.closed and lookup.row_count == 4
    bar = lookup.bar_at('TEST', 20000)
    assert bar.source.market_build_id == plan().build_id
    assert bar.source.bars_attempt_id == plan().units[0].attempt_id
    assert bar.source.liquidity_attempt_id == plan().units[2].attempt_id
    assert bar.source.interval_plan_token == 'e'*64 and bar.source.run_id == RUN
    assert tuple(b.boundary_ms for b in lookup.activity_at('TEST', 20000)) == (5000,10000,15000,20000)
    assert lookup.bar_at('TEST', 25000) is None
    with pytest.raises(ValueError): lookup.bar_at('TEST', 20100)
    with pytest.raises(ValueError): lookup.bar_at('TEST', 35000)


@pytest.mark.parametrize('key', source.KEYS)
def test_foreign_identity(key):
    f = frame().with_columns(pl.lit('foreign').alias(key))
    with pytest.raises(ValueError, match='Foreign'):
        source.CompletedStructuralRejectionLookup(f, **kwargs())


@pytest.mark.parametrize('stage', ('bars', 'technical', 'broker_100ms'))
@pytest.mark.parametrize('defect', ('missing', 'duplicate', 'build', 'date', 'attempt'))
def test_producer_identity_defects(stage, defect):
    p = plan(); u = next(u for u in p.units if u.stage == stage)
    rest = tuple(v for v in p.units if v is not u)
    units = rest if defect == 'missing' else (*p.units, u) if defect == 'duplicate' else (
        *rest, replace(u, **{'build_id':'foreign'} if defect == 'build' else
            {'session_date':'2026-01-02'} if defect == 'date' else {'attempt_id':'not-uuid'}))
    k = kwargs(); k['plan'] = replace(p, units=units)
    with pytest.raises(ValueError): source.CompletedStructuralRejectionLookup(frame(), **k)


@pytest.mark.parametrize('clocks', ((5000,5000), (10000,5000), (5000,35000), (5001,), (0,)))
def test_order_duplicate_future_alignment(clocks):
    with pytest.raises(ValueError): source.CompletedStructuralRejectionLookup(frame(clocks), **kwargs())


@pytest.mark.parametrize('key,value', [('close_int',None), ('high_int',None),
    ('trade_count',None), ('price_valid',None), ('extremes_valid',None),
    ('close_int',0), ('high_int',1), ('resolution_ms',10000)])
def test_malformed_observations(key, value):
    f = frame().with_columns(pl.lit(value, dtype=source.SCHEMA[key]).alias(key))
    with pytest.raises(ValueError): source.CompletedStructuralRejectionLookup(f, **kwargs())


def test_sparse_missing_nonfinite_and_extremes_are_retained():
    f = frame((5000,15000,20000)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias('macd_line'),
        pl.lit(float('inf')).alias('macd_signal'), pl.lit(False).alias('extremes_valid'),
        pl.lit(0, dtype=pl.UInt64).alias('high_int'))
    lookup = source.CompletedStructuralRejectionLookup(f, **kwargs())
    bars = lookup.activity_at('TEST', 20000)
    assert bars[1] is None and bars[0].price_valid and not bars[0].extremes_valid
    assert bars[0].high_int == 0 and bars[0].trade_count == 12
    assert bars[0].macd_line is None and math.isinf(bars[0].macd_signal)
    assert lookup.unavailable_extremes_count == 3
    nan = source.CompletedStructuralRejectionLookup(frame().with_columns(
        pl.lit(float('nan')).alias('macd_line')), **kwargs()).bar_at('TEST', 20000)
    assert math.isnan(nan.macd_line)


def test_policy_variants_and_empty_no_query():
    k = kwargs(); k['policy'] = replace(k['policy'], bar_resolution_ms=10000,
        decision_resolution_ms=100, activity_count=2)
    client = Client(frame((10000,20000), resolution=10000))
    lookup = source.load_completed_structural_rejection_lookup(client, **k)
    assert 'b.resolution_ms=10000' in client.queries[0]
    assert len(lookup.activity_at('TEST',20000)) == 2
    k = kwargs(); k['tickers'] = ()
    client = Client(frame())
    lookup = source.load_completed_structural_rejection_lookup(client, **k)
    assert not client.queries and lookup.row_count == 0


def test_fast_decisions_floor_completed_slot_without_gap_fill():
    k = kwargs(); k['policy'] = replace(k['policy'], decision_resolution_ms=100)
    lookup = source.CompletedStructuralRejectionLookup(frame((5000,15000)), **k)
    assert lookup.completed_at('TEST',5100) == lookup.completed_at('TEST',5000)
    assert lookup.completed_at('TEST',4900) is None
    assert lookup.completed_at('TEST',10100) is None
    assert lookup.activity_at('TEST',15100)[-1].boundary_ms == 15000
    assert lookup.activity_at('TEST',15100)[-2] is None


@pytest.mark.parametrize('value', [0,1,2,None])
def test_actual_arrow_uint8_boolean_contract(value):
    f = frame().with_columns(pl.lit(value,dtype=pl.UInt8).alias('extremes_valid'),
                            pl.col('price_valid').cast(pl.UInt8))
    client = Client(f)
    if value in (0,1):
        lookup = source.load_completed_structural_rejection_lookup(client, **kwargs())
        assert lookup.bar_at('TEST',20000).extremes_valid is bool(value)
    else:
        with pytest.raises(ValueError,match='flag'):
            source.load_completed_structural_rejection_lookup(client, **kwargs())
    assert client.stream.closed


@pytest.mark.parametrize('key,value', [('run_id','bad'),('interval_plan_token','bad'),
    ('tickers',('TEST','TEST')),('tickers',('FOREIGN',)),('through_boundary_ms',True),
    ('through_boundary_ms',30101),('max_rows',True),('max_rows',0),('session_date','2026-01-01')])
def test_request_binding_invalid(key,value):
    k = kwargs(); k[key] = value
    with pytest.raises(ValueError): source.CompletedStructuralRejectionLookup(frame(), **k)


@pytest.mark.parametrize('key,value', [('run_id','bad'),('interval_plan_token','bad')])
def test_empty_request_still_requires_exact_run_interval_identity(key, value):
    k = kwargs(); k['tickers'] = (); k[key] = value
    client = Client(frame())
    with pytest.raises(ValueError): source.load_completed_structural_rejection_lookup(client, **k)
    assert client.queries == []


def test_required_resolution_and_wrong_shape_types():
    k = kwargs(); k['plan'] = replace(plan(), required_resolutions_ms=(100,))
    with pytest.raises(ValueError,match='resolution'): source.CompletedStructuralRejectionLookup(frame(), **k)
    for f in (frame().drop('high_int'), frame().select(reversed(list(source.SCHEMA))),
              frame().with_columns(pl.col('trade_count').cast(pl.Float64))):
        with pytest.raises(ValueError,match='shape'): source.CompletedStructuralRejectionLookup(f, **kwargs())


def test_arrow_overflow_shape_future_close_and_batch_split_duplicate():
    k = kwargs(); k['max_rows'] = 3
    client = Client(frame())
    with pytest.raises(ValueError,match='bound'):
        source.load_completed_structural_rejection_lookup(client, **k)
    assert 'LIMIT 4' in client.queries[0] and client.stream.closed
    for f in (frame().drop('high_int'), frame((35000,)), frame((5000,5000))):
        client = Client(f)
        client.stream.batches = f.to_arrow().to_batches(max_chunksize=1)
        with pytest.raises(ValueError): source.load_completed_structural_rejection_lookup(client, **kwargs())
        assert client.stream.closed


def test_input_frame_mutation_does_not_change_inventory():
    f = frame(); lookup = source.CompletedStructuralRejectionLookup(f, **kwargs())
    f.replace_column(f.columns.index('high_int'), pl.Series('high_int', [1]*4, dtype=pl.UInt64))
    assert lookup.bar_at('TEST',20000).high_int == 110000
