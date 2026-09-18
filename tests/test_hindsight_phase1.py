from datetime import date
import pytest
from src.market_engine.hindsight_phase1 import bounds, opportunities, targets_from_extrema
from src.market_engine.hindsight_phase1_source import validate_universe, quotes_sql, extrema_sql
from scripts.build_hindsight_phase1 import verified, progress_table

DAY=date(2026,8,21)


def records(points):
    # Simulate the exact SQL grouping, retaining boundary points separately.
    bins={}
    for ordinal,(at,price) in enumerate(points):
        us=round(at*1e6);key=((us+999999)//1000000*1000000,us%1000000==0)
        bins.setdefault(key,[]).append((us,ordinal,price))
    return [dict(decision_us=key[0],boundary=key[1],trades=len(group),volume=len(group)*100,
                 missing_clock=0,lo=min(group,key=lambda x:(x[2],x[0],x[1])),hi=min(group,key=lambda x:(-x[2],x[0],x[1]))) for key,group in bins.items()]


def test_compressed_extrema_match_full_base_with_boundary_and_ties():
    from src.market_engine.hindsight import PriceMacdLabels
    from array import array
    points=[(0,5),(0.1,4),(0.2,4),(0.4,6),(0.8,5),(1,7),(1.1,8),(1.5,9),(2,10),(2.2,3),(2.4,2),(2.6,2),(3,20)]
    intervals=[(1,2,'long'),(2,3,'short')]
    oracle=PriceMacdLabels();oracle.times=array('d',(t for t,p in points));oracle.prices=array('d',(p for t,p in points))
    assert targets_from_extrema(records(points),intervals)['positions']==oracle.result(intervals)['positions']


def test_full_grid_unfiltered_values_and_first_target():
    import polars as pl
    left,right=bounds(DAY)
    target=left+4_067_424
    points=list(range(left,right+1,1_000_000))+[target]
    quotes=[dict(time_us=t,quote_us=t,ask=6 if t!=target else 4,bid=5 if t!=target else 3,ask_size=1,bid_size=1) for t in sorted(points)]
    targets=[dict(direction='short',exit_time=target/1e6,position_number=1,label_available_at=(left+10_000_000)/1e6)]
    r=opportunities(DAY,[],quotes,targets)
    assert r.height==57601
    assert r['short_gross_profit'][0]==1
    assert r['spread_bps'][0]>150 and r['trades_10s'][0]==0
    assert r['short_hold_seconds'][0]==pytest.approx(4.067424)
    assert r['short_status'][-1]=='no_future_macd_target'
    assert r['long_gross_profit'].null_count()==r.height


def test_no_stale_or_crossed_quote_is_valued():
    left,right=bounds(DAY)
    quotes=[dict(time_us=t,quote_us=left,ask=4,bid=5,ask_size=1,bid_size=1) for t in range(left,right+1,1_000_000)]
    targets=[dict(direction='long',exit_time=(left+2_000_000)/1e6,position_number=1,label_available_at=(left+3_000_000)/1e6)]
    r=opportunities(DAY,[],quotes,targets)
    assert r['long_gross_profit'].null_count()==r.height


def test_duplicate_and_missing_dated_universe_fail():
    with pytest.raises(ValueError):validate_universe([])
    with pytest.raises(ValueError):validate_universe([dict(ticker='A',listing_id='x')]*2)


def test_corrupt_checkpoint_is_not_reused(tmp_path):
    (tmp_path/'x').write_text('changed')
    with pytest.raises(ValueError,match='integrity'):verified(tmp_path,{'files':{'x':'wrong'}})


def test_sql_uses_canonical_ordinals_and_keeps_invalid_quote():
    meta={'source':dict(event_count=10,next_ordinal=20,last_ordinal=19,first_sip_timestamp_us=0,last_sip_timestamp_us=1)}
    sql=quotes_sql(DAY,'SUGP',meta,[])
    assert 'events_2026' in sql and 'ASOF LEFT JOIN' in sql and 'e.ordinal>=10' in sql
    assert 'bid>0' not in sql and 'ask>0' not in sql


def test_terminal_progress_is_compact():
    from rich.console import Console
    from io import StringIO
    out=StringIO();console=Console(file=out,width=80,color_system=None)
    console.print(progress_table(dict(completed=1,reused=2,failed=1),{'SUGP':'quotes'},10,12))
    assert 'Queued' in out.getvalue() and 'Failed' in out.getvalue() and 'SUGP' in out.getvalue()


def test_intermediate_hash_check_and_unpublished_recovery(tmp_path):
    from scripts.build_hindsight_phase1 import cached_json
    path=tmp_path/'stage.json'
    path.write_text('{"partial":true}')
    assert cached_json(path,lambda:{'complete':True})=={'complete':True}
    assert cached_json(path,lambda:pytest.fail('Should reuse'))=={'complete':True}
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='integrity'):cached_json(path,lambda:{})


def test_quote_query_includes_fresh_presession_and_terminal_quote():
    left,right=bounds(DAY)
    meta={'source':dict(event_count=10,next_ordinal=20,last_ordinal=19,first_sip_timestamp_us=0,last_sip_timestamp_us=1)}
    sql=quotes_sql(DAY,'SUGP',meta,[])
    assert f'e.sip_timestamp_us>={left-1000000}' in sql
    assert f'e.sip_timestamp_us<={right}' in sql


def test_null_quote_fields_remain_explicitly_unavailable():
    left,right=bounds(DAY)
    quotes=[dict(time_us=t,quote_us=t,ask=None,bid=5,ask_size=1,bid_size=1) for t in range(left,right+1,1000000)]
    r=opportunities(DAY,[],quotes,[])
    assert r['quote_valid'].sum()==0 and r['quote_valid'].null_count()==0
