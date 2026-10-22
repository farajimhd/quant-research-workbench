"""Mask topology semantics, typed numerics, recurrence and exact sparse ABI."""
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v42.genome import Neuron,Network,INPUTS
from research.vectorized_backtest.v42.mask_genome import combine_masks,numbers,network
from research.vectorized_backtest.v42.schedule import Proposal

@pytest.mark.parametrize('mode,expected',[('union',[1,1,1,0]),('intersection',[1,0,0,0]),('xor',[0,1,1,0])])
def test_boolean_masks(mode,expected):
    assert combine_masks([1,1,0,0],[1,0,1,0],mode,np.random.default_rng(1)).tolist()==expected

def test_uniform_only_inherits_each_bit():
    a=np.array([1,0,1,0]*20);b=1-a
    z=combine_masks(a,b,'uniform',np.random.default_rng(1));assert 0<z.sum()<len(z)

def test_numeric_bounds_and_determinism():
    a=numbers(np.random.default_rng(3),np.zeros(1000),np.ones(1000))
    np.testing.assert_array_equal(a,numbers(np.random.default_rng(3),np.zeros(1000),np.ones(1000)))
    assert ((a>=0)&(a<=1)).all() and ((a>0)&(a<1)).any() and ((a==0)|(a==1)).any()

def fixture():
    return Network((Neuron((0,1),(0.2,0.4),0.,11,'tanh',1),Neuron((INPUTS,2),(0.5,0.3),0.,12,'tanh',2)),0.,1).validate()

def test_union_aligns_sources_not_array_positions():
    a=fixture();b=replace(a,nodes=(replace(a.nodes[0],sources=(1,3),weights=(.6,.7)),a.nodes[1]))
    c=network(Proposal(bytes(32),(1,)),a,b,'union')
    first=next(n for n in c.nodes if n.slot==1)
    assert set(first.sources)=={0,1,3};assert first.weights[first.sources.index(0)]==.2
    assert first.weights[first.sources.index(3)]==.7
    assert {n.slot for n in c.nodes}=={1,2}

def test_recurrent_slots_survive_reorder_and_checkpoint():
    a=fixture();a=replace(a,nodes=(replace(a.nodes[0],sources=(0,-1)),a.nodes[1]))
    c=network(Proposal(bytes(32)),a,a,'union');assert c==Network.restore(c.payload())
    assert c.nodes[0].sources==(0,-1)

def test_empty_mask_fails_explicitly():
    with pytest.raises(ValueError,match='all node inputs'):network(Proposal(bytes(32)),fixture(),fixture(),'xor')

def test_slot_metadata_does_not_change_packed_execution():
    from research.vectorized_backtest.v42.genome import Individual,STAGES,Policy
    from research.vectorized_backtest.v42.network_pack import pack_networks
    from research.vectorized_backtest.v34.genome import Individual as Old
    from research.vectorized_backtest.v34.network_pack import pack_networks as old_pack
    a=Individual({s:fixture() for s in STAGES},Policy(),{s:0 for s in STAGES if s!='entry'}).validate()
    b=Old.restore(dict(a.payload(),version='v34-recurrent-v1'))
    def compare(x,y):
        if isinstance(x,dict):
            assert x.keys()==y.keys()
            for k in x:compare(x[k],y[k])
        elif isinstance(x,(tuple,list)):
            assert len(x)==len(y)
            for u,v in zip(x,y):compare(u,v)
        else:np.testing.assert_array_equal(x,y)
    compare(pack_networks([a]),old_pack([b]))

def test_scheduled_mask_mode_is_executed_and_missing_age_defaults_zero():
    from research.vectorized_backtest.v42.genome import Individual,STAGES,Policy
    from research.vectorized_backtest.v42.mask_genome import cross
    a=Individual({s:fixture() for s in STAGES},Policy(),{}).validate()
    p=Proposal(bytes(32),(1,),dict(count=5,orientation=0,modes=['union']*5))
    child,details=cross(p,a,a)
    assert len(details['actions'])==5
    assert all(e['mode']=='mask:union' for e in details['actions'])
    assert all(v==0 for v in child.minimum_age.values())
    child.validate()
