from datetime import datetime,timezone
from types import SimpleNamespace
import io
import re
import pytest
from rich.console import Console
from research.level_book.v7 import campaign as c
from research.level_book.v7.campaign_source import bars_sql,decode
from research.level_book.v7.campaign_store import read,write
from src.market_engine.historical_level_checkpoint import digest


def test_planner_bounds_source_metadata_aggregation(tmp_path,monkeypatch):
    universe=[dict(ticker=f'T{i}',symbol_id=str(i),massive_ticker=None) for i in range(300)]
    batches=[]
    def query(sql,threads=2):
        if 'max(universe_date)' in sql:return [dict(day='2026-09-12')]
        if 'feature_tradable_universe' in sql:return universe
        if 'events_ordinal_continuity' in sql:
            assert 'ticker IN (' in sql
            names=re.findall(r"'(T\d+)'",sql);batches.append(names)
            return [dict(ticker=t,days=1,events=1,first='2026-09-11',last='2026-09-11',signature='x') for t in names]
        if 'hostName()' in sql:return [dict(host='fixture')]
        if sql==c.RULE_SQL:return [dict(token_id=1)]
        raise AssertionError(sql)
    monkeypatch.setattr(c,'query',query)
    p=c.plan(SimpleNamespace(runtime=tmp_path,start='2025-01-01',end='2026-09-12',tickers=None))
    assert len(batches)==3 and max(map(len,batches))<=128
    assert len(p['rows'])==300 and all(r['status']=='queued' for r in p['rows'])
    assert p['input_policy'] == c.POLICY


def test_indexed_source_preserves_historical_sip_and_eligibility_contract():
    rules=[dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),
           dict(token_id=2,modifier_int=12,update_last=0,update_high_low=0,update_volume=1)]
    metadata=dict(event_count=20,next_ordinal=120,last_ordinal=119,first_sip_timestamp_us=1,last_sip_timestamp_us=20)
    sql=bars_sql('SUGP','2026-08-21',rules,metadata)
    assert 'ordinal>=100 AND ordinal<120' in sql
    assert 'execution_timestamp' not in sql
    assert 'argMinIf(price,tuple(sip_timestamp_us,ordinal),last_ok)' in sql
    assert 'sumIf(toFloat64(size_primary),volume_ok)' in sql
    assert 'GROUP BY t ORDER BY t' in sql
    assert 'AND sec>=14700' in sql  # Filter before OHLCV and discovery-noise fitting.


def test_unavailable_seconds_are_counted_not_silently_discarded():
    row=dict(t=1,open=10,high=10,low=10,close=10,volume=100,trades=4,last_count=1,extrema_count=1)
    bars,audit=decode([row,dict(row,t=2,last_count=0)])
    assert len(bars)==1 and audit==dict(trades=8,price_unavailable_seconds=1,price_unavailable_volume=100)
    with pytest.raises(ValueError):decode([row,row])


def test_atomic_store_does_not_overwrite_an_immutable_checkpoint(tmp_path):
    p=tmp_path/'book.json.gz';write(p,dict(value=1));write(p,dict(value=1))
    with pytest.raises(ValueError):write(p,dict(value=2))
    assert read(p)==dict(value=1)


def test_worker_restarts_after_orphan_book_and_validates_receipts(tmp_path,monkeypatch):
    day='2026-08-21';ticker='TEST';start=int(datetime.fromisoformat(day+'T04:00:00-04:00').timestamp())
    prices=[10,10.04,10.08,10.12,10.08,10.04,10]*5
    raw=[dict(t=start+i+1,open=p,high=p,low=p,close=p,volume=100,trades=1,last_count=1,extrema_count=1) for i,p in enumerate(prices)]
    metadata=dict(source_date=day,event_count=35,next_ordinal=35,last_ordinal=34,first_sip_timestamp_us=start*1000000,last_sip_timestamp_us=(start+36)*1000000)
    coverage=dict(ticker=ticker,days=1,events=35,first=day,last=day,signature='fixture')
    p=dict(plan_hash='plan',start=day,end=day,rules=[],rows=[dict(ticker=ticker,status='queued',coverage=coverage)])
    monkeypatch.setattr(c,'checked_plan',lambda _:p)
    def query(sql,threads=2):
        if sql==c.RULE_SQL:return []
        if 'GROUP BY ticker ORDER BY ticker' in sql:return [coverage]
        if 'market_stock_split' in sql:return []
        if 'GROUP BY t ORDER BY t' in sql:return raw
        if 'events_ordinal_continuity' in sql:return [metadata]
        raise AssertionError(sql)
    monkeypatch.setattr(c,'query',query)
    args=SimpleNamespace(runtime=tmp_path,ticker=ticker,threads=1)
    original=c.write
    def fail_receipt(path,value,**kwargs):
        if path.parent.name=='receipts':raise OSError('simulated interruption after book write')
        return original(path,value,**kwargs)
    monkeypatch.setattr(c,'write',fail_receipt)
    with pytest.raises(OSError):c.worker(args)
    book=read(tmp_path/'tickers/TEST/books'/f'{day}.json.gz')
    monkeypatch.setattr(c,'write',original);c.worker(args)
    assert read(tmp_path/'tickers/TEST/books'/f'{day}.json.gz')==book
    c.worker(args)
    status=read(tmp_path/'tickers/TEST/progress.json')
    assert status['state']=='complete' and status['resumed']==1
    receipt=tmp_path/'tickers/TEST/receipts'/f'{day}.json';value=read(receipt);value['source_hash']='corrupt';write(receipt,value,immutable=False)
    with pytest.raises(ValueError,match='Resume source/parent'):c.worker(args)


@pytest.mark.parametrize('width',[80,120])
def test_monitor_is_bounded_and_names_failure_states(width):
    state=dict(state='running',workers=4,rows={'TEST':dict(state='active',slot=0)},sessions_total=400,
        started_epoch=1,updated_at=datetime.now(timezone.utc).isoformat(),sessions_completed=20)
    progress={'TEST':dict(session='2026-08-21',stage='MLE fitting',completed=20,total=400,updated_at=state['updated_at'])}
    output=io.StringIO();console=Console(file=output,width=width,color_system=None)
    console.print(c.render(state,progress,width))
    lines=output.getvalue().splitlines()
    assert max(map(len,lines))<=width and len(lines)<=24
    assert 'failed' in output.getvalue() and 'controller age' in output.getvalue()
