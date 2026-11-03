from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.environment import Environment
from research.rl_trading.v7.model import Policy,tensor_batch


def sample(day='2026-01-05', seconds=1000):
    shape=(seconds,2)
    execution={k:np.full(shape,value,dtype=dtype) for k,value,dtype in (
        ('mark',10.,float),('bid',9.99,float),('ask',10.01,float),('bid_size',1000.,float),
        ('ask_size',1000.,float),('quote_valid',True,bool),('quote_us',0,np.int64))}
    execution['quote_us'][:]=np.arange(1,seconds+1)[:,None]*1000000
    selection=np.full((20,5),-1,np.int32);selection[:,0]=0;selection[:,1]=1
    return SimpleNamespace(day=day,tickers=['A','B'],clocks=np.arange(seconds),selection=selection,
                           features=np.zeros((20,5,98),np.float32),execution=execution)


def test_next_second_spread_fee_and_sticky_liquidation():
    s=sample();s.execution['ask'][1,0]=10.1
    cfg=replace(Config(),max_hold_seconds=2)
    env=Environment(s,cfg)
    initial=env.equity
    _,reward,_,_=env.step(1)
    assert env.last_fills[0]['price']==pytest.approx(10.1*(1+cfg.extra_slippage))
    assert env.last_fills[0]['second']==1 and env.fees>0 and reward<0
    assert env.quantity==100
    s.execution['quote_valid'][4,0]=False
    env.step(0);env.step(0)
    assert env.quantity>0
    env.step(0)
    assert env.pending_exit and env.quantity>0
    env.step(0)
    assert env.quantity==0 and env.cash<initial


def test_quote_size_not_replenished_for_same_snapshot():
    s=sample();s.execution['bid_size'][:]=100
    s.execution['quote_us'][:]=1000000
    env=Environment(s)
    env.step(1);env.step(6)
    assert env.quantity==90
    env.step(0)
    assert env.quantity==90
    s.execution['quote_us'][4:,0]=4000000
    env.step(0)
    assert env.quantity==80


def test_future_candidate_not_observed_and_tail_no_entry():
    s=sample();s.selection[:10,1]=-1;s.features[:10,1]=0
    env=Environment(s)
    assert not env.observe()['candidate_valid'][1]
    assert not env.observe()['action_mask'][2]
    with pytest.raises(ValueError):env.step(2)
    env.t=19
    assert not env.observe()['action_mask'][1:].any()


def test_attention_permutation_and_gradients():
    torch.set_num_threads(1);torch.manual_seed(1)
    policy=Policy(width=16,heads=4)
    obs=Environment(sample()).observe()
    obs['features']=np.random.default_rng(1).normal(size=(5,98)).astype(np.float32)
    batch=tensor_batch([obs,obs])
    distribution,value=policy(**batch)
    perm=torch.tensor([1,0,2,3,4])
    changed={k:v.clone() for k,v in batch.items()}
    changed['features']=changed['features'][:,perm]
    changed['candidate_valid']=changed['candidate_valid'][:,perm]
    changed['action_mask'][:,1:6]=changed['action_mask'][:,1:6][:,perm]
    other,other_value=policy(**changed)
    assert torch.allclose(distribution.probs[:,1:6][:,perm],other.probs[:,1:6],atol=1e-6)
    assert torch.allclose(value,other_value,atol=1e-6)
    loss=-distribution.log_prob(torch.tensor([1,0])).mean()+value.square().mean()
    loss.backward()
    assert all(torch.isfinite(p.grad).all() for p in policy.parameters() if p.grad is not None)
    assert policy.time_attention.in_proj_weight.grad.abs().sum()>0


def test_actual_training_resume_matches_continuous(tmp_path,monkeypatch):
    from research.rl_trading.v7 import train
    class Dataset:
        days=['2026-01-05','2026-01-06']
        binding={'test_fixture':True}
        def __init__(self,root):pass
        def load(self,day,cache_root=None):return sample(day)
    monkeypatch.setattr(train,'TrainingDataset',Dataset)
    monkeypatch.setattr(train,'Config',lambda:replace(Config(),width=16,heads=4,rollout=8,batch=4,ppo_epochs=1,max_hold_seconds=2))
    continuous=tmp_path/'continuous';resume=tmp_path/'resume'
    assert train.main(['--output',str(continuous)])==0
    original=train.save_checkpoint
    count=[0]
    def stop_once(path,state):
        original(path,state)
        if path.name=='checkpoint_latest.pt':
            count[0]+=1
            if count[0]==1:(path.parent/'STOP').write_text('test controlled rollout stop')
    monkeypatch.setattr(train,'save_checkpoint',stop_once)
    assert train.main(['--output',str(resume)])==2
    (resume/'STOP').unlink()
    monkeypatch.setattr(train,'save_checkpoint',original)
    assert train.main(['--output',str(resume),'--resume'])==0
    a=torch.load(continuous/'checkpoint_latest.pt',weights_only=False)
    b=torch.load(resume/'checkpoint_latest.pt',weights_only=False)
    assert a['completed']==b['completed'] and a['iteration']==b['iteration']
    for k,v in a['policy'].items():assert torch.equal(v,b['policy'][k]),k
    (resume/'results.json').unlink()
    (resume/'updates'/f"{b['iteration']:06d}.json").unlink()
    assert train.main(['--output',str(resume),'--resume'])==0
    assert (resume/'results.json').exists()
    assert (resume/'updates'/f"{b['iteration']:06d}.json").exists()
    assert train.main(['--output',str(tmp_path/'evaluation'),'--evaluate',str(resume/'checkpoint_latest.pt')])==0
    with pytest.raises(ValueError,match='training dates'):
        train.main(['--output',str(tmp_path/'forbidden'),'--dates','2026-08-11'])
