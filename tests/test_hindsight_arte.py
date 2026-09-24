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
    return pl.DataFrame([dict(time_us=t,low=lo,high=hi,close=(lo+hi)/2,price_valid=1,resolution_ms=100,extremes_valid=1,
                             volume=1.,trade_count=1) for t,lo,hi in points],
        schema={'time_us':pl.Int64,'low':pl.Float64,'high':pl.Float64,'resolution_ms':pl.Int64,
                'extremes_valid':pl.Int64,'volume':pl.Float64,'trade_count':pl.Int64,'close':pl.Float64,'price_valid':pl.Int64})


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


def test_volume_and_prices_use_completed_bars_without_quote_fields():
    left,right = bounds(DAY)
    bars = bar_frame([(left+100000,1.,2.),(left+1000000,1.,2.),(left+2000000,1.,2.)])
    bars = bars.with_columns(pl.Series('resolution_ms',[100,1000,1000]),pl.Series('volume',[999.,3.,7.]))
    values = labels.decision_values(DAY,bars,[])
    assert values['volume'].head(3).to_list() == [0.,3.,7.]
    assert values['session_eligible_volume'][-1] == 10.
    assert values['price_valid'].head(3).to_list() == [False,True,True]
    assert values['decision_price'].head(3).to_list() == [None,1.5,1.5]
    assert values['price_age_seconds'][2] == pytest.approx(1.9)
    assert not {'ask','bid','quote_valid','spread_bps','quote_us'} & set(values.columns)


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


def test_source_queries_only_bars_and_indicators(monkeypatch):
    captured = []
    monkeypatch.setattr(source,'frame',lambda c,q,s:captured.append(q))
    context = dict(build_id='build',units={str(DAY):{'A':{stage:{'attempt_id':str(uuid4())} for stage in source.TABLES}}})
    source.inputs(None,context,DAY,'A')
    assert set(source.TABLES.values()) == {'bars_v1','indicators_v1'}
    assert 'close_int/10000.' in captured[0]
    for q in captured:
        assert '(toInt64(bucket_index)+1)' in q
        assert all(word not in q for word in ('events_','liquidity','quote','bid','ask'))
        assert 'attempt_id=' in q


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
    args = ['benchmark','--date',str(DAY),'--tickers','A','--workers','1']
    assert runner.main(args) == 0
    root = next((tmp_path/'hindsight-arte').iterdir())
    summary = read(root/'summary.json')
    assert summary['results'][0]['coverage']['long_short']['unavailable_seconds'] == 0
    p1 = root/'days'/str(DAY)/'phase1'
    plan = read(p1/'plan.json')
    assert plan['valuation_basis'] == 'price_action' and plan['version'] == labels.VERSION
    output = pl.read_parquet(next((p1/'listings').glob('*/opportunities.parquet')))
    assert not any('quote' in c or c in ('bid','ask') for c in output.columns)
    assert read(__import__('pathlib').Path(summary['results'][0]['phase2_root'])/'plan.json')['valuation_basis'] == 'price_action'
    assert runner.main(args) == 0
    p1 = root/'days'/str(DAY)/'phase1'
    assert read(p1/'summary.json')['counts']['reused'] == 1
    (root/'STOP').touch()
    assert runner.main(args) == 2 and not (root/'complete.json').exists()


def test_price_labels_use_extremum_not_exit_close_and_keep_negative_values():
    from src.market_engine.hindsight_greedy import coefficients
    left,right = bounds(DAY)
    bars = bar_frame([(left+100000,9.,11.),(left+1100000,10.,30.)])
    targets = [dict(direction='long',exit_time=(left+1100000)/1e6,exit_price=30.,position_number=1,
                    label_available_at=(left+3000000)/1e6),
               dict(direction='short',exit_time=(left+1100000)/1e6,exit_price=12.,position_number=2,
                    label_available_at=(left+3000000)/1e6)]
    values = labels.decision_values(DAY,bars,targets).with_columns(pl.lit('A').alias('ticker'),pl.lit('A-id').alias('listing_id'))
    current = values.filter(pl.col('time_us') == left+1000000)
    assert current['decision_price'][0] == 10. # The next 100 ms bar is not complete yet.
    assert current['long_gross_profit'][0] == 20.
    assert current['short_gross_profit'][0] == -2.
    assert current['long_status'][0] == current['short_status'][0] == 'available'
    actual = coefficients(current,gamma=1,cost_per_share=.1,valuation_basis='price_action')
    assert actual['open_profit_per_share'].to_list() == pytest.approx([19.8,-2.2])
    assert actual['hold_profit_per_share'].to_list() == pytest.approx([20.,-2.])
    # Observed prices carry through sparse periods, without a one-second quote-age gate.
    later = labels.decision_values(DAY,bars,[{**targets[0],'exit_time':(left+6000000)/1e6}])
    assert later.filter(pl.col('time_us') == left+5000000)['long_status'][0] == 'available'
    assert values['long_status'][-1] == 'no_future_macd_target'


def test_liquidation_at_1958_prices_losses_without_future_bars_and_forces_flat():
    from src.market_engine.hindsight_greedy import coefficients, ActionTable, Position
    from scripts.build_hindsight_greedy import summarize_listing, flat_policy
    left,right = bounds(DAY)
    cutoff = labels.liquidation_time(DAY)
    assert cutoff == right-120000000
    bars = bar_frame([(left+100000,10.,10.),(cutoff,8.,8.),(cutoff+100000,100.,100.)])
    late_target = dict(direction='long',exit_time=(cutoff+100000)/1e6,exit_price=100.,position_number=1,
                       label_available_at=right/1e6)
    f = labels.decision_values(DAY,bars,[late_target],liquidation_us=cutoff).with_columns(
        pl.lit('A').alias('ticker'),pl.lit('A-id').alias('listing_id'))
    before = f.filter(pl.col('time_us') == cutoff-1000000)
    assert before['long_target_kind'][0] == 'session_liquidation'
    assert before['long_target_us'][0] == cutoff
    assert before['long_target_price'][0] == 8.
    assert before['long_gross_profit'][0] == -2.
    assert before['short_gross_profit'][0] == 2.
    assert before['long_hold_seconds'][0] == 1.
    assert before['long_available_us'][0] == cutoff
    terminal = f.filter(pl.col('time_us') >= cutoff)
    assert terminal.height == 121
    assert terminal['decision_price'].unique().to_list() == [8.]
    assert terminal['long_hold_seconds'].unique().to_list() == [0.]
    values = coefficients(f,valuation_basis='price_action')
    end = values.filter(pl.col('time_us') == cutoff)
    assert end['can_open'].sum() == 0 and end['can_close'].sum() == 2
    table = ActionTable(end.to_dicts(),[Position('A','long',1.,10.,10.)])
    assert 'terminal_requires_liquidation:A:long' in table.evaluate({})['reasons']
    assert not table.evaluate({'A:long':-.5})['feasible']
    exit = table.evaluate({'A:long':-1.})
    assert exit['feasible'] and exit['realized_pnl_now'] == -2.
    assert exit['discounted_future_value'] == 0
    assert not ActionTable(end.to_dicts(),[]).evaluate({'A:long':1.})['feasible']
    policy = flat_policy(summarize_listing(values,'long_short'))
    assert policy.filter(pl.col('time_us') >= cutoff)['chosen_action'].unique().to_list() == ['wait']


def test_sparse_terminal_price_retains_observation_time_and_dst_clock():
    from datetime import datetime
    from src.market_engine.hindsight_phase1 import NY
    for day in (date(2026,1,5),date(2026,8,21)):
        cutoff = labels.liquidation_time(day)
        assert datetime.fromtimestamp(cutoff/1e6,NY).strftime('%H:%M:%S') == '19:58:00'
        bars = bar_frame([(cutoff-300000000,5.,5.),(cutoff+100000,10.,10.)])
        f = labels.decision_values(day,bars,[],liquidation_us=cutoff)
        at = f.filter(pl.col('time_us') == cutoff).row(0,named=True)
        assert at['decision_price'] == 5. and at['price_age_seconds'] == 300.
        assert at['liquidation_price_us'] == cutoff-300000000
    empty = labels.decision_values(DAY,bar_frame([]),[],liquidation_us=labels.liquidation_time(DAY))
    assert empty['long_target_price'].null_count() == empty.height
    assert empty['long_status'].unique().to_list() == ['terminal_price_unavailable']
