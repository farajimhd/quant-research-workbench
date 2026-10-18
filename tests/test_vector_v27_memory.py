"""Boundary cancellation and exact-input replay recovery under memory pressure."""
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_boundary_stop_waits_without_lowering_ram_admission(tmp_path):
    from research.vectorized_backtest.v27.host_memory import wait_for_headroom
    root=tmp_path/'campaign';global_root=root/'global';output=global_root/'training'
    output.mkdir(parents=True);(global_root/'STOP').touch()
    values=iter([159,159,160]);events=[];sleeps=[]
    wait_for_headroom(output,lambda **v:events.append(v),boundary_stop=global_root,
                      snapshot=lambda:SimpleNamespace(available=next(values)*1024**3),sleep=sleeps.append)
    assert sleeps==[2.] and events[0]['required_available_gib']==160
    (root/'STOP').touch()
    with pytest.raises(InterruptedError):
        wait_for_headroom(output,lambda **v:None,boundary_stop=global_root,
                          snapshot=lambda:SimpleNamespace(available=0),sleep=lambda _:None)


def test_capture_recovery_retries_identical_inputs_once(monkeypatch,tmp_path):
    from research.vectorized_backtest.v27 import session_lanes as lanes
    from research.vectorized_backtest.v6.torch_backtest import runtime
    calls=[];closed=[];cleared=[];records=[];gates=object();answer=object()
    worker=lanes.Worker.__new__(lanes.Worker)
    worker.plans={1:SimpleNamespace(close=lambda:closed.append(True))};worker.config={'output':str(tmp_path)}
    data=[SimpleNamespace(device=SimpleNamespace(type='cuda'))]
    def replay(*args,**kwargs):
        calls.append((args,kwargs))
        if len(calls)==1:raise MemoryError('Packed V7 cohort exceeds GPU headroom')
        return answer
    monkeypatch.setattr(lanes,'replay_cohort',replay)
    monkeypatch.setattr(lanes.torch.cuda,'mem_get_info',lambda _: (100,1000))
    monkeypatch.setattr(lanes.torch.cuda,'memory_reserved',lambda _: 200)
    monkeypatch.setattr(lanes.torch.cuda,'empty_cache',lambda:cleared.append(True))
    monkeypatch.setattr(runtime,'write_json',lambda path,value:records.append(value))
    assert worker.replay(data,[],[gates],trace=True) is answer
    assert calls[0]==calls[1] and calls[1][0][2][0] is gates
    assert len(closed)==len(cleared)==len(records)==1
    def exhausted(*a,**k):raise MemoryError('Packed V7 cohort exceeds GPU headroom')
    monkeypatch.setattr(lanes,'replay_cohort',exhausted)
    with pytest.raises(MemoryError):worker.replay(data,[],[gates])
    def other(*a,**k):raise MemoryError('different failure')
    monkeypatch.setattr(lanes,'replay_cohort',other)
    with pytest.raises(MemoryError,match='different failure'):worker.replay(data,[],[gates])
    assert len(closed)==2
