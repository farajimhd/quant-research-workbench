from datetime import date
import random
import sqlite3
from uuid import uuid4

import polars as pl
import pytest

from src.market_engine import hindsight_arte as labels
from src.market_engine import hindsight_arte_source as source
from src.market_engine.hindsight_phase1 import bounds, digest
from src.market_engine.level_book_store import read, write

DAY = date(2026,8,21)


def bar_frame(points):
    return pl.DataFrame([dict(time_us=t,low=lo,high=hi,resolution_ms=100,extremes_valid=1,
                             volume=1.,trade_count=1) for t,lo,hi in points],
        schema={'time_us':pl.Int64,'low':pl.Float64,'high':pl.Float64,'resolution_ms':pl.Int64,
                'extremes_valid':pl.Int64,'volume':pl.Float64,'trade_count':pl.Int64})


def test_vectorized_targets_match_independent_bar_oracle():
    rng = random.Random(19)
    points = [(t*100000, float(rng.randint(1,10)),0.) for t in range(1,1000) if rng.random() > .15]
    points = [(t,lo,lo+rng.randint(0,5)) for t,lo,_ in points]
    episodes = pl.DataFrame({'position_number':range(1,21),'start_us':[i*4000000 for i in range(1,21)],
        'end_us':[(i+1)*4000000 for i in range(1,21)],'direction':[1,-1]*10})
    for lookback in (0,2,30):
        expected = []
        for e in episodes.to_dicts():
            entries = [p for p in points if e['start_us']-lookback*1000000 <= p[0] <= e['start_us']]
            if not entries: continue
            side = e['direction']
            entry = min(entries,key=lambda p:(p[1] if side==1 else -p[2],p[0]))
            exits = [p for p in points if e['start_us'] <= p[0] < e['end_us'] and p[0] > entry[0]]
            if not exits: continue
            exit = min(exits,key=lambda p:(-p[2] if side==1 else p[1],p[0]))
            ep,xp = (entry[1],exit[2]) if side==1 else (entry[2],exit[1])
            if (xp-ep)*side > 0: expected.append((e['position_number'],entry[0],exit[0],ep,xp))
        actual = labels.targets(bar_frame(points),episodes,lookback)
        assert [(p['position_number'],round(p['entry_time']*1e6),round(p['exit_time']*1e6),p['entry_price'],p['exit_price'])
                for p in actual['positions']] == expected


def test_sparse_macd_neutral_and_terminal_boundaries():
    frame = pl.DataFrame({'time_us':[1000000,2000000,5000000,6000000,9000000],
                         'macd_line':[1.,1.,0.,-1.,-1.],'macd_signal':[0.]*5})
    assert labels.intervals(frame,10000000).select('start_us','end_us','direction').rows() == [
        (1000000,5000000,1),(6000000,10000000,-1)]
    with pytest.raises(ValueError,match='unique'):
        labels.intervals(pl.concat([frame,frame]),10000000)
    empty = frame.head(0)
    assert labels.targets(bar_frame([]),labels.intervals(empty,10000000))['positions'] == []


def test_volume_uses_only_completed_seconds_and_quote_age():
    left,right = bounds(DAY)
    bars = bar_frame([(left+100000,1.,2.),(left+1000000,1.,2.),(left+2000000,1.,2.)])
    bars = bars.with_columns(pl.Series('resolution_ms',[100,1000,1000]),pl.Series('volume',[999.,3.,7.]))
    times = list(range(left,right+1,1000000))
    q = pl.DataFrame({'time_us':times,'quote_us':[left]*len(times),'ask':[2.]*len(times),
                     'bid':[1.]*len(times),'bid_size':[1.]*len(times),'ask_size':[1.]*len(times)})
    values = labels.decision_values(DAY,bars,q,[])
    assert values['volume'].head(3).to_list() == [0.,3.,7.]
    assert values['session_eligible_volume'][-1] == 10.
    assert values['quote_valid'].head(3).to_list() == [True,True,False]


def test_source_requires_matching_completed_certificates(tmp_path):
    manifest,ledger = tmp_path/'build.json',tmp_path/'ledger.sqlite3'
    definition = dict(version='market-day-core-v5',database='arte',storage_policy='live_market_ssd',
        trade_eligibility={'excluded_reporting_flag':source.sql.DELAYED},
        plan={'requested':[str(DAY)],'units':[{'source_date':str(DAY),'ticker':'A'}]})
    write(manifest,dict(status='core_complete',build_id='build',definition=definition))
    with sqlite3.connect(ledger) as c:
        c.executescript('CREATE TABLE builds(build_id,definition_hash,status,database_name);'
            'CREATE TABLE units(build_id,session_date,ticker,stage,attempt_id,source_hash,output_rows,output_hash,status);')
        c.execute('INSERT INTO builds VALUES (?,?,?,?)',('build',digest(definition),'core_complete','arte'))
        for stage in source.TABLES:
            c.execute('INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?)',('build',str(DAY),'A',stage,str(uuid4()),'source',0,'0','complete'))
    assert set(source.load_build(manifest,ledger,[DAY])['units'][str(DAY)]['A']) == set(source.TABLES)
    with sqlite3.connect(ledger) as c:
        c.execute("UPDATE units SET status='building' WHERE stage='technical'")
    with pytest.raises(ValueError,match='missing certified technical'):
        source.load_build(manifest,ledger,[DAY])


def test_quote_query_uses_bucket_completion_not_last_event(monkeypatch):
    captured = []
    monkeypatch.setattr(source,'frame',lambda c,q,s:captured.append(q))
    context = dict(build_id='build',units={str(DAY):{'A':{'broker_100ms':{'attempt_id':str(uuid4())}}}})
    source.quote_samples(None,context,DAY,'A',[1000000])
    q = captured[0]
    assert 'p.time_us>=q.end_us' in q and '(toInt64(bucket_index)+1)*100000' in q
    assert 'events_' not in q and 'last_event_us' not in q and 'attempt_id=' in q


def test_corrupt_persisted_product_fails_before_read(monkeypatch):
    context = dict(build_id='build',units={str(DAY):{'A':{stage:dict(attempt_id=str(uuid4()),output_rows=1,output_hash='42')
        for stage in source.TABLES}}})
    monkeypatch.setattr(source,'query',lambda *a:[dict(n=1,unique_keys=1,hash=43)])
    with pytest.raises(ValueError,match='differs from certificate'):
        source.verify_listing(None,context,DAY,'A')


def test_runnable_phase1_phase2_and_resume(tmp_path,monkeypatch):
    from scripts import build_hindsight_arte_dataset as runner
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    left,right = bounds(DAY)
    bars = bar_frame([(left+i*100000,1.+i/100,2.+i/100) for i in range(1,100)])
    seconds = bars.filter(pl.col('time_us')%1000000 == 0).with_columns(pl.lit(1000).alias('resolution_ms'))
    bars = pl.concat([bars,seconds],how='vertical_relaxed')
    indicators = pl.DataFrame({'time_us':[left+1000000,left+5000000,left+9000000],
                               'macd_line':[1.,-1.,1.],'macd_signal':[0.,0.,0.]})
    class Client:
        def close(self): pass
    context = dict(build_id='build',definition_hash='definition',units={str(DAY):{'A':{}}})
    monkeypatch.setattr(source,'load_build',lambda *a:context)
    monkeypatch.setattr(source,'reader',lambda *a:Client())
    monkeypatch.setattr(source,'storage_check',lambda *a:{})
    monkeypatch.setattr(source,'population',lambda *a:([dict(ticker='A',listing_id='listing-A')],dict(certificate={'status':'certified'})))
    monkeypatch.setattr(source,'verify_listing',lambda *a:None)
    monkeypatch.setattr(source,'inputs',lambda *a:(bars,indicators))
    monkeypatch.setattr(runner,'load_env_files',lambda *a,**kw:None)
    monkeypatch.setattr(source,'quote_samples',lambda c,s,d,t,times:pl.DataFrame({'time_us':times,'quote_us':times,
        'ask':[2.]*len(times),'bid':[1.9]*len(times),'bid_size':[1.]*len(times),'ask_size':[1.]*len(times)}))
    args = ['benchmark','--date',str(DAY),'--tickers','A','--workers','1']
    assert runner.main(args) == 0
    root = next((tmp_path/'hindsight-arte').iterdir())
    summary = read(root/'summary.json')
    assert summary['results'][0]['coverage']['long_short']['unavailable_seconds'] > 0
    assert runner.main(args) == 0
    p1 = root/'days'/str(DAY)/'phase1'
    assert read(p1/'summary.json')['counts']['reused'] == 1
    (root/'STOP').touch()
    assert runner.main(args) == 2 and not (root/'complete.json').exists()
