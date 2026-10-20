from concurrent.futures import Future,ThreadPoolExecutor
from types import SimpleNamespace
import copy
import threading
import numpy as np
from research.vectorized_backtest.v32.global_pipeline import GenerationPopulation
from research.vectorized_backtest.v32.genome import sample
from research.vectorized_backtest.v32.network_pack import pack_networks


def test_generation_packs_exact_selections_once_without_rng_or_mutation():
    rng=np.random.default_rng(2020);members=sample(rng,10)
    before=[m.payload() for m in members];state=copy.deepcopy(rng.bit_generator.state)
    shared=GenerationPopulation(members,maximum_batches=2);calls=[]
    def submit(fn,*args):
        calls.append(args);future=Future();future.set_result(fn(*args));return future
    selection=[0,2,5,8];a=shared.pack(selection,submit);b=shared.pack(selection,submit)
    assert a is b and shared.hits==1 and len(calls)==1
    packed,_=a.result();reference=pack_networks([members[i] for i in selection])
    for key in reference:
        if key=='values':
            assert all(np.array_equal(x,y) for x,y in zip(packed[key],reference[key]))
        elif isinstance(reference[key],np.ndarray):assert np.array_equal(packed[key],reference[key])
        else:assert packed[key]==reference[key]
    shared.pack([1,3],submit);shared.pack([4,7],submit)
    assert len(shared.batches)==2
    assert len(calls)==1
    view=shared.subset([8,0,7,2])
    selected,_=view.pack([2,0,1],submit).result()
    expected=pack_networks([members[i] for i in [7,8,0]])
    assert all(np.array_equal(a,b) for a,b in zip(selected['values'],expected['values']))
    assert np.array_equal(selected['operations'],expected['operations'])
    assert selected['kernel_nodes']==expected['kernel_nodes']
    assert len(calls)==1
    assert [m.payload() for m in members]==before and rng.bit_generator.state==state


def test_generation_preparation_failure_reaches_all_consumers():
    import pytest
    members=sample(np.random.default_rng(2020),3)
    source=Future();shared=GenerationPopulation(members)
    pending=shared.pack([1,0],lambda *args:source)
    source.set_exception(ValueError('invalid graph'))
    with pytest.raises(ValueError,match='invalid graph'):pending.result()


def test_loading_yields_before_blocking_cuda_owner(monkeypatch):
    from research.vectorized_backtest.v32.stream_sessions import StreamWorker
    worker=StreamWorker.__new__(StreamWorker)
    worker.config=dict(device='cpu',output='unused')
    worker.emit=lambda *a,**k:None
    future=Future()
    worker.loader=SimpleNamespace(submit=lambda fn,day:future)
    steps=worker.steps('day',None,[],1,'token',1)
    assert next(steps) is future
    assert not future.done()
    steps.close()


def test_global_lane_refills_before_other_lane_finishes_and_barrier_is_complete():
    from research.vectorized_backtest.v32.session_lanes import SessionLanes
    slow=threading.Event();refilled=threading.Event();calls=[];lock=threading.Lock()
    class Proxy:
        def run(self,day,next_day,payload,generation,token,width):
            assert isinstance(payload,GenerationPopulation)
            with lock:calls.append((day,next_day,id(payload)))
            if day=='a':
                slow.set();assert refilled.wait(5)
            if day=='d':assert slow.wait(5);refilled.set()
            return dict(day=day,timings=[dict(day=day)],clocks=100)
    class Panel:
        def __init__(self):self.state={};self.samples=[]
        def update(self,**values):self.state.update(values)
    lanes=SessionLanes.__new__(SessionLanes)
    lanes.config=dict(shared_replay=True,batch_size=512,replay_width_workers=1)
    lanes.workers=2;lanes.widths={d:3 for d in 'abcd'};lanes.error=None;lanes.panel=Panel()
    lanes.proxies=[Proxy(),Proxy()];lanes.pools=[ThreadPoolExecutor(1),ThreadPoolExecutor(1)]
    rng=np.random.default_rng(2020);members=sample(rng,10);state=copy.deepcopy(rng.bit_generator.state)
    try:result=lanes.generation(list('abcd'),members,1,'token')
    finally:
        for pool in lanes.pools:pool.shutdown(wait=True)
    assert [r['day'] for r in result]==list('abcd')
    assert len({c[2] for c in calls})==1
    assert sorted((d,n) for d,n,_ in calls)==[('a','c'),('b','d'),('c',None),('d',None)]
    assert lanes.panel.state['completed_sessions']==4
    assert lanes.panel.state['completed_batches']==4
    assert lanes.panel.state['active_sessions']==lanes.panel.state['queued_sessions']==0
    assert rng.bit_generator.state==state


def test_completed_phase_releases_only_obsolete_buffers(monkeypatch,tmp_path):
    import pytest
    from research.vectorized_backtest.v32 import stream_sessions as module
    events=[];worker=module.StreamWorker.__new__(module.StreamWorker)
    worker.config=dict(output=str(tmp_path),source_sha256='fixture');worker.lane=0
    worker.host={'a':SimpleNamespace(close=lambda:events.append('a')),'b':SimpleNamespace(close=lambda:events.append('b'))}
    worker.prefetch=SimpleNamespace(close=lambda:events.append('prefetch'))
    worker.staging=SimpleNamespace(release_completed=lambda:123)
    worker.market_block=object()
    monkeypatch.setattr(module.torch.cuda,'empty_cache',lambda:events.append('cuda-cache'))
    report=worker.release_phase()
    assert report['host_sessions_released']==2 and report['pinned_staging_bytes_released']==123
    assert not worker.host and worker.market_block is None
    assert events==['prefetch','a','b','cuda-cache']
    worker.config['retain_session_residency']=True
    with pytest.raises(ValueError,match='local session residency'):worker.release_phase()
