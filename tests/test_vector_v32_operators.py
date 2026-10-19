from dataclasses import replace
import numpy as np
import pytest
import torch
from test_vector_v32 import toy,probe_fixture
from research.vectorized_backtest.v32.genome import Neuron,Network,OPS,STAGES,INPUTS,sample,Individual
from research.vectorized_backtest.v32.network import NetworkPlan
from research.vectorized_backtest.v32.calibration import observe
from research.vectorized_backtest.v32.operators import evaluate

@pytest.mark.parametrize('op',OPS)
def test_operator_probe_reference_and_identity(op):
    node=Neuron((0,1,2,3,4),(.7,-.3,0.,.8,.2),.12,1,op)
    net=Network((node,),.123).validate();probe=probe_fixture()
    fast=observe(net,probe);ref=observe(net,probe,reference=True)
    np.testing.assert_allclose(fast[0],ref[0],rtol=0,atol=2e-6)
    np.testing.assert_array_equal(fast[1],ref[1])
    assert Network.restore(net.payload())==net
    assert np.isfinite(fast[0]).all() and np.max(np.abs(fast[0]))<=1

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('op',OPS)
def test_operator_cuda_cpu_recurrent_resume_and_reset(op):
    nodes=(Neuron((0,),(.8,),.03,1,'identity'),Neuron((INPUTS,1,-1,2,3),(.7,-.3,.4,.8,.2),.12,2,op))
    net=Network(nodes,.123).validate();member=sample(np.random.default_rng(20),1)[0]
    member=replace(member,networks={s:net for s in STAGES})
    plans=[NetworkPlan([member],d) for d in ('cpu','cuda')];data=[toy(d,t=8) for d in ('cpu','cuda')]
    states=[]
    for tick in range(5):
        decisions=[]
        for plan,item in zip(plans,data):
            p=torch.zeros((1,1,3,9),device=plan.device,dtype=torch.float64)
            result=plan.signals(item,torch.tensor(tick,device=plan.device),p,p[...,0],item.tensors['mark'][tick][None,None])
            decisions.append(result.cpu());states.append(plan.export_state())
        assert torch.equal(*decisions)
        torch.testing.assert_close(states[-1]['memory'],states[-2]['memory'],rtol=0,atol=2e-6)
        assert torch.equal(states[-1]['memory_known'],states[-2]['memory_known'])
        if tick==2:
            for plan in plans:
                state=plan.export_state();plan.reset();plan.restore_state(state)
    for plan in plans:
        plan.reset();state=plan.export_state();assert not state['memory'].any() and not state['memory_known'].any()


def test_attention_is_input_dependent_and_stable():
    def run(q):return evaluate('attention',[np.array([q],np.float32),np.array([1.],np.float32),np.array([.8],np.float32),np.array([-1.],np.float32),np.array([-.8],np.float32)],0.,np.zeros(1,np.float32))[0]
    assert run(8)>.6 and run(-8)<-.6 and abs(run(0))<1e-7
    assert np.isfinite(run(8))


def test_temporal_convolution_has_exact_causal_taps():
    probe=probe_fixture();probe['inputs'][:]=0;probe['inputs'][:,0,:,0]=.6
    nodes=(Neuron((0,),(1.,),0.,1,'identity'),Neuron((-1,),(1.,),0.,2,'identity'),Neuron((0,-1,-2),(.5,.3,.2),0.,3,'conv'))
    net=Network(nodes,0.).validate();out=observe(net,probe)[0]
    expected=np.tanh(np.array([.3,.18,.12,0,0,0,0,0],np.float32))
    np.testing.assert_allclose(out[0,:,0],expected,atol=1e-7)
    probe['inputs'][:,5:,:,0]=.9
    altered=observe(net,probe)[0]
    np.testing.assert_array_equal(out[:,:5],altered[:,:5])


def test_operator_changes_semantic_identity():
    from research.vectorized_backtest.v32.lineage import program_key
    a=sample(np.random.default_rng(3),1)[0];n=Network((Neuron((0,1),(1.,1.),0.),),0.)
    a=replace(a,networks={s:n for s in STAGES});b=replace(a,networks=dict(a.networks,entry=replace(n,nodes=(replace(n.nodes[0],op='max'),))))
    assert program_key(a)!=program_key(b)
    assert Individual.restore(b.payload())==b


def test_probe_nullable_feature_rows_preserve_missingness():
    import polars as pl
    from research.vectorized_backtest.v32.sampling_probe import feature_rows
    rows=feature_rows(pl.Series('feature_row',[0,None,12,-1],dtype=pl.Int64))
    assert rows.dtype==np.int64
    np.testing.assert_array_equal(rows,[0,-1,12,-1])
    assert feature_rows(pl.Series('feature_row',[],dtype=pl.Int64)).dtype==np.int64
    with pytest.raises(ValueError,match='integer'):feature_rows(pl.Series([1.5]))
    with pytest.raises(ValueError,match='Observed'):feature_rows(pl.Series([None],dtype=pl.Int64),[True],1)
    with pytest.raises(ValueError,match='outside'):feature_rows(pl.Series([1]),[False],1)
    with pytest.raises(ValueError,match='negative'):feature_rows(pl.Series([-2]))


def test_missing_reference_never_exposes_safe_index_values():
    import torch
    from research.vectorized_backtest.v27.resident_features import ResidentFeatures
    from research.vectorized_backtest.v27.features import ORIGINAL,LEGACY
    bank=ResidentFeatures.__new__(ResidentFeatures)
    bank.device=torch.device('cpu');bank.column_plans={}
    bank.tensors={'feature_row':torch.tensor([-1,0]),'mark':torch.ones(2),
        'base':torch.tensor([[.75]]),'base_valid':torch.tensor([[True]]),
        'indicators':torch.tensor([[.5]]),'enriched':torch.tensor([[.25]])}
    rows=torch.tensor([[0,1,-1]])
    values,known=bank._values_reference(rows,torch.zeros_like(rows),torch.ones_like(rows),[0,len(ORIGINAL),len(LEGACY)])
    assert not known[0,0].any() and not known[0,2].any()
    assert known[0,1].all()
    assert torch.equal(values[0,0],torch.zeros(3))
    assert torch.equal(values[0,2],torch.zeros(3))
    assert torch.equal(values[0,1],torch.tensor([.75,.5,.25]))
