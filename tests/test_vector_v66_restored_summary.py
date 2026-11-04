import json
import numpy as np
import pytest
from research.vectorized_backtest.v66 import restored_summary as summary
from research.vectorized_backtest.v66.distributed_assets import sha
from research.vectorized_backtest.v66.full_common import aggregate
from test_vector_v66_streamed_scoring import session


def fixture(tmp_path):
    day='2026-01-05';metrics=session(5)
    metrics.update(capital_dollar_seconds=np.arange(12,dtype=float),turnover=np.arange(12,dtype=float),
                   ticker_score=[{'ticker':float(v)} for v in metrics['net_pnl']])
    def encode(value):
        if isinstance(value,np.ndarray):return value.tolist()
        raise TypeError(type(value))
    path=tmp_path/'original.json'
    path.write_text(json.dumps(dict(day=day,split='training',population_sha256='token',full_session=True,
        execution={},validation_opened=False,case_owners=[None]*12,metrics=metrics),default=encode))
    job=dict(source_sha256='source',objective=dict(drawdown_weight=0.,loss_weight=0.),execution={},
        founder_readmission=dict(completed_replays={f'training/0/{day}':dict(path=str(path),sha256=sha(path))}))
    return job,day,path,metrics


def test_restored_summary_exact_and_cache_avoids_json_ledgers(tmp_path,monkeypatch):
    job,day,path,raw=fixture(tmp_path);out=tmp_path/'new'
    first=summary.prepared(job,out,'training',day,0,'token',12)
    for key,values in aggregate([raw]).items():
        np.testing.assert_array_equal(first['metrics']['_session_panel'][key],values)
    assert 'episode_profits' not in first['metrics']
    monkeypatch.setattr(summary,'restore',lambda *a:pytest.fail('reparsed JSON ledger'))
    second=summary.prepared(job,out,'training',day,0,'token',12)
    assert second['summary_cached'] and second['ref']['receipt']==str(path)
    for key in first['metrics']['_session_panel']:
        np.testing.assert_array_equal(second['metrics']['_session_panel'][key],first['metrics']['_session_panel'][key])
    from research.vectorized_backtest.v66.behavior_sketch import sketch
    np.testing.assert_array_equal(sketch(second['metrics'],12)[0],sketch(raw,12)[0])
    assert second['metrics']['selection_behavior_keys']==first['metrics']['selection_behavior_keys']
    assert second['metrics']['ticker_score']==first['metrics']['ticker_score']
    path.write_text('{}')
    with pytest.raises(ValueError,match='receipt changed'):summary.prepared(job,out,'training',day,0,'token',12)


def test_live_panel_reads_reused_summary_without_new_receipt(tmp_path):
    from research.vectorized_backtest.v66.progress_sessions import load_live_session
    job,day,path,raw=fixture(tmp_path);root=tmp_path/'new'
    summary.prepared(job,root/'replays/training','training',day,0,'token',12)
    panel,details=load_live_session(job,root,0,'training',day,list(range(12)),'token')
    assert details['cached'] and details['replay_details']['reused_candidates']==12
    assert details['replay_details']['evaluated_candidates']==0
    np.testing.assert_array_equal(panel['panel']['score'],aggregate([raw])['score'])
    assert not (root/'replays/training/generation-0000'/day/'receipt.json').exists()


def test_summary_tampering_fails_closed(tmp_path):
    job,day,path,raw=fixture(tmp_path);out=tmp_path/'new'
    summary.prepared(job,out,'training',day,0,'token',12)
    marker=summary.location(out,0,day);record=json.loads(marker.read_bytes())
    marker.with_name(record['arrays']).write_bytes(b'bad')
    with pytest.raises(ValueError,match='arrays changed'):summary.prepared(job,out,'training',day,0,'token',12)


def test_progress_recognizes_original_receipt_summary(tmp_path):
    from research.vectorized_backtest.v66.campaign_progress import Model
    job,day,path,raw=fixture(tmp_path);root=tmp_path/'new'
    job.update(training_days=[day],validation_days=[])
    folder=root/'global/generation-0000';folder.mkdir(parents=True)
    (folder/'population.json').write_text('{}')
    model=object.__new__(Model);model.job=job;model.replay_sample_scope=None;model.replay_samples={}
    model.global_root=lambda:root
    model.status=lambda:dict(generation=0,completed_generations=0,replay_generation=0,replay_role='training',
        stage='Restore saved founder results',session_paths={},active_sessions=[],owner={})
    before=model.global_replays()['training:'+day]
    assert before['status']=='queued' and before['reused']
    summary.prepared(job,root/'replays/training','training',day,0,'token',12)
    assert model.global_replays()['training:'+day]['status']=='complete'


def test_pruned_summaries_preserve_champion_selection():
    from test_vector_v66_founder_pool import evidence
    from research.vectorized_backtest.v66.founder_selection import select,session_descriptor
    from research.vectorized_backtest.v66.full_common import prepare_summary
    ordered,panel=evidence();before=select(ordered,panel,8)
    prepared=[]
    for m in ordered:
        prepare_summary(m);m['_founder_descriptor']=session_descriptor(m)
        prepared.append({k:m[k] for k in ('net_pnl','closed_positions','terminal_valid','_session_panel','_summary_objective','_founder_descriptor')})
    assert select(prepared,panel,8)==before


def test_initial_admission_reproduces_instead_of_passing_through(tmp_path,monkeypatch):
    from research.vectorized_backtest.v66.founder_reproduction import reproduce
    from research.vectorized_backtest.v66 import lineage
    job,day,path,raw=fixture(tmp_path)
    metrics=summary.prepared(job,tmp_path/'out','training',day,0,'token',12)['metrics']
    members=list(range(12));ids=np.arange(12);selected=[3,8]
    def breed(rng,parents,parent_ids,pedigree,rank,generation,selection,**kw):
        assert parents==[3,8] and parent_ids.tolist()==[3,8] and generation==1
        assert kw['target_size']==1024
        np.testing.assert_array_equal(kw['ordered'][0]['net_pnl'],np.asarray(raw['net_pnl'])[selected])
        assert kw['ordered'][0]['ticker_score']==[raw['ticker_score'][i] for i in selected]
        return ['child']*1024,np.arange(100,1124),{}
    monkeypatch.setattr(lineage,'next_population',breed)
    children,newids,report=reproduce(selected,members,ids,None,[metrics],None,None,None,None)
    assert len(children)==1024 and report['initial_new_candidates']==1024


def test_initial_view_available_before_generation_seal(tmp_path):
    from threading import Event
    from research.vectorized_backtest.v66.initial_progress import InitialData
    job,day,path,raw=fixture(tmp_path);token='a'*64
    receipt=json.loads(path.read_bytes());receipt['population_sha256']=token;path.write_text(json.dumps(receipt))
    job['founder_readmission']['completed_replays'][f'training/0/{day}']['sha256']=sha(path)
    job.update(training_days=[day],fresh_initial_replay='v66-fresh-founders-v1')
    root=tmp_path/'run';folder=root/'phase-2/global/generation-0000';folder.mkdir(parents=True)
    (folder/'population.json').write_text(json.dumps(dict(ids=list(range(12)),population=[],population_sha256=token),sort_keys=True,separators=(',',':')))
    summary.prepared(job,root/'phase-2/replays/training','training',day,0,token,12)
    model=InitialData(job,root,Event());model.evidence=model.cached_evidence(folder)
    assert len(model.evidence['receipts'])==1 and not (folder/'training.json').exists()
    np.testing.assert_array_equal(model.evidence['panel']['score'],aggregate([raw])['score'])
    model.request=3;model.load_sessions()
    assert model.error is None and len(model.sessions)==1
    assert model.sessions[0]['score']==aggregate([raw])['score'][3]


def test_position_timeline_uses_shared_shards_not_legacy_clocks(tmp_path):
    from research.vectorized_backtest.v66.position_details import shard_timeline
    from research.market_shards.v1.contract import origin_us
    day='2026-01-05';bars=tmp_path/'bars'/day;bars.mkdir(parents=True)
    seal=bars/'complete.json';seal.write_text('{}');job={}
    for role,ticker in [('training','AAA'),('validation','BBB')]:
        manifest=tmp_path/(role+'-shards.json');manifest.write_text(json.dumps({'sessions':[{'day':day,'selected':[ticker]}]}))
        wrapper=tmp_path/(role+'.json');wrapper.write_text(json.dumps(dict(days=[day],inputs=str(bars.parent),
            receipts={day:sha(seal)},shard_manifest=str(manifest),shard_manifest_sha256=sha(manifest))))
        job[role]=str(wrapper);job[role+'_sha256']=sha(wrapper)
    receipt=dict(result_identity=dict(data=dict(input_sha256=sha(tmp_path/'training-shards.json'))))
    clocks,records=shard_timeline(job,tmp_path,'training',day,receipt)
    assert len(clocks)==19800 and clocks[0]==origin_us(day)//1000000+14401
    assert clocks[-1]==origin_us(day)//1000000+34200
    assert [v['listing_id'] for v in records[1:]]==['ARTE:AAA','ARTE:BBB']
    receipt['result_identity']['data']['input_sha256']='changed'
    with pytest.raises(ValueError,match='shared-shard identity changed'):shard_timeline(job,tmp_path,'training',day,receipt)
