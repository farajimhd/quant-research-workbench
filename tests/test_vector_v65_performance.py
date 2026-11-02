import pytest
from research.vectorized_backtest.v65.performance import configuration
from research.vectorized_backtest.v65.profile_throughput import comparable


@pytest.mark.parametrize('workers',[2,4,6,8,12,16])
@pytest.mark.parametrize('block',[128,256,512,1024])
def test_bounded_execution_settings(workers,block):
    assert configuration(dict(global_session_workers=workers,replay_tuning=dict(time_block=block,transfer_mode='events')))==dict(
        session_workers=workers,time_block=block,transfer_mode='events')


@pytest.mark.parametrize('job',[
    dict(global_session_workers=10),dict(replay_tuning=dict(time_block=4096)),
    dict(replay_tuning=dict(transfer_mode='unknown')),dict(replay_tuning=dict(drop_events=True))])
def test_unknown_or_unbounded_settings_rejected(job):
    with pytest.raises(ValueError):configuration(job)


def test_resume_scheduling_changes_require_exact_binding_and_preserve_semantics(tmp_path):
    import json,copy
    from research.vectorized_backtest.v65.resume_authority import global_predecessor
    from research.vectorized_backtest.v65.distributed_assets import sha
    from research.vectorized_backtest.v65.performance import loading
    old=dict(global_session_workers=6,machines={'workstation':dict(code='old',session_workers=6)},
             population=1024,execution={'entry':1000},source_sha256='old')
    path=tmp_path/'old.json';path.write_text(json.dumps(old))
    job=copy.deepcopy(old);job.update(global_session_workers=16,source_sha256='new',host_loading=dict(resident=True,workers=1,depth=6))
    job['machines']['workstation'].update(code='new',session_workers=16)
    job['resume_transition']=dict(predecessor_job=str(path),predecessor_job_sha256=sha(path),scheduling_change=dict(
        before=dict(replay=configuration(old),host=loading(old)),after=dict(replay=configuration(job),host=loading(job))))
    assert global_predecessor(job)==old
    bad=copy.deepcopy(job);bad['execution']['entry']=2000
    with pytest.raises(ValueError,match='semantics'):global_predecessor(bad)
    bad=copy.deepcopy(job);bad['global_session_workers']=12
    with pytest.raises(ValueError,match='Scheduling transition'):global_predecessor(bad)
    bad=copy.deepcopy(job);del bad['resume_transition']['scheduling_change']
    with pytest.raises(ValueError,match='semantics'):global_predecessor(bad)


def test_order_parity_ignores_paths_but_preserves_counts_and_content():
    def metrics(path,count,sha):return dict(net_pnl=[1.],execution_orders=[dict(receipt=path,candidate_index=0,orders=count,sha256=sha)])
    baseline=comparable(metrics('a',10,'abc'))
    assert baseline==comparable(metrics('b',10,'abc'))
    assert baseline!=comparable(metrics('b',11,'abc'))
    assert baseline!=comparable(metrics('b',10,'def'))


def test_compact_admission_counts_growth_and_blocks_without_gpu_episode_ledger():
    from types import SimpleNamespace
    from research.vectorized_backtest.v65.performance import memory_budget
    def member(nodes):return SimpleNamespace(routing=(),networks={'entry':SimpleNamespace(nodes=range(nodes))})
    item=SimpleNamespace(bytes=100,resident_features=SimpleNamespace(bytes=200),clocks=100)
    small=memory_budget(item,[member(2)],[[0]],256)
    grown=memory_budget(item,[member(100)],[[0]],256)
    larger=memory_budget(item,[member(2)],[[0]],512)
    assert small['resident_bytes']==300 and small['cpu_episode_ledger_gpu_bytes']==0
    assert grown['required_bytes']>small['required_bytes']
    assert larger['required_bytes']>small['required_bytes']
    assert small['context_headroom_bytes']==2*1024**3


def test_session_rotation_releases_only_obsolete_device_residency(monkeypatch):
    from types import SimpleNamespace
    from research.vectorized_backtest.v65.session_lanes import Worker
    import torch
    calls=[]
    def item(name):
        return SimpleNamespace(deactivate=lambda:calls.append(name),
            resident_features=SimpleNamespace(deactivate=lambda:calls.append(name+' features')))
    worker=Worker.__new__(Worker);worker.host={'old':item('old'),'active':item('active')}
    worker.plans={'retained':object()}
    monkeypatch.setattr(torch.cuda,'empty_cache',lambda:calls.append('release cache'))
    worker.retire_device_sessions('active',torch.device('cuda'))
    assert calls==['old features','old','release cache']
    assert set(worker.host)=={'old','active'} and 'retained' in worker.plans


def test_arte_resident_banks_share_immutable_storage_without_changing_legacy_ownership():
    import numpy as np
    from types import SimpleNamespace
    from research.vectorized_backtest.v65.resident_features import ResidentFeatures
    arrays=dict(features=np.ones((2,3),np.float32),feature_valid=np.ones((2,3),bool),
                clocks=np.array([1,2]),market_keys=np.array([1,2]))
    bank=dict(values=np.ones((2,332),np.float32),source_rows=np.array([[0],[1]]),ids=np.array([[0],[1]]))
    market={k:np.ones(2,np.float64) for k in ('mark','bid','ask','quote_us','volume','notional','trade_count')}
    market['feature_row']=np.array([0,1])
    item=SimpleNamespace(arrays=arrays,bank=bank,market=market,indicator_values=np.ones((2,5),np.float32),
        enriched_values=np.ones((2,4),np.float32),shard_values=np.ones((2,8),np.float32))
    resident=ResidentFeatures(item)
    for name,value in [('base',arrays['features']),('history',bank['values']),
                       ('indicators',item.indicator_values),('enriched',item.enriched_values),('shards',item.shard_values)]:
        assert np.shares_memory(resident.host[name].numpy(),value)
        assert np.array_equal(resident.host[name].numpy(),value)
    del item.shard_values
    legacy=ResidentFeatures(item)
    assert not np.shares_memory(legacy.host['base'].numpy(),arrays['features'])
    assert not np.shares_memory(legacy.host['indicators'].numpy(),item.indicator_values)
