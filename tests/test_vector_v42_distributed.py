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
