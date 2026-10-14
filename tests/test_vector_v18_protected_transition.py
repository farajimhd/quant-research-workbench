import pytest
import torch
from research.vectorized_backtest.v18.evaluator import step


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('width',[3,32,704])
def test_fused_financial_transition_matches_torch_trajectories(width):
    from research.vectorized_backtest.v18.protected_transition import transition
    torch.manual_seed(84);shape=(2,16,width);p=torch.zeros((*shape,9),dtype=torch.float64,device='cuda')
    a=torch.zeros((2,16,16),dtype=torch.float64,device='cuda');a[...,8]=float('inf');fp=p.clone();fa=a.clone()
    policy=torch.tensor([.15,.05,.25,.10,.01,.02,5.,3.,0.],dtype=torch.float64,device='cuda').expand(*shape,9).clone();policy[:,::3,:,8]=1.
    prices=torch.rand((2,1,width),dtype=torch.float64,device='cuda')*10+2
    for clock in range(1,81):
        previous=prices.clone();prices=prices*(1+torch.randn_like(prices)*.03)
        observed=torch.rand_like(prices)>.02;membership=torch.rand_like(prices)>.5
        signals=torch.randint(0,32,shape,dtype=torch.uint8,device='cuda');swing=prices*.88
        terminal=torch.full((2,1,1),clock==80,device='cuda',dtype=torch.bool);tick=torch.tensor(float(clock),device='cuda',dtype=torch.float64)
        common=(prices,previous,observed,membership,signals,swing,policy,tick,terminal,1000.,200.,.001,.005,1.)
        p,a=step(p,a,*common,True,.20,300.)
        fp,fa=transition(fp,fa,*common,.20,300.)
        torch.testing.assert_close(fp,p,rtol=1e-11,atol=1e-8)
        torch.testing.assert_close(fa,a,rtol=1e-11,atol=1e-8)
    assert torch.count_nonzero(fp[...,0])==0


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_protected_replay_graph_preserves_complete_episode_ledger():
    from test_vector_v18_regressions import synthetic,member
    from research.vectorized_backtest.v18.evaluator import replay_cohort,Execution
    data=synthetic('cuda');gates=torch.tensor([1,4,8,0,0,0],device='cuda',dtype=torch.uint8)[None,:,None]
    execution=Execution(fee_per_share=.005,minimum_order_fee=1.)
    expected=replay_cohort([data],[member()],[gates],execution=execution,backend='eager',trace=True)[0]
    actual=replay_cohort([data],[member()],[gates],execution=execution,backend='compile',trace=True)[0]
    for key,value in expected.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(actual[key],value,rtol=1e-11,atol=1e-8)
        else:assert actual[key]==value


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('stop,swing',[ (.8853,0.),(.10,7.9),(.20,0.) ])
def test_fused_entry_risk_limits_match_oracle_at_boundaries(stop,swing):
    from research.vectorized_backtest.v18.protected_transition import transition
    p=torch.zeros((1,1,1,9),device='cuda',dtype=torch.float64);a=torch.zeros((1,1,16),device='cuda',dtype=torch.float64);a[...,8]=float('inf')
    price=torch.full((1,1,1),10.,device='cuda',dtype=torch.float64);flags=price>0
    policy=torch.tensor([stop,1.,.25,.1,0.,0.,5.,1.,float(swing>0)],device='cuda',dtype=torch.float64).reshape(1,1,1,9)
    args=(p,a,price,price,flags,flags,flags.to(torch.uint8),price*0+swing,policy,torch.ones((),device='cuda',dtype=torch.float64),~flags,1000.,200.,0.,.005,1.)
    expected=step(*args,True,.20,300.);actual=transition(*args,.20,300.)
    for left,right in zip(actual,expected):torch.testing.assert_close(left,right,rtol=1e-12,atol=1e-10)
