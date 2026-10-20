from concurrent.futures import ThreadPoolExecutor
import queue
from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v32 import stream_sessions as module


class FakeWorker:
    def __init__(self,config,events,lane):self.config=config;self.closed=False;self.current_day="fixture"
    def load(self,day):return day
    def emit(self,*args,**kwargs):pass
    @property
    def prefetch(self):return SimpleNamespace(release_under_pressure=lambda:None)
    def steps(self,day,*args):
        self.current_day=day
        if day=='fail':raise ValueError('fixture failure')
        yield [day+'-batch0',day+'-batch1']
        yield [day+'-tail']
        return dict(day=day)
    def close(self):self.closed=True


@pytest.fixture(autouse=True)
def fake_replay_tasks(monkeypatch):
    class Task:
        def __init__(self,graphs):
            import time
            self.original=graphs
            self.graphs=[SimpleNamespace(block=32,total=32) for _ in graphs]
            self.completed=0;self.started=time.perf_counter();self.enqueue_seconds=0.;self.quanta=0
        @property
        def done(self):return self.completed==1
        def poll(self):return True
        def enqueue(self):
            module.replay_groups([self.original],None);self.completed=1
        def drain(self):pass
    monkeypatch.setattr(module,'ReplayTask',Task)


def test_service_independent_requests_cleanup_and_error(monkeypatch,tmp_path):
    groups=[]
    monkeypatch.setattr(module,'StreamWorker',FakeWorker)
    monkeypatch.setattr(module,'replay_groups',lambda values,progress:groups.append(values))
    config=dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,lane_memory_bytes=100,host_reserve_gib=0)
    service=module.SharedReplayService()
    a=service.factory(config,queue.Queue(),0);b=service.factory(config,queue.Queue(),1)
    try:
        with ThreadPoolExecutor(2) as pool:
            fa=pool.submit(a.run,'day-a');fb=pool.submit(b.run,'day-b')
            assert fa.result()==dict(day='day-a')
            assert fb.result()==dict(day='day-b')
        flattened=[g for row in groups for cohort in row for g in cohort]
        assert sorted(flattened)==sorted([d+s for d in ('day-a','day-b') for s in ('-batch0','-batch1','-tail')])
        assert all(len(row)<=2 and sum(map(len,row))<=4 for row in groups)
        with pytest.raises(ValueError,match='fixture failure'):a.run('fail')
        assert b.load('still usable')=='still usable'
        a.close();b.close()
    finally:service.close()
    assert not service.thread.is_alive()


def test_gpu_failure_propagates_and_does_not_leave_waiters(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'StreamWorker',FakeWorker)
    def fail(*args):raise MemoryError('admission failed')
    monkeypatch.setattr(module,'replay_groups',fail)
    service=module.SharedReplayService()
    worker=service.factory(dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,lane_memory_bytes=100,host_reserve_gib=0),queue.Queue(),0)
    with pytest.raises((MemoryError,RuntimeError)):worker.run('a')
    with pytest.raises(RuntimeError):service.close()
    assert not service.thread.is_alive()


def test_session_queued_during_preparation_joins_same_replay(monkeypatch,tmp_path):
    import threading,time
    started=threading.Event();release=threading.Event();groups=[]
    class Preparing(FakeWorker):
        def steps(self,day,*args):
            if day=='a':
                started.set();assert release.wait(3)
            yield [day+'-0',day+'-1']
            return day
    monkeypatch.setattr(module,'StreamWorker',Preparing)
    monkeypatch.setattr(module,'replay_groups',lambda values,progress:groups.append(values))
    service=module.SharedReplayService()
    cfg=dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,lane_memory_bytes=100,host_reserve_gib=0)
    a=service.factory(cfg,queue.Queue(),0);b=service.factory(cfg,queue.Queue(),1)
    try:
        with ThreadPoolExecutor(2) as pool:
            fa=pool.submit(a.run,'a');assert started.wait(3)
            fb=pool.submit(b.run,'b')
            deadline=time.monotonic()+3
            while service.commands.empty() and time.monotonic()<deadline:time.sleep(.01)
            assert not service.commands.empty();release.set()
            assert fa.result()=='a' and fb.result()=='b'
        assert sorted(g for row in groups for group in row for g in group)==['a-0','a-1','b-0','b-1']
    finally:release.set();service.close()


def test_pending_cpu_job_does_not_block_other_replay(monkeypatch,tmp_path):
    from concurrent.futures import Future
    import threading
    pending=Future();waiting=threading.Event();replayed=threading.Event()
    class AsyncWorker(FakeWorker):
        def steps(self,day,*args):
            if day=='cpu':
                waiting.set()
                value=yield pending
                return value
            yield [day]
            return day
    monkeypatch.setattr(module,'StreamWorker',AsyncWorker)
    monkeypatch.setattr(module,'replay_groups',lambda *args:replayed.set())
    service=module.SharedReplayService()
    cfg=dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,host_reserve_gib=0)
    a=service.factory(cfg,queue.Queue(),0);b=service.factory(cfg,queue.Queue(),1)
    try:
        with ThreadPoolExecutor(2) as pool:
            fa=pool.submit(a.run,'cpu');assert waiting.wait(3)
            fb=pool.submit(b.run,'gpu');assert replayed.wait(3)
            assert not fa.done() and fb.result(timeout=3)=='gpu'
            pending.set_exception(OSError('publication failed'))
            with pytest.raises(OSError,match='publication failed'):fa.result(timeout=3)
        a.close();b.close()
    finally:
        if not pending.done():pending.set_result(None)
        service.close()


@pytest.mark.parametrize('late',['cpu','gpu'])
def test_session_finishes_while_other_replay_is_incomplete(monkeypatch,tmp_path,late):
    import threading,time
    from concurrent.futures import Future
    started=threading.Event();release=threading.Event()
    class Incremental:
        def __init__(self,graphs):
            self.slow=graphs==['slow'];self.graphs=[SimpleNamespace(block=32,total=32000)]
            self.completed=0;self.started=time.perf_counter();self.enqueue_seconds=0.;self.quanta=0
        @property
        def done(self):return release.is_set() if self.slow else self.completed>0
        def poll(self):time.sleep(.001);return True
        def enqueue(self):self.completed+=1;started.set()
        def drain(self):pass
    class Worker(FakeWorker):
        def steps(self,day,*args):
            if day=='cpu':
                f=Future();f.set_result('cpu result');assert (yield f)=='cpu result'
            else:yield [day]
            return day
    monkeypatch.setattr(module,'StreamWorker',Worker);monkeypatch.setattr(module,'ReplayTask',Incremental)
    service=module.SharedReplayService()
    cfg=dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,host_reserve_gib=0)
    a=service.factory(cfg,queue.Queue(),0);b=service.factory(cfg,queue.Queue(),1)
    try:
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(a.run,'slow');assert started.wait(3)
            second=pool.submit(b.run,late)
            try:
                assert second.result(timeout=3)==late
                assert not first.done()
            finally:release.set()
            assert first.result(timeout=3)=='slow'
        a.close();b.close()
    finally:release.set();service.close()


def test_progress_is_published_while_other_stream_is_inflight(monkeypatch,tmp_path):
    import threading,time
    from concurrent.futures import Future
    release=threading.Event();progress=threading.Event()
    class Incremental:
        def __init__(self,graphs):
            self.slow=graphs==['slow'];self.graphs=[SimpleNamespace(block=32,total=2048)]
            self.completed=0;self.started=time.perf_counter();self.enqueue_seconds=0.;self.quanta=0;self.queued=False
        @property
        def done(self):return self.completed==64
        def poll(self):
            if self.queued and (not self.slow or release.is_set()):self.completed=64
            return not self.queued or self.done
        def enqueue(self):self.queued=True
        def drain(self):release.set()
    class Worker(FakeWorker):
        def steps(self,day,*args):
            self.current_day=day
            yield [day]
            return day
        def emit(self,day,**values):
            if day=='fast' and values.get('completed_seconds')==2048:
                assert not release.is_set()
                progress.set();release.set()
    monkeypatch.setattr(module,'StreamWorker',Worker);monkeypatch.setattr(module,'ReplayTask',Incremental)
    service=module.SharedReplayService()
    cfg=dict(output=str(tmp_path),device='cuda',backend='compile',batch_size=512,host_reserve_gib=0)
    a=service.factory(cfg,queue.Queue(),0);b=service.factory(cfg,queue.Queue(),1)
    # Queue both before the service begins their replay.
    try:
        fa=Future();fb=Future()
        service.commands.put((a.key,'run',('slow',),fa))
        service.commands.put((b.key,'run',('fast',),fb))
        assert progress.wait(3)
        assert fa.result(timeout=3)=='slow' and fb.result(timeout=3)=='fast'
    finally:release.set();service.close()
