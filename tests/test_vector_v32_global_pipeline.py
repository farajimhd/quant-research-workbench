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
    assert [m.payload() for m in members]==before and rng.bit_generator.state==state


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
