from datetime import date
from hashlib import sha256
from types import SimpleNamespace
import polars as pl
from research.rl_trading.v6.environment_source import ArteExecutionSource


def test_target_source_pins_full_market_day_unit(monkeypatch):
    def certify(market,reader):
        unit=market.units[0]
        assert (unit.build_id,unit.session_date,unit.ticker,unit.stage,unit.attempt_id)==(
            'build','2026-07-31','A','broker_100ms','attempt')
        return SimpleNamespace(token='certified')
    monkeypatch.setattr('src.backend.backtest_liquidity_price.certify_price_level_plan',certify)
    monkeypatch.setattr('research.rl_trading.v6.bracket_evidence.read_touched_price_levels',
        lambda *args:pl.DataFrame({'ticker':['A','A'],'price_int':[100000,110000],'execution_volume':[2.,3.]}))
    source=ArteExecutionSource.__new__(ArteExecutionSource)
    source.reader=None
    source.source={'build_id':'build','definition_hash':'definition'}
    source.day=date(2026,7,31)
    source.ledger='unused'
    source.attempts={'A':'attempt'}
    source.price_plans={}
    source.read_hash=sha256()
    source.query_count=source.rows_read=0
    assert source.target_capacity('A',1_000_000,10.5)==3.
    assert source.rows_read==2


def test_columnar_buckets_preserve_missing_invalid_and_order():
    from research.rl_trading.v6.environment_source import _execution_buckets, EXECUTION_SCHEMA
    import pytest
    def row(t,b,**kw):
        value={k:0 for k in EXECUTION_SCHEMA}
        value.update(ticker=t,bucket_index=b,**kw)
        return value
    rows=[row('A',0,quote_count=1,extrema_count=1,quote_timestamp_us=50,quote_valid=1,event_count=1,
              last_event_us=90,bid_int=100000,ask_int=100100,bid_size=2.,ask_size=3.,
              high_int=110000,low_int=90000,extremes_valid=1),
          row('B',0,extrema_count=1,high_int=120000,low_int=100000,extremes_valid=1),
          row('A',1,quote_count=1,quote_timestamp_us=200001,quote_valid=1,event_count=1,last_event_us=200002)]
    result=_execution_buckets(pl.DataFrame(rows,schema=EXECUTION_SCHEMA),0)
    assert [(v.close_us,v.ticker) for v in result]==[(100000,'A'),(100000,'B'),(200000,'A')]
    assert result[0].quote.valid and result[0].quote.bid==10.
    assert result[0].high==11. and result[1].quote is None
    assert not result[2].quote.valid and result[2].high is None
    with pytest.raises(ValueError,match='Duplicate'):
        _execution_buckets(pl.DataFrame(rows+[rows[0]],schema=EXECUTION_SCHEMA),0)
    rows[0]['low_int']=120000
    with pytest.raises(ValueError,match='Malformed'):
        _execution_buckets(pl.DataFrame(rows,schema=EXECUTION_SCHEMA),0)


def test_target_batch_certifies_and_reads_once(monkeypatch):
    from src.backend.backtest_liquidity_price import PriceLevelPlan
    calls=[]
    def certify(market,reader):
        calls.append(('certify',market.tickers))
        return PriceLevelPlan('build',tuple(SimpleNamespace(ticker=t) for t in market.tickers),'proof')
    def read(*args):
        calls.append(('read',tuple(args[-1]['ticker'])))
        return pl.DataFrame({'ticker':['A','A','B'],'price_int':[100000,110000,120000],
                             'execution_volume':[2.,3.,4.]})
    monkeypatch.setattr('src.backend.backtest_liquidity_price.certify_price_level_plan',certify)
    monkeypatch.setattr('research.rl_trading.v6.bracket_evidence.read_touched_price_levels',read)
    source=ArteExecutionSource.__new__(ArteExecutionSource)
    source.reader=None; source.source={'build_id':'build','definition_hash':'definition'}
    source.day=date(2026,7,31); source.ledger='unused'; source.attempts={'A':'a','B':'b'}
    source.price_plans={}; source.read_hash=sha256(); source.query_count=source.rows_read=0
    assert source.target_capacities(100000,{'A':10.5,'B':12.})=={'A':3.,'B':4.}
    assert calls==[('certify',('A','B')),('read',('A','B'))]


def test_joined_read_obeys_select_only_contract():
    from research.rl_trading.v1.arte_sql import _approved
    from research.rl_trading.v6.environment_source import EXECUTION_SCHEMA
    source=ArteExecutionSource.__new__(ArteExecutionSource)
    source.origin=0; source.end_us=1_000_000; source.day=date(2026,7,31)
    source.attempts={'A':'00000000-0000-0000-0000-000000000001'}
    source.source={'build_id':'build','units':{'2026-07-31':{'A':{'bars':{'attempt_id':source.attempts['A']}}}}}
    statements=[]
    def read(statement,schema):
        statements.append(_approved(statement))
        return pl.DataFrame(schema=EXECUTION_SCHEMA)
    source._frame=read
    assert source._read_buckets(0,100000,['A'])==()
    assert len(statements)==1 and 'FULL ALL JOIN' in statements[0]
