from threading import Event, Lock
from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v65.session_prefetch import SessionPrefetch


class Item:
    device=SimpleNamespace(type='cpu')
    def __init__(self):self.closed=False
    def close(self):self.closed=True


def test_late_preparation_events_cannot_rewind_active_replay():
    from research.vectorized_backtest.v65.session_pipeline import SessionProgress
    progress=SessionProgress(SimpleNamespace(publish=lambda **k:None),['a'],1024,1,'training')
    progress.event(dict(day='a',stage='Prepare resident RAM'))
    assert progress.rows['a']['status']=='preparing'
    progress.event(dict(day='a',stage='Resident RAM ready'))
    assert progress.rows['a']['status']=='queued'
    progress.begin('a')
    progress.event(dict(day='a',stage='Resident RAM ready'))
    assert progress.rows['a']['status']=='running' and progress.rows['a']['stage']=='Load session'


def test_resident_preparation_keeps_all_assigned_sessions_and_reuses_them(tmp_path,monkeypatch):
    from collections import OrderedDict
    from research.vectorized_backtest.v65 import session_lanes as lanes
    calls=[]
    queue=SessionPrefetch(lambda d:(calls.append(d) or Item()),workers=2,capacity=3,
        reserve_gib=0,admission_gib=1,snapshot=lambda:SimpleNamespace(available=100*2**30))
    worker=SimpleNamespace(host=OrderedDict(),maximum_host_sessions=12,prefetch=queue,
        check_stop=lambda:None,emit=lambda *a,**k:None,lane=0,config={'output':str(tmp_path)})
    monkeypatch.setattr(lanes,'_worker',worker)
    days=[str(i) for i in range(12)]
    try:
        result=lanes.prime_sessions(days)
        assert result['ready']==12 and not result['deferred']
        assert set(worker.host)==set(days) and len(calls)==12
        lanes.prime_sessions(days)
        assert len(calls)==12 and all(not item.closed for item in worker.host.values())
        with pytest.raises(ValueError,match='capacity'):lanes.prime_sessions(days+['overflow'])
    finally:queue.close()


def test_resident_preparation_reports_memory_deferral_without_demand_allocation(tmp_path,monkeypatch):
    from research.vectorized_backtest.v65 import session_lanes as lanes
    queue=SessionPrefetch(lambda d:pytest.fail('Unsafe allocation'),workers=1,capacity=2,
        reserve_gib=128,admission_gib=32,snapshot=lambda:SimpleNamespace(available=100*2**30))
    monkeypatch.setattr(lanes,'_worker',SimpleNamespace(host={},maximum_host_sessions=3,prefetch=queue,
        check_stop=lambda:None,emit=lambda *a,**k:None,lane=0,config={'output':str(tmp_path)}))
    try:
        result=lanes.prime_sessions(['a','b','c'])
        assert result['ready']==0 and result['deferred']==['a','b','c']
    finally:queue.close()


def test_prepares_three_sessions_concurrently_with_bounded_lookahead():
    gate=Event();entered=Event();lock=Lock();active=0;peak=0;calls=[]
    def load(day):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active);calls.append(day)
            if active==3:entered.set()
        assert gate.wait(5)
        with lock:active-=1
        return Item()
    queue=SessionPrefetch(load,workers=3,capacity=6,reserve_gib=1,admission_gib=1,
        snapshot=lambda:SimpleNamespace(available=100*2**30))
    try:
        queue.schedule([str(i) for i in range(12)])
        assert entered.wait(5)
        assert len(queue.pending)==6 and peak==3
        assert queue.stats()['preparation_running']==3
        gate.set()
        result=queue.take('0');assert not result.closed
        queue.close();assert not result.closed
    finally:gate.set();queue.close()


def test_cached_sessions_are_not_reconstructed():
    queue=SessionPrefetch(lambda d:pytest.fail('Rebuilt retained session'),workers=1,capacity=2)
    try:
        queue.schedule(['a','b'],{'a':Item(),'b':Item()})
        assert not queue.pending
    finally:queue.close()


def test_pressure_defers_without_allocating_or_suppressing_failures():
    queue=SessionPrefetch(lambda d:pytest.fail('Allocated below RAM reserve'),workers=1,capacity=1,
        reserve_gib=128,admission_gib=32,snapshot=lambda:SimpleNamespace(available=100*2**30))
    try:
        queue.schedule(['a']);assert queue.take('a') is None
    finally:queue.close()
    def broken(day):raise ValueError('Bad immutable shard')
    queue=SessionPrefetch(broken,workers=1,capacity=1,reserve_gib=0,admission_gib=1,
        snapshot=lambda:SimpleNamespace(available=100*2**30))
    try:
        queue.schedule(['a'])
        with pytest.raises(ValueError,match='Bad immutable shard'):queue.take('a')
    finally:queue.close()


def test_preparation_rejects_cuda_and_releases_failed_item():
    item=Item();item.device=SimpleNamespace(type='cuda')
    queue=SessionPrefetch(lambda d:item,workers=1,capacity=1,reserve_gib=0,admission_gib=1,
        snapshot=lambda:SimpleNamespace(available=100*2**30))
    try:
        queue.schedule(['a'])
        with pytest.raises(ValueError,match='CUDA'):queue.take('a')
        assert item.closed
    finally:queue.close()


def test_partial_resume_sends_population_to_first_unfinished_session(tmp_path,monkeypatch):
    import json
    from concurrent.futures import Future
    from research.vectorized_backtest.v65.session_lanes import SessionLanes
    from research.vectorized_backtest.v65.distributed_assets import sha
    from research.vectorized_backtest.v65 import controller_metrics
    receipt=tmp_path/'generation-0001'/'a'/'receipt.json';receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(day='a',split='training',population_sha256='token',full_session=True,
        execution={},validation_opened=False,case_owners=[None],metrics={'net_pnl':[1.]})))
    bound={'training/1/a':sha(receipt)}
    monkeypatch.setattr(controller_metrics,'compact',lambda x:x)
    calls=[];finished=[]
    class Pool:
        def submit(self,fn,*args):
            calls.append(args);future=Future()
            future.set_result(dict(day=args[0],metrics={},ref={},timings=[],wall_seconds=0,clocks=19799))
            return future
    lanes=SessionLanes.__new__(SessionLanes)
    lanes.workers=1;lanes.widths={'a':1,'b':1,'c':1};lanes.error=None
    lanes.config=dict(output=str(tmp_path),split='training',immutable_population_cache=True,defer_publication=True)
    lanes.panel=SimpleNamespace(job=dict(execution={},resume_transition={'completed_replays':bound}),
        session_result=lambda day,result,role,generation:finished.append((day,result.get('restored',False))))
    lanes.pools=[Pool()]
    results=lanes.generation(['a','b','c'],[SimpleNamespace(payload=lambda:{'genome':1})],1,'token')
    assert [r['day'] for r in results]==['a','b','c']
    assert finished==[('a',True),('b',False),('c',False)]
    assert calls[0][0]=='b' and calls[0][2]==[{'genome':1}]
    assert calls[1][0]=='c' and calls[1][2] is None
    assert sha(receipt)==bound['training/1/a']
    receipt.write_text('{}')
    with pytest.raises(ValueError,match='receipt changed'):
        lanes.generation(['a','b','c'],[SimpleNamespace(payload=lambda:{'genome':1})],1,'token')
