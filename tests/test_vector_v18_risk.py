"""Protected financial transitions, including swing stops and paid fees."""
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v18.evaluator import step,Execution
from research.vectorized_backtest.v18.objectives import score,session_scores
from research.vectorized_backtest.v18.search_space import fraction
from test_vector_v18_regressions import metrics


def state():
    p=torch.zeros((1,1,1,9),dtype=torch.float64)
    a=torch.zeros((1,1,16),dtype=torch.float64);a[...,8]=float('inf')
    return p,a


def transition(p,a,price,signal,stop=.10,swing=0.,clock=1):
    value=torch.full((1,1,1),price,dtype=torch.float64)
    flags=torch.ones_like(value,dtype=torch.bool)
    policy=torch.tensor([stop,1.,.25,.1,0.,0.,5.,1.,float(swing>0)],dtype=torch.float64).reshape(1,1,1,9)
    return step(p,a,value,value,flags,flags,torch.full_like(value,signal,dtype=torch.uint8),torch.full_like(value,swing),policy,torch.tensor(float(clock)),torch.zeros_like(flags),1000.,200.,0.,.005,1.,True,.20,300.)


def test_wide_fixed_and_swing_stops_are_rejected_not_clamped():
    for stop,swing in ((.8853,0.),(.1,7.9)):
        p,a=transition(*state(),10.,1,stop,swing)
        assert p[...,0].item()==0 and a[...,14].item()==1
    p,a=transition(*state(),10.,1,.20)
    assert p[...,2].item()==8. and a[...,12].item()==pytest.approx(202.)


def test_additions_cannot_offset_paid_costs_or_exceed_risk_budget():
    p,a=transition(*state(),10.,1)
    for clock in range(2,7):p,a=transition(p,a,12.,4,clock=clock)
    assert p[...,5].item()==3 and a[...,15].item()==2
    assert a[...,12].item()<300 and a[...,12].item()>250
    assert a[...,13].item()<=.20


def test_global_and_local_reject_unsafe_receipts_even_with_high_profit():
    m=metrics([[10000.],[10000.],[10000.]])
    m['maximum_position_risk']=[301.,100.,100.];m['maximum_stop_distance']=[.1,.21,.1]
    result=score([m],[0,0,0]);_,eligible=session_scores([m],prepared=result)
    assert result['feasible'].tolist()==[False,False,True]
    assert eligible.tolist()==[[False,False,True]]
    assert result['components']['peak_position_risk_penalty'][2]==50


def test_stop_gene_remains_evolvable_with_room_up_to_twenty_percent():
    rng=np.random.default_rng(5);values=[fraction(rng,'stop_fraction') for _ in range(5000)]
    assert min(values)>0 and max(values)<=.20 and max(values)>.19
    with pytest.raises(ValueError,match='risk envelope'):Execution(maximum_stop_distance=.21).validate()


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_protected_compiled_transition_matches_eager_after_additions():
    p,a=state();p=p.cuda();a=a.cuda()
    value=torch.full((1,1,1),10.,dtype=torch.float64,device='cuda');flags=torch.ones_like(value,dtype=torch.bool)
    policy=torch.tensor([.1,1.,.25,.1,0.,0.,5.,1.,0.],dtype=torch.float64,device='cuda').reshape(1,1,1,9)
    compiled=torch.compile(step,fullgraph=True,dynamic=False)
    for clock in range(1,7):
        price=value if clock==1 else value*1.2
        args=(p,a,price,price,flags,flags,torch.full_like(value,1 if clock==1 else 4,dtype=torch.uint8),value*0,policy,torch.tensor(float(clock),device='cuda'),~flags,1000.,200.,0.,.005,1.,True,.20,300.)
        expected=step(*args);actual=compiled(*args)
        for x,y in zip(actual,expected):torch.testing.assert_close(x,y,rtol=1e-12,atol=1e-10)
        p,a=actual
    assert a[...,15].item()==2
