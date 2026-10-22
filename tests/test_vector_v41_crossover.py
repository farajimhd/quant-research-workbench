import copy
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v41.genome import Network,Neuron,Individual,INPUTS,MARKET_INPUTS,OPS,STAGES,Policy
from research.vectorized_backtest.v41.composition import compose,cross,exchange
from research.vectorized_backtest.v41.crossover_context import CrossoverContext,bounded_probe
from research.vectorized_backtest.v41.calibration import observe
from research.vectorized_backtest.v41.schedule import Proposal
from research.vectorized_backtest.v41.features import CATALOG


def probe():
    rng=np.random.default_rng(22)
    x=rng.uniform(-.8,.8,(2,12,10,INPUTS)).astype(np.float32)
    return dict(inputs=x,known=np.ones(x.shape[:-1]+(len(CATALOG),),bool),
        held_states=x[...,MARKET_INPUTS:].copy(),eligible=np.ones(x.shape[:-1],bool),metadata=dict(validation_opened=False))


def net(uid=1):
    return Network((Neuron((0,-1),(.7,.2),.1,uid),Neuron((INPUTS,1),(.5,-.3),0.,uid+1)),.2,0,.7,.1).validate()


@pytest.mark.parametrize('op',['tanh','min','max','product','gate','attention'])
def test_composition_all_joins_preserve_size_head_and_delayed_dependencies(op):
    a,b=net(),net(10)
    result=compose(Proposal(bytes(32),(3,)),a,b,True,op)
    assert result.nodes[-1].op==op
    assert (result.size_source,result.size_weight,result.size_bias)==(a.size_source,a.size_weight,a.size_bias)
    assert result.nodes[0].sources==(0,-1)
    assert result.validate() is result


@pytest.mark.parametrize('op',OPS)
def test_cached_module_statistics_match_existing_cpu_arithmetic(op):
    p=bounded_probe(probe());base=net()
    sources=(0,1,2) if op=='attention' else (0,1)
    n=replace(base,nodes=(base.nodes[0],Neuron(sources,(.4,)*len(sources),.1,22,op)))
    # The size head keeps the recurrent first node reachable.
    n.validate();context=CrossoverContext(p)
    actual=context.describe(n,'exit',profile=True)['stats'][-1]
    values,valid,_,_=observe(n,p,'exit');v=values[valid].astype(np.float64)
    np.testing.assert_allclose(actual[:2],[v.mean(),v.std()],rtol=2e-6,atol=2e-7)
    assert actual[2]==len(v)
    context.describe(n,'exit',profile=True)
    assert context.telemetry['profile_builds']==1
    assert p['inputs'].shape[:3]==(1,8,8) and not p['inputs'].flags.writeable


def test_probe_validation_rejected_and_cache_is_bounded():
    p=probe();p['metadata']['validation_opened']=True
    with pytest.raises(ValueError):bounded_probe(p)
    context=CrossoverContext(probe(),capacity=1)
    context.describe(net(),'entry');context.describe(net(22),'entry')
    assert len(context.cache)==1


def test_cross_cached_and_uncached_contexts_identical_rng_and_payload():
    a=Individual({s:net(1+3*i) for i,s in enumerate(STAGES)},Policy(),{}).validate()
    b=Individual({s:net(101+3*i) for i,s in enumerate(STAGES)},replace(Policy(),maximum_adds=7),{}).validate()
    p=probe();cached=CrossoverContext(p);cold=CrossoverContext(p,capacity=1)
    for i,join in enumerate(('tanh','min','max','product','gate','attention')):
        plan=dict(orientation=0,count=1,modes=['compose'],joins=[join],action_start=i)
        x,m=cross(Proposal(bytes(32),(i,),plan),a,b,context=cached)
        y,n=cross(Proposal(bytes(32),(i,),plan),a,b,context=cold)
        assert x.payload()==y.payload() and m==n
        assert m['actions'][0]['join']==join


def test_interface_finite_and_exact_repeated_exchange():
    context=CrossoverContext(probe());a,b=net(),net(22)
    scale,offset=context.interface(a,1,b,1,'entry')
    assert scale==1 and offset==0
    first=Proposal(bytes(32),(2,));second=Proposal(bytes(32),(2,))
    assert exchange(first,a,b,context=context)==exchange(second,a,b,context=CrossoverContext(probe()))


def test_fixed_species_budgets_and_reciprocal_siblings():
    from research.vectorized_backtest.v41.templates import sample_templates
    from research.vectorized_backtest.v41.local_runner import CompactPedigree
    from research.vectorized_backtest.v41.lineage import next_population
    from test_vector_v41_local_resume import metrics
    population=sample_templates()[0][:64];ids=np.arange(64);values=ids.astype(float)+1
    pedigree=CompactPedigree(3)
    pedigree.birth(population,1,[(-1,-1)]*64,['fresh']*64)
    members,newids,report=next_population(np.random.default_rng(42),population,ids,pedigree,list(ids[::-1]),2,
        session_values=values[None],session_eligible=np.ones((1,64),bool),global_values=values,global_eligible=np.ones(64,bool),
        ordered=[metrics(64)],crossover_probe=probe())
    assert len(members)==64 and len(set(newids))==64
    assert sum(report['species_offspring'].values())==45
    assert sum(report['mating_requests'].values())==45
    groups={}
    for i in newids[6:]:
        r=pedigree.records[int(i)]
        if 'pair_group' in r:groups.setdefault(r['pair_group'],[]).append(r)
    assert any(len(g)>1 for g in groups.values())
    for group in groups.values():
        first=group[0]
        for child in group:
            assert sorted(child['parents'])==sorted(first['parents'])
            if child['sibling_index']%2!=first['sibling_index']%2:assert child['parents']==first['parents'][::-1]
