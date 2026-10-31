import os
from copy import copy
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from test_vector_v60_market_pipeline import market_fixture

@pytest.mark.parametrize('device',['cpu','cuda'])
def test_compact_projection_equals_permitted_legacy_channels(device):
    if device=='cuda' and os.environ.get('V60_TEST_CUDA')!='1':pytest.skip('opt-in GPU')
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.features import CATALOG
    from research.vectorized_backtest.v60.authority import BLOCKED
    plan=NetworkPlan(sample(np.random.default_rng(2),1),device)
    old=copy(plan);old.columns=list(range(len(CATALOG)));old.lookup={i:i for i in old.columns}
    old.logarithmic=torch.tensor([f.unit not in ('ratio','bool') for f in CATALOG],device=device)
    old.quote_columns=torch.tensor([plan.columns[i] for i in plan.quote_columns.cpu().tolist()],device=device)
    item=market_fixture(device,listings=137);i=torch.tensor(3,device=device)
    original,known=old.project(item,i);actual,valid=plan.project(item,i)
    selected=torch.tensor([c+k*len(CATALOG) for k in range(3) for c in plan.columns],device=device)
    torch.testing.assert_close(actual,original.index_select(-1,selected),rtol=0,atol=0)
    torch.testing.assert_close(valid,known[:,plan.columns],rtol=0,atol=0)
    assert actual.shape[-1]==2502 and not set(plan.columns)&set(BLOCKED)
    plan.close()

@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
def test_crossing_uses_compact_causal_lag_and_matches_scalar():
    from research.vectorized_backtest.v60.genome import Individual,Network,Neuron,STAGES
    from research.vectorized_backtest.v60.authority import ALLOWED
    from research.vectorized_backtest.v60.policy import Policy
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.market_inference import Inference
    m=Individual({s:Network((Neuron((ALLOWED[0],),(1.,),0.,op='cross'),)) for s in STAGES},Policy(),{}).validate()
    plan=NetworkPlan([m],'cuda');plan.prepare(1,137);item=market_fixture('cuda',9,137)
    engine=Inference(plan,item,137)
    assert len(engine.lag_projection.columns)==1
    p=torch.zeros((1,1,137,9),device='cuda',dtype=torch.float64)
    for start in (0,4):
        signals,ratios,*_=engine(torch.arange(start,start+4,device='cuda'))
        for offset in range(4):
            expected=plan.signals(item,torch.tensor(start+offset,device='cuda'),p,p[...,0],p[...,0])
            torch.testing.assert_close(signals[offset],expected[0],rtol=0,atol=0)
            torch.testing.assert_close(ratios[offset],plan.ratios[0],rtol=0,atol=0)
    plan.close()

def test_balanced_batches_keep_every_identity_and_memory_bound():
    from research.vectorized_backtest.v60.replay_batches import plan_batches,work
    def candidate(n):return SimpleNamespace(routing=None,networks={'entry':SimpleNamespace(nodes=[SimpleNamespace(sources=(0,1,2))]*n)})
    members=[candidate(n) for n in [1]*12+[100]*12]
    result=plan_batches(members,list(range(24)),549,4,8)
    assert sorted(i for row in result for i in row)==list(range(24))
    assert list(map(len,result))==[8]*3
    assert len({sum(work(members[i]) for i in row) for row in result})==1
    assert result==plan_batches(members,list(range(24)),549,4,8)
    tiny=plan_batches(members,list(range(24)),549,4,8,workspace_bytes=400000)
    assert sorted(i for row in tiny for i in row)==list(range(24))
    assert all(sum(len(members[i].networks['entry'].nodes) for i in row)*549*5+len(row)*549*44<=400000 for row in tiny)

def test_shared_whole_branch_preserves_outputs_and_exact_budget(monkeypatch):
    from dataclasses import replace
    from research.vectorized_backtest.v60 import whole_network as whole
    from research.vectorized_backtest.v60.genome import sample,Individual,STAGES
    from research.vectorized_backtest.v60.network import NetworkPlan
    a=sample(np.random.default_rng(5),1)[0];n=a.networks['entry']
    other=replace(n,threshold=.2,size_weight=.3,size_bias=-.1)
    for mode in whole.POLICY['modes']:
        compact=whole.network(n,other,mode)
        with monkeypatch.context() as patch:
            patch.setattr(whole,'shared_branch_nodes',lambda a,b:False)
            original=whole.network(n,other,mode)
        assert len(compact.nodes)==len(n.nodes)+6
        assert len(original.nodes)==2*len(n.nodes)+6
        members=[Individual({s:net for s in STAGES},a.policy,{}).validate() for net in (original,compact)]
        plan=NetworkPlan(members,'cpu');item=market_fixture('cpu',5,13)
        p=torch.zeros((1,2,13,9),dtype=torch.float64)
        for i in range(5):
            sig=plan.signals(item,torch.tensor(i),p,p[...,0],p[...,0])
            torch.testing.assert_close(sig[:,0],sig[:,1],rtol=0,atol=0)
            torch.testing.assert_close(plan.ratios[:,0],plan.ratios[:,1],rtol=0,atol=0)
        plan.close()
