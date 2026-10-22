import pytest
import torch
from pathlib import Path
from research.vectorized_backtest.v41.evaluator import Execution,step
from research.vectorized_backtest.v41.objectives import local_panel


def fixture(stop=.2,addition=False):
    p=torch.zeros((1,1,1,9),dtype=torch.float64)
    a=torch.zeros((1,1,16),dtype=torch.float64);a[...,8]=float('inf')
    if addition:
        p[...,0]=200;p[...,1]=10;p[...,2]=8;p[...,3]=20;p[...,4]=10
    policy=torch.tensor([stop,.5,.25,.03,0.,0.,20,0,0],dtype=torch.float64).reshape(1,1,1,9)
    price=torch.full((1,1,1),10.,dtype=torch.float64);valid=torch.ones((1,1,1),dtype=torch.bool)
    signals=torch.full((1,1,1),4 if addition else 1,dtype=torch.int16)
    ratios=torch.ones((1,1,1,3),dtype=torch.float64)
    return (p,a,price,price,valid,valid,signals,price*.7,policy,torch.tensor(1.,dtype=torch.float64),torch.tensor(False),1000.,200.,0.,.005,1.),ratios


@pytest.mark.parametrize('stop,addition,accepted',[(.2,False,True),(.21,False,False),(.2,True,False)])
def test_v34_risk_budget_and_distance_restored(stop,addition,accepted):
    args,ratios=fixture(stop,addition);p,a=step(*args,True,.2,300.,ratios,0.)
    if addition:assert p[...,0].item()==200 and a[...,15].item()==1
    else:assert (p[...,0].item()>0)==accepted
    assert a[...,14].item()==(0 if accepted or addition else 1)
    assert a[...,15].item()==int(addition)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('stop,addition',[(.2,False),(.21,False),(.2,True)])
def test_new_limits_cuda_match_cpu(stop,addition):
    from research.vectorized_backtest.v41.protected_transition import transition
    args,ratios=fixture(stop,addition);expected=step(*args,True,.2,300.,ratios,0.)
    actual=transition(*(v.cuda() if isinstance(v,torch.Tensor) else v for v in args),.2,300.,ratios.cuda(),0.)
    for a,b in zip(expected,actual):torch.testing.assert_close(a,b.cpu(),rtol=1e-12,atol=1e-10)


def test_objective_checks_v34_risk_distance_and_terminal():
    from test_vector_v32_two_phase import metrics
    m=metrics(4);m['maximum_position_risk']=[300.,301.,100.,100.];m['maximum_stop_distance']=[.2,.1,.21,.1];m['terminal_valid']=[True,True,True,False]
    assert local_panel([m])['eligible'].tolist()==[[True,False,False,False]]
    assert Execution().validate().maximum_stop_distance==.2
    assert Execution().validate().maximum_position_risk==300.
    with pytest.raises(ValueError):Execution(maximum_position_risk=0).validate()
    with pytest.raises(ValueError):Execution(maximum_stop_distance=.3).validate()


@pytest.mark.parametrize('stop,addition',[(.2,False),(.21,False),(.2,True)])
def test_cpu_execution_matches_v34_exactly(stop,addition):
    from research.vectorized_backtest.v34.evaluator import step as baseline,Execution as BaselineExecution
    from research.vectorized_backtest.v34.objectives import local_panel as baseline_panel
    from test_vector_v32_two_phase import metrics
    assert Execution().__dict__==BaselineExecution().__dict__
    args,ratios=fixture(stop,addition)
    for a,b in zip(step(*args,True,.2,300.,ratios,0.),baseline(*args,True,.2,300.,ratios,0.)):assert torch.equal(a,b)
    m=metrics(4);m['maximum_position_risk']=[300.,301.,100.,100.];m['maximum_stop_distance']=[.2,.1,.21,.1]
    for key,value in baseline_panel([m]).items():
        import numpy as np
        np.testing.assert_array_equal(local_panel([m])[key],value)


def test_execution_and_eligibility_source_match_v34_except_namespace():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','objectives.py'):
        assert (root/'v41'/name).read_text()==(root/'v34'/name).read_text().replace('v34','v41').replace('V34','V41')
