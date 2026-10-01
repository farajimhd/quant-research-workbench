import numpy as np
import polars as pl
import pytest
import torch

from research.rl_trading.v6.episode_windows import rolling_allocation, opportunity_windows, WindowConfig
from research.rl_trading.v6.objective import bracket_loss


def candidates():
    return pl.DataFrame({'episode_uid':['d:a:1','d:a:1','d:a:1','d:b:1'],
        'time_us':[1_000_000,2_000_000,4_000_000,3_000_000],
        'score':[.04,.03,.02,.02],'direction':[1,1,1,1],
        'decision_close':[1.,1.02,1.20,1.]})


def test_rolling_unique_episode_normalization_and_boundary():
    frame=rolling_allocation(candidates())
    first=frame.filter(pl.col('decision_us')==1_000_000).sort('episode_uid')
    assert first.height==2
    assert first['allocation_weight'].to_list()==pytest.approx([2/3,1/3])
    assert frame.group_by('decision_us').agg(pl.col('allocation_weight').sum())['allocation_weight'].to_list()==pytest.approx([1.]*4)
    duplicate=candidates().vstack(candidates().head(1))
    with pytest.raises(ValueError,match='Duplicate'):rolling_allocation(duplicate)
    boundary=pl.DataFrame({'episode_uid':['a','b'],'time_us':[0,15_000_000],'score':[.02,.02],'direction':[1,1]})
    assert rolling_allocation(boundary).filter(pl.col('decision_us')==0).height==1


def test_all_episode_windows_independent_of_selection_and_peak_guard():
    episodes=pl.DataFrame({'session_date':['d','d'],'listing_id':['a','b'],'episode_id':[1,1],
        'direction':[1,1],'entry_hint_us':[1_000_000,1_000_000],'end_us':[7_000_000,7_000_000]})
    bars=pl.DataFrame({'listing_id':['a']*6+['b']*6,
        'time_us':list(range(1_000_000,7_000_000,1_000_000))*2,
        'close':[1.,1.02,1.10,1.20,1.15,1.10]*2,'high':[1.,1.02,1.10,1.20,1.15,1.10]*2})
    flat,held=opportunity_windows(episodes,candidates(),bars)
    assert flat['episode_uid'].n_unique()==2
    assert flat.filter((pl.col('episode_uid')=='d:a:1')&(pl.col('time_us')==4_000_000))['enter_probability'][0]==0
    assert flat.filter(pl.col('enter_probability')>0).height==3
    assert flat.group_by('episode_uid').agg(pl.col('sample_weight').sum())['sample_weight'].to_list()==pytest.approx([1,1])
    assert held.filter(pl.col('time_us')<=pl.col('hypothetical_entry_us')).is_empty()
    assert held.filter(pl.col('time_us')==6_000_000)['exit_probability'].to_list()==[1,1]


def test_soft_entry_loss_does_not_penalize_other_profitable_tickers():
    logits=torch.tensor([0.,2.,8.],requires_grad=True)
    sizes=torch.tensor([.4,.6],requires_grad=True)
    empty=torch.empty(0)
    loss,_=bracket_loss(logits,sizes,empty,empty,token=1,size_fraction=.4,
        wait_hold=True,soft_tokens=(0,1),soft_probabilities=(.2,.8))
    loss.backward()
    assert logits.grad[2]==0
    assert logits.grad[:2].abs().sum()>0
    with pytest.raises(ValueError,match='probability'):
        bracket_loss(logits,sizes,empty,empty,token=1,size_fraction=.4,
            soft_tokens=(0,1),soft_probabilities=(.2,.2))

def test_independent_episode_actual_optimizer_and_evaluation():
    from datetime import date
    from pathlib import Path
    from research.rl_trading.v6.bank import SessionBank
    from research.rl_trading.v6.session_data import PackedSession
    from research.rl_trading.v6.training import TeacherDecision, train_session
    from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
    clocks=np.array([1_000_000,2_000_000,1_000_000,2_000_000],np.int64)
    scalar=np.zeros((4,37),np.float32);scalar[:,0]=[.1,.2,.3,.4]
    bank=SessionBank(Path('unused'),{'offsets':{'a':[0,2],'b':[2,4]}},
        clocks,scalar,np.zeros((4,2,5,11),np.float32))
    session=PackedSession(date(2026,8,5),'train',Path('unused'),'test',bank,None,('a','b'))
    labels=(TeacherDecision(1_000_000,0,2,np.array([10000,10000,0,0,0,0,0],np.float32),
        np.empty(0,np.int64),np.empty((0,11),np.float32),np.array([False,True]),
        np.empty(0,bool),np.empty(0,bool),np.empty(0,bool),size_fraction=.4,
        soft_tokens=(0,2),soft_probabilities=(.1,.9),episode_uid='d:b:1'),
        TeacherDecision(2_000_000,0,6,np.array([9990,10000,0,.001,1,0,0],np.float32),
        np.array([1],np.int64),np.array([[1,10,1,0,0,0,0,0,0,0,0]],np.float32),
        np.array([False,False]),np.array([True]),np.array([False]),np.array([False]),
        soft_tokens=(3,6),soft_probabilities=(.2,.8),episode_uid='d:b:1'))
    policy=RankedBracketActorCritic(width=8,wait_hold=True)
    policy.independent_episode_supervision=True
    opt=torch.optim.Adam(policy.parameters(),lr=.001)
    before={k:v.clone() for k,v in policy.state_dict().items()}
    result=train_session(policy,opt,session,labels,(),device=torch.device('cpu'),clocks_per_chunk=2)
    assert result.decisions==2 and result.execution_outcomes==0 and result.optimizer_steps==1
    assert np.isfinite(result.mean_loss)
    assert any(not torch.equal(v,policy.state_dict()[k]) for k,v in before.items())
    before={k:v.clone() for k,v in policy.state_dict().items()}
    from dataclasses import replace
    session=replace(session,role='development')
    result=train_session(policy,opt,session,labels,(),device=torch.device('cpu'),clocks_per_chunk=2,evaluation=True)
    assert result.optimizer_steps==0
    assert all(torch.equal(v,policy.state_dict()[k]) for k,v in before.items())
