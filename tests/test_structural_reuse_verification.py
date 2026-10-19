from copy import deepcopy
from datetime import datetime
from math import sin
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pipelines.market_sip.events import structural_checkpoint_storage as storage
from pipelines.market_sip.events import structural_session_checkpoints as sessions


@pytest.fixture
def boundary():
    zone=ZoneInfo('America/New_York')
    start=datetime(2026,7,2,9,tzinfo=zone).timestamp()
    bars=[dict(t=start+(i+1)*15,open=10+sin(i/6),close=10+sin(i/6),
               low=9.98+sin(i/6),high=10.02+sin(i/6),volume=100.) for i in range(300)]
    value=sessions.calculate(prior=None,ticker='TEST',day='2026-07-02',bars=bars,actions=[],
        regular_close=datetime(2026,7,2,13,tzinfo=zone),existing_ah=True)['REGULAR']
    value['source']={'build_id':'certified','output_hash':'exact'}
    levels,observations,row=sessions.normalized_boundary(value,'a'*64)
    assert levels and observations
    return value,levels,observations,row


def client_for(levels, observations):
    calls=[]
    def query(sql):
        calls.append(sql)
        data=observations if storage.OBSERVATIONS_TABLE in sql else levels
        identity='observation_id' if storage.OBSERVATIONS_TABLE in sql else 'level_id'
        return sorted(deepcopy(data),key=lambda r:r[identity])
    return SimpleNamespace(query=query),calls


def test_reuse_projects_identical_rows_with_one_restore_and_no_publication(boundary,monkeypatch):
    value,levels,observations,row=boundary
    original=deepcopy(row)
    client,calls=client_for(levels,observations)
    restore=sessions.restore_boundary;count=[]
    def checked(row,**kwargs):
        assert kwargs==dict(copy_state=False,max_payload_bytes=64*1024**2)
        count.append(1)
        return restore(row,**kwargs)
    monkeypatch.setattr(sessions,'restore_boundary',checked)
    monkeypatch.setattr(sessions,'normalized_boundary',lambda *a:pytest.fail('duplicate projection/serialization'))
    result=storage.verify_reused_boundary(client,row,expected_source=value['source'],expected_prior_hash=value['prior_hash'])
    from src.market_engine.historical_level_checkpoint import digest
    assert digest(result)==digest(value) and row==original
    assert len(count)==1 and len(calls)==2
    assert all('PREWHERE (ticker,valid_from,checkpoint_boundary) IN [' in q for q in calls)
    assert all('SELECT *' in q and ' FINAL ' in q and 'LIMIT ' in q for q in calls)


def test_canonical_payload_bound_is_preserved(boundary):
    with pytest.raises(ValueError,match='payload exceeds'):
        sessions.restore_boundary(boundary[-1],copy_state=False,max_payload_bytes=1)


def test_reuse_progress_is_saved_only_after_both_boundaries_verify(tmp_path,monkeypatch):
    import json,sqlite3
    from datetime import date
    from scripts import backfill_structural_sessions as runner
    from pipelines.certified_research import source as prerequisites
    days=['2026-01-02','2026-01-05'];by_ticker={'TEST':days}
    (tmp_path/'workers').mkdir()
    db=sqlite3.connect(tmp_path/'build-ledger-v2.sqlite3')
    db.execute('CREATE TABLE units(build_id TEXT,session_date TEXT,ticker TEXT,stage TEXT,status TEXT,attempt_id TEXT,source_hash TEXT,output_hash TEXT)')
    db.executemany('INSERT INTO units VALUES(?,?,?,?,?,?,?,?)',
        [('build',d,'TEST','bars','complete','attempt','source','output') for d in days]);db.commit();db.close()
    reporting=[dict(source_date=d,status='complete') for d in days]
    class Client:
        http=object();profile_totals={}
        def __init__(self,*args,**kwargs):pass
        def close(self):pass
        def query(self,sql):
            if runner.COVERAGE_TABLE in sql:
                return [dict(session_date=d,state='complete') for d in ['2025-12-31']+days]
            if 'events_ordinal_continuity' in sql:return [dict(source_date=d) for d in days]
            if 'historical_trade_reporting_coverage' in sql:return reporting
            if sql==runner.RULE_SQL:return []
            raise AssertionError(sql)
    a=SimpleNamespace(run_dir=tmp_path,bars_run_dir=tmp_path,env_file=tmp_path/'unused',
                      endpoint='http://unused',start_date=date(2026,1,1))
    spec=dict(request=dict(rules_hash=runner.digest([])),history_inventory=dict(source_hashes={d:d for d in days}),
        reporting=reporting,rules=[],by_ticker=by_ticker,plan_hash='plan',producer_hash='producer',
        history_start='2025-01-01',end=days[-1],closes={d:d+'T21:00:00+00:00' for d in days},
        build_id='build',owner=dict(token='test-owner'))
    monkeypatch.setattr(runner.builder,'Client',Client)
    monkeypatch.setattr(runner,'require_parent',lambda owner:None)
    monkeypatch.setattr(runner,'load_seed',lambda *args,**kwargs:dict(checkpoint_hash='prior',session='2025-12-31'))
    split_checks=[]
    monkeypatch.setattr(runner,'split_evidence',lambda *a,**k:split_checks.append(k['session']) or [])
    monkeypatch.setattr(prerequisites,'inventory',lambda q,start,end:dict(missing=[],incomplete=[],source_hashes={str(start):str(start)}))
    monkeypatch.setattr(prerequisites,'reporting_inventory',lambda *a:{})
    monkeypatch.setattr(prerequisites,'require_ready',lambda *a:None)
    monkeypatch.setattr(storage,'read_boundaries',lambda c,t,d:[dict(checkpoint_boundary=b) for b in ('PM','REGULAR')])
    seen=[]
    def verify(c,row,**kwargs):
        p=json.loads(runner.ticker_file(tmp_path,'TEST','.json').read_text())
        assert p['stage']=='verifying reuse'
        assert p['reused']==len(seen)//2
        assert kwargs['expected_source']['canonical_hash']==days[len(seen)//2]
        seen.append(row['checkpoint_boundary'])
    monkeypatch.setattr(storage,'verify_reused_boundary',verify)
    monkeypatch.setattr(storage,'publish_boundary',lambda *a:pytest.fail('Republishing a completed boundary'))
    result=runner._process_ticker(spec,a,'TEST')
    assert result['completed']==0 and result['reused']==2
    assert result['profile_totals']['reuse_verify']>=0
    assert seen==['PM','REGULAR','PM','REGULAR']
    assert split_checks==[date(2026,1,2),date(2026,1,5)]
    assert json.loads(runner.ticker_file(tmp_path,'TEST','.json').read_text())['status']=='complete'


@pytest.mark.parametrize('damage',['missing','extra','field','duplicate'])
def test_full_normalized_row_integrity_is_preserved(boundary,damage):
    value,levels,observations,row=boundary
    if damage=='missing':observations.pop()
    elif damage=='extra':observations.append(dict(observations[-1],observation_id='z'*64))
    elif damage=='field':observations[0]['price']+=0.01
    else:observations.append(deepcopy(observations[-1]))
    client,_=client_for(levels,observations)
    with pytest.raises(ValueError,match='snapshot differs'):
        storage.verify_reused_boundary(client,row,expected_source=value['source'],expected_prior_hash=value['prior_hash'])


@pytest.mark.parametrize('damage',['payload','header','source','parent','inner_hash'])
def test_boundary_and_lineage_corruption_is_still_rejected(boundary,damage):
    import json
    from src.market_engine.historical_level_checkpoint import digest
    value,levels,observations,row=boundary
    expected_source=deepcopy(value['source']);expected_prior=value['prior_hash']
    if damage=='payload':row['streaming_state']+='x'
    elif damage=='header':row['ticker']='OTHER'
    else:
        payload=json.loads(row['streaming_state'])
        if damage=='source':payload['source']={'build_id':'wrong'}
        elif damage=='parent':payload['prior_hash']='wrong'
        else:payload['streaming_state']['hash']='wrong'
        row['streaming_state']=json.dumps(payload);row['streaming_state_hash']=digest(payload)
    client,_=client_for(levels,observations)
    with pytest.raises((ValueError,KeyError)):
        storage.verify_reused_boundary(client,row,expected_source=expected_source,expected_prior_hash=expected_prior)
