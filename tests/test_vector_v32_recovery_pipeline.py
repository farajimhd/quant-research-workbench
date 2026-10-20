from concurrent.futures import Future,ThreadPoolExecutor
from types import SimpleNamespace
import json
from pathlib import Path
import pytest

from research.vectorized_backtest.v32 import preparation
from research.vectorized_backtest.v32.prefetch import CPUOnlyPrefetch
from research.vectorized_backtest.v32.result_processing import timed_call,process_metrics


def test_completion_reserves_memory_without_large_load_admission(monkeypatch,tmp_path):
    monkeypatch.setenv('V32_CPU_RESERVE_GIB','128')
    monkeypatch.setattr(preparation,'_headroom',lambda:(138*2**30,160*2**30))
    pending=[]
    def submit(fn,args,**kw):
        f=Future();pending.append(f);return f
    monkeypatch.setattr(preparation,'_submit',submit)
    a=preparation.submit_completion(timed_call,process_metrics,{},[],[],output=tmp_path,emit=lambda **v:None)
    b=preparation.submit_completion(timed_call,process_metrics,{},[],[],output=tmp_path,emit=lambda **v:None)
    assert len(pending)==1 and not b.done()
    pending[0].set_result('first');assert a.result()=='first'
    b.next_poll=0;assert not b.done() and len(pending)==2
    pending[1].set_result('second');assert b.result()=='second'
    assert preparation._completion_reserved==0
    normal=preparation.submit_when_ready(list,[],output=tmp_path,emit=lambda **v:None)
    assert not normal.done();normal.cancel()


def test_completion_cannot_bypass_bars_reserve(monkeypatch,tmp_path):
    monkeypatch.setenv('V32_CPU_RESERVE_GIB','128')
    monkeypatch.setattr(preparation,'_headroom',lambda:(127*2**30,160*2**30))
    f=preparation.submit_completion(timed_call,process_metrics,{},[],[],output=tmp_path,emit=lambda **v:None)
    assert not f.done();assert f.cancel()
    with pytest.raises(ValueError,match='restricted'):
        preparation.submit_completion(list,[],output=tmp_path,emit=lambda **v:None)


def test_large_completion_reservation_grows_without_truncating_results():
    import numpy as np
    value=np.broadcast_to(np.asarray(0.),(20_000_000,))
    assert preparation.completion_budget({'episodes':value})>8*2**30
    assert preparation.completion_budget({'episodes':[[],[1.,2.]]})==8*2**30


def test_host_load_and_completion_share_unallocated_budget(monkeypatch,tmp_path):
    monkeypatch.setenv('V32_CPU_RESERVE_GIB','128')
    monkeypatch.setattr(preparation,'_headroom',lambda:(160*2**30,160*2**30))
    budget,required=preparation.reserve_host_load(160*2**30,128,32)
    assert budget==32*2**30
    f=preparation.submit_completion(timed_call,process_metrics,{},[],[],output=tmp_path,emit=lambda **v:None)
    assert not f.done();f.cancel();preparation.release_host_load(budget)
    assert preparation._host_reserved==0


def test_prefetch_requires_budget_before_loading_and_retains_cpu_only():
    available=[159*2**30];calls=[];events=[]
    with ThreadPoolExecutor(max_workers=1) as pool:
        p=CPUOnlyPrefetch(pool,reserve_gib=128,admission_gib=32,
            snapshot=lambda:SimpleNamespace(available=available[0]),report=events.append)
        def load(day):
            calls.append(day);available[0]-=20*2**30
            return SimpleNamespace(device=SimpleNamespace(type='cpu'),close=lambda:None)
        p.start('a',load);assert p.take('a') is None and calls==[]
        available[0]=200*2**30;p.start('b',load)
        assert p.take('b') is not None and calls==['b']
        assert [e['outcome'] for e in events]==['deferred','ready']


def test_actual_panel_retries_atomic_status_replacement(monkeypatch,tmp_path):
    from rich.console import Console
    from research.vectorized_backtest.v32 import terminal,publication
    monkeypatch.setattr(publication,'require_runtime',lambda p:None)
    monkeypatch.setattr(publication,'sleep',lambda seconds:None)
    original=Path.replace;calls=[]
    def replace(path,target):
        calls.append(str(target))
        if len(calls)<3:
            error=PermissionError('temporary Windows reader');error.winerror=5;raise error
        return original(path,target)
    monkeypatch.setattr(Path,'replace',replace)
    contract=dict(population=1024,generations=50,input_receipts={},batch_size=512,session_workers=2,execution={})
    panel=terminal.CampaignProgress(tmp_path,contract,console=Console(force_terminal=False))
    panel.publish()
    assert len(calls)==3 and json.loads((tmp_path/'status.json').read_bytes())['status']=='running'
    assert not list(tmp_path.glob('*.tmp'))
