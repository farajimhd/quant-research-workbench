import numpy as np
from collections import Counter
from research.vectorized_backtest.v37.templates import sample_templates,ALLOCATIONS,Graph,INDEX
from research.vectorized_backtest.v37.genome import INPUTS,MARKET_INPUTS
from research.vectorized_backtest.v37.family_stats import annotate,aggregate,seed
from research.vectorized_backtest.v37.local_runner import CompactPedigree
from research.vectorized_backtest.v37.lineage import program_key


def test_exact_template_allocation_and_semantic_uniqueness():
    members,families=sample_templates()
    assert len(members)==3072 and Counter(families)==ALLOCATIONS
    assert len({program_key(m) for m in members})==3072
    assert all(m.validate() is m for m in members)
    for family in ALLOCATIONS:
        assert len({m.networks['entry'].size_bias for m,f in zip(members,families) if f==family})==3


def evaluate(net,values):
    memory=np.zeros(len(net.nodes));outputs=[]
    for row in values:
        current=[]
        for n in net.nodes:
            terms=[w*(memory[-s-1] if s<0 else row[s] if s<INPUTS else current[s-INPUTS]) for s,w in zip(n.sources,n.weights)]
            if n.op=='min':v=np.tanh(min(terms)+n.bias)
            elif n.op=='max':v=np.tanh(max(terms)+n.bias)
            elif n.op=='identity':v=np.clip(sum(terms)+n.bias,-1,1)
            else:raise AssertionError(n.op)
            current.append(v)
        memory=np.asarray(current);outputs.append(current[-1]>net.threshold)
    return outputs


def test_crossing_requires_current_and_previous_validity():
    g=Graph();net=g.finish(g.cross('indicator.rsi.7',.3))
    f=INDEX['indicator.rsi.7'];rows=np.zeros((3,INPUTS));rows[:,f]=np.tanh([.2,.4,.5]);rows[:,2*len(INDEX)+f]=1
    assert evaluate(net,rows)==[False,True,False]
    rows[0,2*len(INDEX)+f]=0
    assert evaluate(net,rows)==[False,False,False]


def test_counts_survive_mutation_and_resume_without_rng():
    members,_=sample_templates();p=CompactPedigree();ids=p.birth(members[:3],1,[(-1,-1)]*3,['fresh']*3)
    seed(p,ids,['random','macd_signal','ema_cross'])
    children=p.birth(members[:2],2,[(0,1),(1,2)],['crossover','crossover_mutation'])
    last=p.birth(members[:1],3,[tuple(children)],['crossover'])[0]
    assert (p.records[int(last)]['template_ancestry'],p.records[int(last)]['random_ancestry'])==(3,1)
    assert '-T75.0-R25.0-' in p.records[int(last)]['label']
    restored=CompactPedigree(records=list(p.records.values()),next_serial=p.next_serial)
    new=restored.birth(members[:1],4,[(int(last),-1)],['mutation'])[0]
    assert restored.records[int(new)]['family_contributions']==p.records[int(last)]['family_contributions']


def test_vectorized_attribution_conserves_profit_and_count():
    records=[]
    for name in ('macd_signal','random'):
        r={'label':'G001-000001-M000-X000-abcd'};annotate(r,[],family=name);records.append(r)
    r={'label':'G001-000002-M000-X000-abcd'};annotate(r,records);records.append(r)
    result=aggregate(records,dict(net_pnl=[10.,-2.,8.],closed_positions=[1,0,2]))
    assert sum(r['candidate_equivalents'] for r in result['rows'])==3
    assert sum(r['total_profit'] for r in result['rows'])==16


def test_random_sampler_preserves_v32_payloads_and_rng():
    from test_vector_v32 import probe_fixture
    from research.vectorized_backtest.v32.initial_population import initialize as reference
    from research.vectorized_backtest.v37.initial_population import initialize_random
    a=np.random.default_rng(2020);b=np.random.default_rng(2020)
    left,_=reference(a,12,probe=probe_fixture());right,_=initialize_random(b,12,probe=probe_fixture())
    assert [m.payload() for m in left]==[dict(m.payload(),version='v32-recurrent-v1') for m in right]
    assert a.bit_generator.state==b.bit_generator.state
