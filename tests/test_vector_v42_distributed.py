import json
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v42.genome import Neuron,Network,Individual,Policy,STAGES
from research.vectorized_backtest.v42.distributed_bus import Bus,digest
from research.vectorized_backtest.v42.distributed_preparation import PopulationPreparation

def member():return Individual({s:Network((Neuron((0,),(.5,),.1,12),)) for s in STAGES},Policy(),{})

def test_fresh_scaffolds_align_but_growth_does_not_shift_slots():
    a=Network((Neuron((0,),(.5,),0.,11),Neuron((1,),(.1,),0.,22)))
    b=Network((Neuron((0,),(.5,),0.,33),Neuron((1,),(.1,),0.,44)))
    assert [n.slot for n in a.nodes]==[1,2]==[n.slot for n in b.nodes]
    grown=replace(a,nodes=(a.nodes[0],Neuron((2,),(.2,),0.,55),a.nodes[1]))
    assert [n.slot for n in grown.nodes]==[1,2**64+55,2]

def test_queue_claim_is_exclusive_and_recovery_is_host_affine(tmp_path):
    job=dict(transport=str(tmp_path));a=Bus(job,'workstation');b=Bus(job,'workstation');request=dict(kind='test')
    key=a.submit(request);assert b.submit(request)==key
    assert a.claim(dict(pid=1,creation_time=1))[0]==key;assert b.claim(dict(pid=2,creation_time=2)) is None
    with pytest.raises(RuntimeError,match='still owns'):a.recover_own_claims(lambda owner:True)
    a.recover_own_claims(lambda owner:False)
    a.node='laptop';assert a.claim(dict(pid=3,creation_time=3)) is None
    a.node='workstation';assert a.claim(dict(pid=4,creation_time=4))[0]==key

def test_population_cache_and_packing_are_exact():
    from research.vectorized_backtest.v42.network_pack import pack_networks,select_packed
    m=member();payload=[m.payload(),m.payload()];p=PopulationPreparation()
    try:
        first=p.submit(payload,digest(payload));second=p.submit(payload,digest(payload));assert first is second
        restored,keys,packed,_=first.result();expected=pack_networks([restored[1]])
        actual=select_packed(packed,[1])
        for k in expected:
            if k=='values':
                for x,y in zip(actual[k],expected[k]):np.testing.assert_array_equal(x,y)
            else:np.testing.assert_array_equal(actual[k],expected[k])
        assert (p.hits,p.misses)==(1,1)
    finally:p.close()

def test_global_fresh_sampling_uses_calibrated_random_initializer(monkeypatch):
    from research.vectorized_backtest.v42 import initial_population as ip
    monkeypatch.delenv('V42_INITIAL_POOL',raising=False)
    monkeypatch.setattr(ip,'initialize_random',lambda rng,size,probe:(['fresh']*size,dict(size=size)))
    population,report=ip.initialize(np.random.default_rng(1),256,probe={})
    assert len(population)==256 and report['size']==256


def test_validation_subset_reuses_training_pack_without_rng():
    p=PopulationPreparation();payload=[member().payload(),member().payload()]
    try:
        token=digest(payload);p.submit(payload,token).result();subset=[payload[1]];subtoken=digest(subset)
        assert p.subset(subset,subtoken,token,[1])
        population,keys,packed,_=p.submit(subset,subtoken).result()
        assert len(population)==1 and p.subset_hits==1 and p.misses==1
    finally:p.close()

def test_transport_publication_cannot_escape_owned_root(tmp_path):
    from research.vectorized_backtest.v42.distributed_storage import publish
    with pytest.raises(ValueError,match='escaped'):publish(tmp_path/'owned',tmp_path/'other.json',{})

def test_transport_read_retries_only_transient_access(monkeypatch,tmp_path):
    from pathlib import Path
    from research.vectorized_backtest.v42.distributed_bus import read
    attempts=[]
    def content(self):
        attempts.append(1)
        if len(attempts)<3:raise PermissionError('atomic replace')
        return b'{"complete":true}'
    monkeypatch.setattr(Path,'read_bytes',content)
    assert read(tmp_path/'x.json')=={'complete':True} and len(attempts)==3
    monkeypatch.setattr(Path,'read_bytes',lambda self:b'invalid')
    with pytest.raises(ValueError):read(tmp_path/'x.json')

def test_mirror_verified_replacement_and_failure_preserves_old(tmp_path):
    import hashlib
    from research.vectorized_backtest.v42.distributed_mirror import copy_atomic
    source=tmp_path/'source.json';target=tmp_path/'checkpoint.json'
    source.write_bytes(b'new checkpoint');target.write_bytes(b'old checkpoint')
    with pytest.raises(ValueError,match='hash changed'):copy_atomic(source,target,'0'*64)
    assert target.read_bytes()==b'old checkpoint'
    copy_atomic(source,target,hashlib.sha256(source.read_bytes()).hexdigest())
    assert target.read_bytes()==source.read_bytes() and not list(tmp_path.glob('*.mirror.tmp'))

def test_mirror_persistent_lock_does_not_advance_stamp(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from research.vectorized_backtest.v42 import distributed_mirror as m
    source=tmp_path/'source.json';target=tmp_path/'checkpoint.json'
    source.write_bytes(b'new');target.write_bytes(b'old')
    owner=SimpleNamespace(stamps={})
    attempts=[]
    def locked(*args):attempts.append(1);raise PermissionError('held open')
    monkeypatch.setattr(m.os,'replace',locked);monkeypatch.setattr(m.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):m.snapshot(owner,'laptop',source,target)
    assert len(attempts)==20 and not owner.stamps and target.read_bytes()==b'old'
    assert not list(tmp_path.glob('*.mirror.tmp'))

def test_mirror_real_windows_destination_lock(tmp_path):
    import os,ctypes,threading
    if os.name!='nt':pytest.skip('Windows sharing contract')
    from ctypes import wintypes
    from research.vectorized_backtest.v42.distributed_mirror import copy_atomic
    source=tmp_path/'source.json';target=tmp_path/'checkpoint.json'
    source.write_bytes(b'new');target.write_bytes(b'old')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE;api.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=api.CreateFileW(str(target),0x80000000,1,None,3,0,None)
    assert handle!=wintypes.HANDLE(-1).value
    timer=threading.Timer(.2,lambda:api.CloseHandle(handle));timer.start()
    try:copy_atomic(source,target)
    finally:timer.join()
    assert target.read_bytes()==b'new'

def test_memory_pressure_evicts_inactive_data_before_unchanged_admission(monkeypatch,tmp_path):
    from research.vectorized_backtest.v42.session_lanes import Worker
    import psutil
    from types import SimpleNamespace
    from collections import OrderedDict
    worker=Worker.__new__(Worker);worker.config=dict(output=str(tmp_path),device='cpu',host_reserve_gib=12.,host_admission_gib=8.)
    closed=[];worker.prefetch=SimpleNamespace(close=lambda:closed.append('prefetch'));worker.plans=OrderedDict(one=SimpleNamespace(close=lambda:closed.append('plan')));worker.staging=SimpleNamespace(close=lambda:closed.append('staging'));worker.host=OrderedDict(old=SimpleNamespace(close=lambda:closed.append('data')));worker.resident_results={'authoritative':'cache copy'}
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=19*2**30))
    worker.release_inactive_sessions()
    assert set(closed)=={'prefetch','plan','staging','data'} and not worker.host and not worker.plans
    assert worker.config['host_reserve_gib']==12 and worker.config['host_admission_gib']==8
