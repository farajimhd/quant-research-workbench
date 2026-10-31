import os
import numpy as np
import pytest
import torch
from types import SimpleNamespace


def market_fixture(device='cpu',ticks=17,listings=7):
    from research.vectorized_backtest.v60.features import CATALOG
    torch.manual_seed(37);c=len(CATALOG)
    raw=torch.rand((ticks,listings,c),device=device);known=torch.rand_like(raw)>.1
    source=torch.arange(ticks,device=device)[:,None].expand(-1,listings)
    selections={}
    def fetch(rows,ids,clock,columns):
        key=tuple(columns)
        if key not in selections:selections[key]=torch.tensor(columns,device=device,dtype=torch.long)
        select=selections[key]
        return raw.index_select(0,clock[0]).index_select(-1,select).transpose(0,1),known.index_select(0,clock[0]).index_select(-1,select).transpose(0,1)
    bank=SimpleNamespace(tensors={'source':source,'ids':source,'clocks':torch.arange(ticks,device=device)},_values=fetch)
    mark=torch.full((ticks,listings),10.,device=device,dtype=torch.float64)
    item=SimpleNamespace(device=torch.device(device),resident_features=bank,
        tensors={'mark':mark,'bid':mark-.01,'ask':mark+.01,'quote_valid':torch.ones_like(mark,dtype=torch.bool),
                 'membership':torch.ones_like(mark,dtype=torch.bool)})
    return item


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_batched_market_projection_does_not_mix_candles(device):
    if device=='cuda' and os.environ.get('V60_TEST_CUDA')!='1':pytest.skip('opt-in GPU')
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.market_inference import project
    plan=NetworkPlan(sample(np.random.default_rng(19),1),device);item=market_fixture(device,listings=137)
    indices=torch.tensor([0,2,4,8],device=device)
    expected=[plan.project(item,i) for i in indices]
    values,valid=project(plan,item,indices)
    torch.testing.assert_close(valid,torch.stack([r[1] for r in expected]),rtol=0,atol=0)
    torch.testing.assert_close(values,torch.stack([r[0] for r in expected]),rtol=0,atol=0)
    absolute,_=project(plan,item,indices,True)
    expected_absolute=torch.stack([plan.project_absolute(item,i)[0] for i in indices])
    torch.testing.assert_close(absolute,expected_absolute,rtol=0,atol=0)
    plan.close()


def test_constructors_forbid_all_position_inputs_and_ages():
    from dataclasses import replace
    from research.vectorized_backtest.v60.genome import sample,MARKET_INPUTS,INPUTS
    from research.vectorized_backtest.v60.authority import legal_sources,dependency_certificate
    from research.vectorized_backtest.v60.templates import sample_templates
    members=sample(np.random.default_rng(5),16)+sample_templates()[0]
    for m in members:
        dependency_certificate(m)
        assert not any(m.minimum_age.values())
        assert all(not MARKET_INPUTS<=s<INPUTS for net in m.networks.values() for n in net.nodes for s in n.sources)
    for entry in (True,False):
        sources=legal_sources(10,entry=entry)
        assert not ((sources>=MARKET_INPUTS)&(sources<INPUTS)).any()
    with pytest.raises(ValueError):replace(members[0],minimum_age={'exit':1}).validate()


def test_mutation_schedule_only_constructs_market_only_children():
    from research.vectorized_backtest.v60.schedule import SETTINGS,Proposal,balanced
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.composition import mutation
    from research.vectorized_backtest.v60.authority import dependency_certificate
    fields=SETTINGS['parameter_fields']
    assert len(fields)==len(SETTINGS['parameter_weights'])
    assert 'age' not in fields
    assert set(balanced(200,SETTINGS['parameter_weights']))==set(range(len(fields)))
    parent=sample(np.random.default_rng(31),1)[0]
    # Exercise both the standalone fallback and explicitly scheduled fields.
    for slot in range(40):
        plan={} if slot<20 else {'edits':[dict(stage=slot%5,mode='parameter',field=fields[slot%len(fields)])]}
        child,edits=mutation(Proposal(b'market-only-test',(slot,),plan),parent,1)
        dependency_certificate(child)
        assert edits and not any(child.minimum_age.values())


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
def test_time_batch_matches_sequential_gpu_all_heads_and_sizes():
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.features import CATALOG
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.network_cuda import market_batch
    members=sample(np.random.default_rng(93),5);members+=members[:2]
    from research.vectorized_backtest.v60.feature_layout import WIDTH
    b=len(members);t,u,c=4,137,WIDTH
    plan=NetworkPlan(members,'cuda');plan.prepare(b,u)
    torch.manual_seed(117)
    values=torch.rand((t,u,3*c),device='cuda')*.8-.4
    valid=torch.rand((t,u,c),device='cuda')>.15;values[...,2*c:]=valid.float()
    entry_indices=torch.tensor([[1,65,136],[0,64,130],[2,66,129],[3,67,128]],device='cuda',dtype=torch.int32)
    membership=torch.zeros((t,u),device='cuda',dtype=torch.bool)
    membership.scatter_(1,entry_indices.long(),True)
    data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(t,device='cuda')}),tensors={'membership':membership})
    p=torch.zeros((1,b,u,9),device='cuda',dtype=torch.float64);opened=p[...,0].clone()
    plan.project=lambda data,index:(values.index_select(0,index.reshape(1))[0],valid.index_select(0,index.reshape(1))[0])
    expected=[];sizes=[]
    for i in range(t):
        expected.append(plan.signals(data,torch.tensor(i,device='cuda'),p,opened,p[:,:1,:,0]).clone()[0])
        sizes.append(plan.ratios.clone()[0])
    work=torch.empty((t,plan.total_nodes,u),device='cuda');known=torch.empty_like(work,dtype=torch.bool)
    actual,ratios=market_batch(plan,values,valid,membership,work,known,entry_indices)
    torch.testing.assert_close(actual,torch.stack(expected),rtol=0,atol=0)
    torch.testing.assert_close(ratios,torch.stack(sizes),rtol=0,atol=0)
    plan.close()


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
def test_routed_batch_ignores_execution_and_matches_current_market_arbitration():
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.strategy_mix import route
    from research.vectorized_backtest.v60.routing_plan import RoutingPlan
    from research.vectorized_backtest.v60.market_inference import Inference
    parents=sample(np.random.default_rng(13),2);members=[route(*parents)]
    item=market_fixture('cuda',5,7)
    item.tensors.update(v60_entry_swing_ids=torch.zeros((5,7),device='cuda',dtype=torch.long),
        v60_entry_swing_values=torch.full((1,121),8.,device='cuda'))
    plan=RoutingPlan(members,'cuda');plan.prepare(1,7);plan.leaf_columns=torch.zeros(2,device='cuda',dtype=torch.long)
    policy=torch.empty((1,1,7,11),device='cuda',dtype=torch.float64)
    columns=torch.zeros((1,7),device='cuda',dtype=torch.long)
    plan.bind(policy,torch.zeros((1,1,7,5),device='cuda'),columns)
    engine=Inference(plan,item,7);indices=torch.arange(4,device='cuda')
    batch=engine(indices)
    p=torch.zeros((1,1,7,9),device='cuda',dtype=torch.float64);opened=p[...,0].clone()
    try:
        for i in range(4):
            p[...,0]=i*1000;opened.fill_(-i*100)
            signal=plan.signals(item,indices[i],p,opened,p[...,0])
            torch.testing.assert_close(batch[0][i],signal[0],rtol=0,atol=0)
            torch.testing.assert_close(batch[1][i],plan.ratios[0],rtol=0,atol=0)
            torch.testing.assert_close(batch[2][i],policy[0],rtol=0,atol=0)
            torch.testing.assert_close(batch[4][i],plan.owner,rtol=0,atol=0)
    finally:plan.close()


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
def test_double_buffer_packets_equal_independent_inference():
    from research.vectorized_backtest.v60.genome import sample
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.inference_pipeline import Pipeline
    item=market_fixture('cuda');members=sample(np.random.default_rng(7),3)
    plan=NetworkPlan(members,'cuda');plan.prepare(3,7)
    signals=torch.empty((1,3,7),device='cuda',dtype=torch.int16)
    pipe=Pipeline(plan,item,16,4,signals,None,None,{})
    p=torch.zeros((1,3,7,9),device='cuda',dtype=torch.float64);opened=p[...,0].clone()
    try:
        for begin in range(0,17,4):
            pipe.stage(begin);pipe.submit(begin+4);pipe.submit(begin+8)
            for i in range(begin,min(begin+4,17)):
                index=torch.tensor(i,device='cuda');pipe.provide(index)
                observed=signals.clone();ratios=plan.ratios.clone()
                expected=plan.signals(item,index,p,opened,p[:,:1,:,0])
                torch.testing.assert_close(observed,expected,rtol=0,atol=0)
                torch.testing.assert_close(ratios,plan.ratios,rtol=0,atol=0)
    finally:pipe.close();plan.close()


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
@pytest.mark.parametrize('journal_capacity',[None,112])
def test_pipelined_financial_replay_matches_sequential_signals(monkeypatch,journal_capacity):
    from unittest.mock import patch
    from research.vectorized_backtest.v60.genome import Individual,Network,Neuron,STAGES
    from research.vectorized_backtest.v60.policy import Policy
    from research.vectorized_backtest.v60.authority import ALLOWED
    from research.vectorized_backtest.v60.network import NetworkPlan
    from research.vectorized_backtest.v60.order_replay import replay
    from research.vectorized_backtest.v60.episodes import Episodes
    from research.vectorized_backtest.v60.evaluator import Execution
    members=[Individual({s:Network((Neuron((ALLOWED[0],),(1.,),.5 if s in ('entry','exit') else -2.),)) for s in STAGES},
        Policy(stop_fraction=.2,target_fraction=.5,swing_left=0,swing_right=0),{}).validate()]*2
    def run(pipeline):
        monkeypatch.setenv('V60_INFERENCE_PIPELINE',str(pipeline))
        b,u,t=2,7,65;item=market_fixture('cuda',t,u)
        item.execution_tail=None;item.listing_ids=np.arange(u);item.arrays={'clocks':np.arange(t)}
        p=torch.zeros((1,b,u,9),device='cuda',dtype=torch.float64);a=torch.zeros((1,b,16),device='cuda',dtype=torch.float64);a[...,8]=float('inf')
        opened=torch.zeros((1,b,u),device='cuda',dtype=torch.float64);ticker=torch.zeros((1,b,u,2),device='cuda',dtype=torch.float64)
        plan=NetworkPlan(members,'cuda');plan.prepare(b,u)
        sig=torch.zeros((1,b,u),device='cuda',dtype=torch.int16)
        prices=item.tensors['mark'][:,None,None];valid=item.tensors['quote_valid'][:,None,None]
        cap=torch.stack((torch.full_like(prices,1000),torch.full_like(prices,1000),torch.arange(t,device='cuda')[:,None,None,None].expand_as(prices)),dim=-1)
        policy=torch.tensor([.2,.5,.25,.03,0.,0.,10.,1.,0.,0.,0.],device='cuda',dtype=torch.float64)[None,None,None].expand(1,b,1,11)
        ep=Episodes(1,b,8192,'cuda',max_listings=u,trace=True)
        def provide(index,position,entry):plan.signals(item,index,position,entry,prices[0],out=sig)
        try:
            with patch('research.vectorized_backtest.v60.order_tail.Tail',lambda *a:SimpleNamespace(receipt={'pages':[]})):
                result=replay(item=item,plan=plan,position=p,aggregate=a,opened=opened,policy=policy,prices=prices,
                    observed=valid,membership=valid,signals=sig,values=torch.zeros((1,1),device='cuda'),
                    ids=torch.zeros((t,1,u),device='cuda',dtype=torch.long),columns=torch.zeros(b,device='cuda',dtype=torch.long),
                    limits=torch.zeros((1,b,1,5),device='cuda'),ticker_state=ticker,bids=item.tensors['bid'][:,None,None],
                    asks=item.tensors['ask'][:,None,None],quote_valid=valid,capacity=cap,execution=Execution(),episodes=ep,
                    provide=provide,progress=None,block=8,gate=None,backend='compile',record_capacity=journal_capacity)
            return p.cpu(),a.cpu(),result,ep.export()
        finally:plan.close()
    before=run(0);after=run(1)
    for a,b in zip(before[:2],after[:2]):torch.testing.assert_close(a,b,rtol=0,atol=0)
    assert before[2]==after[2]
    assert before[2]['admission_entry_first_fills'][0]>0
    assert before[3]==after[3]
