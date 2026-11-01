import numpy as np
from dataclasses import replace
from research.vectorized_backtest.v62.genome import sample,STAGES
from research.vectorized_backtest.v62.schedule import Proposal
from research.vectorized_backtest.v62.growth import extend,subtree


def test_small_sampling_and_local_growth():
    members=sample(np.random.default_rng(29),36,stratified=True,structural=True)
    assert {len(n.nodes) for m in members for n in m.networks.values()}=={1,2}
    for m in members:
        for kind,delta in [('insert',1),('condition',3),('alternative',3)]:
            n=m.networks['entry'];g=extend(n,'entry',Proposal(b'x'*32,(kind,)),kind)
            assert len(g.nodes)==len(n.nodes)+delta
            assert g.size_source==(n.size_source if n.size_source>=0 else len(n.nodes)-1)
            replace(m,networks=dict(m.networks,entry=g)).validate()


def test_identity_insertion_preserves_decisions_and_sizing():
    import torch
    from test_vector_v62_market_pipeline import market_fixture
    from research.vectorized_backtest.v62.network import NetworkPlan
    m=sample(np.random.default_rng(17),1)[0]
    child=replace(m,networks={s:extend(n,s,Proposal(b'y'*32,(s,)),'insert') for s,n in m.networks.items()})
    plan=NetworkPlan([m,child],'cpu');data=market_fixture('cpu',9,17)
    p=torch.zeros((1,2,17,9),dtype=torch.float64)
    try:
        for i in range(9):
            sig=plan.signals(data,torch.tensor(i),p,p[...,0],p[...,0])
            torch.testing.assert_close(sig[:,0],sig[:,1],rtol=0,atol=0)
            torch.testing.assert_close(plan.ratios[:,0],plan.ratios[:,1],rtol=0,atol=0)
    finally:plan.close()


def test_subtree_exchange_keeps_donor_dependencies():
    members=sample(np.random.default_rng(44),8)
    for a,b in zip(members,members[1:]):
        for stage in STAGES:
            an=a.networks[stage];bn=b.networks[stage]
            result=subtree(an,bn,len(an.nodes)-1,len(bn.nodes)-1)
            result.validate()
            assert result.nodes[-1].op==bn.nodes[-1].op


def test_objective_unchanged():
    from research.vectorized_backtest.v60.robust_score import reward as old
    from research.vectorized_backtest.v62.robust_score import reward as new
    x=np.array([-10000.,-1,0,10,2000,5000,50000])
    np.testing.assert_array_equal(old(x,2000),new(x,2000))


def test_new_founder_families_have_ancestry_slots():
    from research.vectorized_backtest.v62.family_stats import annotate
    from research.vectorized_backtest.v62.templates import FAMILIES
    for family in ('simple','combination'):
        row={'label':'G001-000001-fresh'};annotate(row,[],family=family)
        assert row['template_ancestry']==0 and row['random_ancestry']==1
        assert family in FAMILIES
