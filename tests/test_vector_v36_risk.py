import pytest
import torch
from research.vectorized_backtest.v36.evaluator import Execution,step
from research.vectorized_backtest.v36.objectives import local_panel


def fixture(stop=.3,addition=False):
    p=torch.zeros((1,1,1,9),dtype=torch.float64)
    a=torch.zeros((1,1,16),dtype=torch.float64);a[...,8]=float('inf')
    if addition:
        p[...,0]=200;p[...,1]=10;p[...,2]=8;p[...,3]=20;p[...,4]=10
    policy=torch.tensor([stop,.5,.25,.03,0.,0.,20,0,0],dtype=torch.float64).reshape(1,1,1,9)
    price=torch.full((1,1,1),10.,dtype=torch.float64);valid=torch.ones((1,1,1),dtype=torch.bool)
    signals=torch.full((1,1,1),4 if addition else 1,dtype=torch.int16)
    ratios=torch.ones((1,1,1,3),dtype=torch.float64)
    return (p,a,price,price,valid,valid,signals,price*.7,policy,torch.tensor(1.,dtype=torch.float64),torch.tensor(False),1000.,200.,0.,.005,1.),ratios


@pytest.mark.parametrize('stop,addition,accepted',[(.3,False,True),(.31,False,False),(.2,True,True)])
def test_risk_budget_removed_and_distance_retained(stop,addition,accepted):
    args,ratios=fixture(stop,addition);p,a=step(*args,True,.3,0.,ratios,0.)
    if addition:assert p[...,0].item()==220 and a[...,12].item()>300
    else:assert (p[...,0].item()>0)==accepted
    assert a[...,14].item()==(0 if accepted or addition else 1)
    assert a[...,15].item()==0


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('stop,addition',[(.3,False),(.31,False),(.2,True)])
def test_new_limits_cuda_match_cpu(stop,addition):
    from research.vectorized_backtest.v36.protected_transition import transition
    args,ratios=fixture(stop,addition);expected=step(*args,True,.3,0.,ratios,0.)
    actual=transition(*(v.cuda() if isinstance(v,torch.Tensor) else v for v in args),.3,0.,ratios.cuda(),0.)
    for a,b in zip(expected,actual):torch.testing.assert_close(a,b.cpu(),rtol=1e-12,atol=1e-10)


def test_objective_accepts_high_dollar_risk_but_checks_distance_and_terminal():
    from test_vector_v32_two_phase import metrics
    m=metrics(3);m['maximum_position_risk']=[1000.]*3;m['maximum_stop_distance']=[.3,.31,.1];m['terminal_valid']=[True,True,False]
    assert local_panel([m])['eligible'].tolist()==[[True,False,False]]
    assert Execution().validate().maximum_stop_distance==.3
    with pytest.raises(ValueError):Execution(maximum_position_risk=300).validate()
    with pytest.raises(ValueError):Execution(maximum_stop_distance=.31).validate()
