import copy
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v41.schedule import balanced,Proposal,generation_root
from test_vector_v41_crossover import probe


def test_exact_interleaved_quotas_and_rotation():
    for n in (1,7,36,484,717,922):
        for weights in ([.525,.475],[.8,.2],[.4,.3,.3],[.2]*5):
            a=balanced(n,weights,9);counts=np.bincount(a,minlength=len(weights))
            assert len(a)==n and np.max(np.abs(counts-n*np.array(weights)))<=1
            assert np.array_equal(a,balanced(n,weights,9))
    assert np.sum(balanced(922,[.525,.475])==0)==484


def test_ids_and_other_offspring_do_not_consume_proposal_randomness():
    root=generation_root(np.random.default_rng(2020));a=Proposal(root,(2,3));b=Proposal(root,(2,3))
    ids=[a.uid() for _ in range(100)]
    assert len(set(ids))==100
    assert a.uniform()==b.uniform()
    unused=Proposal(root,(2,4));unused.uniform(size=10000)
    assert Proposal(root,(2,5)).normal()==Proposal(root,(2,5)).normal()
    assert Proposal(root,(2,3,'attempt',1)).uniform()!=Proposal(root,(2,3,'attempt',2)).uniform()


def fixture():
    from research.vectorized_backtest.v41.templates import sample_templates
    from research.vectorized_backtest.v41.local_runner import CompactPedigree
    members=sample_templates()[0][:40];p=CompactPedigree(3)
    ids=p.birth(members,1,[(-1,-1)]*len(members),['fresh']*len(members))
    m=dict(net_pnl=list(map(float,range(1,41))),closed_positions=[1.]*40,
        capital_dollar_seconds=[1000.]*40,turnover=[1000.]*40,
        episode_listings=[[0]]*40,episode_profits=[[1.]]*40,
        episode_entry_clocks=[[1]]*40,episode_exit_clocks=[[2]]*40)
    return members,p,ids,m


def run_generation(members,p,ids,m):
    from research.vectorized_backtest.v41.lineage import next_population
    values=np.arange(1,len(members)+1,dtype=float);ok=np.ones(len(members),bool)
    rng=np.random.default_rng(2020);before=copy.deepcopy(rng.bit_generator.state)
    out,newids,report=next_population(rng,members,ids,p,list(range(len(members)-1,-1,-1)),2,
        session_values=values[None],session_eligible=ok[None],global_values=values,
        global_eligible=ok,ordered=[m],crossover_probe=probe())
    assert rng.bit_generator.state==before
    return [v.payload() for v in out],newids.tolist(),report,p.records


def test_real_reproduction_deterministic_and_draw_isolated(monkeypatch):
    from research.vectorized_backtest.v41 import lineage
    members,p,ids,m=fixture();baseline=run_generation(members,copy.deepcopy(p),ids,m)
    original=lineage.sample
    def noisy(rng,*args,**kwargs):
        result=original(rng,*args,**kwargs);rng.uniform(size=500);return result
    monkeypatch.setattr(lineage,'sample',noisy)
    again=run_generation(members,copy.deepcopy(p),ids,m)
    assert baseline[0]==again[0] and baseline[1]==again[1] and baseline[3]==again[3]
    report=again[2]
    assert report['mutations']==19 and report['mutation_rate']==.525
    assert sum(report['mutation_edit_counts'].values())==19
    assert all(report['mutation_edit_counts'][str(i)]>0 for i in range(1,6))
    assert sum(report['mating_requests'].values())==28
    assert all(v>0 for v in report['requested_action_modes'].values())


def test_archive_selection_does_not_depend_on_random_seed():
    from research.vectorized_backtest.v41.archive import select_archive
    rows=[dict(canonical_sha256=str(i),net_profit=float(i+1),serial=i) for i in range(200)]
    a=select_archive(rows,seed=2020);b=select_archive(rows,seed=2021)
    assert a==b and len(a)==64 and max(r['net_profit'] for r in a)==200


def test_financial_and_certified_sources_match_v34():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','objectives.py','network.py','network_cuda.py','session_lanes.py','species.py'):
        assert (root/'v41'/name).read_text()==(root/'v34'/name).read_text().replace('v34','v41').replace('V34','V41')
    for name in ('features.py','indicator_math.py','enriched_math.py','indicator_identity.py','certified_history_bank.py'):
        assert (root/'v41'/name).read_bytes()==(root/'v34'/name).read_bytes()
