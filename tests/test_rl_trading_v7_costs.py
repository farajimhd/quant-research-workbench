from types import SimpleNamespace
import numpy as np
import torch
from research.rl_trading.v7.experiments.cost_aware import CostEnvironment,CostPolicy,current_costs
from research.rl_trading.v7.model import tensor_batch


def fixture():
    n=1000
    e={k:np.full((n,2),v,dtype=d) for k,v,d in [('mark',10.,float),('bid',9.99,float),
        ('ask',10.01,float),('bid_size',1000.,float),('ask_size',1000.,float),
        ('quote_valid',True,bool),('quote_us',1000000,np.int64)]}
    selection=np.tile(np.array([0,1,-1,-1,-1],np.int32),(20,1))
    return SimpleNamespace(day='2026-01-05',tickers=['A','B'],clocks=np.arange(1,n+1),
                           execution=e,selection=selection,features=np.zeros((20,5,98),np.float32))


def test_current_cost_token_is_causal_and_accounts_for_consumption():
    s=fixture();env=CostEnvironment(s)
    before=env.observe()['features'].copy()
    s.execution['ask'][1:,0]=20.
    assert np.array_equal(before,env.observe()['features'])
    env.used[(0,1)]=(1000000,100)
    costs=current_costs(env,0)
    assert costs[1]==0 and costs[8]==1
    assert costs[9]==0 and costs[10]==0
    s.execution['quote_valid'][0,0]=False
    costs=current_costs(env,0)
    assert costs[7]==1 and costs[13]==1 and costs[11]==0


def test_absolute_price_and_commission_observability():
    s=fixture();env=CostEnvironment(s)
    for key in ('mark','bid','ask'):s.execution[key][:,1]*=10
    low,high=current_costs(env,0),current_costs(env,1)
    assert low[0]!=high[0] and low[2]!=high[2]
    obs=env.observe()
    assert obs['features'].shape==(5,112)
    assert np.all(obs['features'][2:]==0)
    torch.set_num_threads(1)
    model=CostPolicy(width=16,heads=4)
    dist,value=model(**tensor_batch([obs]))
    loss=-dist.log_prob(torch.tensor([1])).mean()+value.square().mean()
    loss.backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
