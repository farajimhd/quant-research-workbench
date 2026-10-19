from concurrent.futures import ThreadPoolExecutor
import queue
import pytest
from research.vectorized_backtest.v32 import stream_sessions as module


class FakeWorker:
    def __init__(self,config,events,lane):self.config=config;self.closed=False
    def load(self,day):return day
    def steps(self,day,*args):
        self.current_day=day
        if day=='fail':raise ValueError('fixture failure')
        yield [day+'-batch0',day+'-batch1']
        yield [day+'-tail']
        return dict(day=day)
    def close(self):self.closed=True


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
