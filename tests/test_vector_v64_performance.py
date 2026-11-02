import pytest
from research.vectorized_backtest.v64.performance import configuration
from research.vectorized_backtest.v64.profile_throughput import comparable


@pytest.mark.parametrize('workers',[2,4,6,8])
@pytest.mark.parametrize('block',[128,256,512,1024])
def test_bounded_execution_settings(workers,block):
    assert configuration(dict(global_session_workers=workers,replay_tuning=dict(time_block=block,transfer_mode='events')))==dict(
        session_workers=workers,time_block=block,transfer_mode='events')


@pytest.mark.parametrize('job',[
    dict(global_session_workers=10),dict(replay_tuning=dict(time_block=4096)),
    dict(replay_tuning=dict(transfer_mode='unknown')),dict(replay_tuning=dict(drop_events=True))])
def test_unknown_or_unbounded_settings_rejected(job):
    with pytest.raises(ValueError):configuration(job)


def test_order_parity_ignores_paths_but_preserves_counts_and_content():
    def metrics(path,count,sha):return dict(net_pnl=[1.],execution_orders=[dict(receipt=path,candidate_index=0,orders=count,sha256=sha)])
    baseline=comparable(metrics('a',10,'abc'))
    assert baseline==comparable(metrics('b',10,'abc'))
    assert baseline!=comparable(metrics('b',11,'abc'))
    assert baseline!=comparable(metrics('b',10,'def'))


def test_compact_admission_counts_growth_and_blocks_without_gpu_episode_ledger():
    from types import SimpleNamespace
    from research.vectorized_backtest.v64.performance import memory_budget
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
    from research.vectorized_backtest.v64.session_lanes import Worker
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
