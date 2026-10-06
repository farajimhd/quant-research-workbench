from dataclasses import replace
import copy
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.teacher_forecast import (ForecastWindows, LabelForecast,
    allocation_loss, forecast_loss, configure)
from research.rl_trading.v6.ticker_heads import TickerDecoder
from research.rl_trading.v6.training import TeacherDecision, train_session
from research.rl_trading.v6.price_action_opportunities import VERSION


def fixture(device='cpu', count=12):
    torch.manual_seed(42)
    clock=np.arange(1,count+1,dtype=np.int64)*1_000_000
    scalar=np.zeros((count*2,37),np.float32)
    scalar[:,0]=np.tile(np.linspace(.1,.4,count),2)
    scalar[:,8]=2
    bank=SessionBank(Path('unused'),{'offsets':{'A':[0,count],'B':[count,count*2]}},
        np.tile(clock,2),scalar,np.zeros((count*2,2,5,11),np.float32))
    session=PackedSession(date(2026,7,31),'train',Path('unused'),'fixture',bank,None,('A','B'))
    policy=RankedBracketActorCritic(width=16,wait_hold=True,
        config=MarketAttentionConfig(top_r=1,market_tokens=2,heads=2)).to(device)
    configure(policy)
    policy.independent_episode_supervision=True;policy.full_market_actions=True
    p=np.tile(np.array([[.9,.1,0,0]],np.float32),(count,1))
    decisions=tuple(TeacherDecision(int(t),0,1,np.array([10000,10000,0,0,0,0,0],np.float32),
        np.empty(0,np.int64),np.empty((0,11),np.float32),np.array([True,False]),
        np.empty(0,bool),np.empty(0,bool),np.empty(0,bool),
        soft_tokens=(0,1),soft_probabilities=(.1,.9),label_version=VERSION,episode_uid='fixture:A:1',
        allocation_ratio_target=.25,forecast_probabilities=p[i:i+5],forecast_close_us=clock[i:i+5])
        for i,t in enumerate(clock))
    return policy,session,decisions


def test_windows_keep_actual_gaps_identity_and_tail_masks():
    frame=pl.DataFrame(dict(listing_id=['A']*3+['B']*2,time_us=[100,400,900,200,1000],
        teacher_probabilities=[[1.,0,0,0],[0.,1,0,0],[0.,0,1,0],[0.,1,0,0],[0.,0,0,1]]))
    windows=ForecastWindows.from_frame(frame)
    p,t=windows.window(1)
    assert t.tolist()==[400,900] and len(p)==2
    assert np.shares_memory(p,windows.probabilities)
    assert windows.window(3)[1].tolist()==[200,1000]
    assert not p.flags.writeable
    with pytest.raises(ValueError,match='sorted'):
        ForecastWindows.from_frame(frame.reverse())
    with pytest.raises(ValueError,match='Duplicate'):
        ForecastWindows.from_frame(pl.concat([frame.head(1)]*2))


def test_held_forecasts_preserve_saved_quality_clock_and_pair_boundaries():
    frame=pl.DataFrame(dict(listing_id=['A']*5,time_us=[100,400,900,1100,1400],pair_id=[1,1,1,2,2],
        teacher_probabilities=[[0.,1,0,0]]*5,action=['WAIT']*5,exit_gain=[.1,.2,None,.3,.1],
        exit_quality=[.2,.95,None,.8,.1],reference_action=['HOLD','EXIT','WAIT','HOLD','HOLD']))
    windows=ForecastWindows.from_reference_frame(frame)
    assert windows.action_window(0).tolist()==[2,3]
    assert windows.window(0)[1].tolist()==[100,400]
    np.testing.assert_allclose(windows.window(0)[0],[[0,0,.8,.2],[0,0,.05,.95]],atol=1e-7)
    assert len(windows.window(2)[0])==0
    assert windows.action_window(3).tolist()==[2,2]
    assert not windows.probabilities.flags.writeable


def test_autoregression_reads_only_previous_label_and_inference_never_labels():
    torch.manual_seed(3)
    model=LabelForecast(8);gru=torch.nn.GRUCell(19,8)
    x=torch.randn(2,8)
    p=torch.zeros(2,5,4);p[:,:,1]=1
    a=model(x,gru,previous_targets=p)
    changed=p.clone();changed[:,2]=torch.tensor([1.,0,0,0])
    b=model(x,gru,previous_targets=changed)
    torch.testing.assert_close(a[:,:3],b[:,:3],rtol=0,atol=0)
    assert not torch.equal(a[:,3],b[:,3])
    free=model(x,gru)
    torch.testing.assert_close(free,model(x,gru),rtol=0,atol=0)
    loss=forecast_loss(a,p).mean();loss.backward()
    assert gru.weight_hh.grad.abs().sum()>0 and torch.isfinite(gru.weight_ih.grad).all()


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_actual_training_accepts_and_supervises_held_forecast_branch(device):
    policy,session,labels=fixture(device)
    held=[]
    for item in labels[1:]:
        p=np.tile(np.array([[0,0,.1,.9]],np.float32),(len(item.forecast_close_us),1))
        held.append(replace(item,token=3,held_index=np.array([0],np.int64),held_features=np.zeros((1,11),np.float32),
            enter_allowed=np.zeros(2,bool),exit_allowed=np.ones(1,bool),stop_allowed=np.zeros(1,bool),target_allowed=np.zeros(1,bool),
            soft_tokens=(3,6),soft_probabilities=(.9,.1),allocation_ratio_target=None,
            forecast_probabilities=p,forecast_actions=np.full(len(p),3,np.int64)))
    before=policy.action_gru.weight_hh.detach().clone()
    result=train_session(policy,torch.optim.Adam(policy.parameters(),lr=.001),session,tuple(held),(),device=torch.device(device),teacher_loss='balanced-v2')
    assert result.forecast_targets==(11,10,9,8,7)
    assert not torch.equal(before,policy.action_gru.weight_hh)


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_real_training_core_updates_shared_gru_size_and_encoder_and_reloads(device):
    policy,session,labels=fixture(device)
    before={k:v.detach().clone() for k,v in policy.state_dict().items()}
    optimizer=torch.optim.Adam(policy.parameters(),lr=.001)
    result=train_session(policy,optimizer,session,labels,(),device=torch.device(device),
        clocks_per_chunk=4,teacher_loss='balanced-v2')
    assert result.allocation_targets==12 and result.allocation_ratio_mae is not None
    assert result.forecast_targets==(12,11,10,9,8)
    assert all(np.isfinite(result.forecast_cross_entropy))
    for name in ('action_gru.weight_ih','action_gru.weight_hh','decoder.size_head.weight','encoder.project.weight'):
        assert not torch.equal(before[name],policy.state_dict()[name]),name
    restored,_,_=fixture(device);restored.load_state_dict(policy.state_dict())
    evaluation=replace(session,role='development')
    a=train_session(policy,None,evaluation,labels,(),device=torch.device(device),evaluation=True)
    b=train_session(restored,None,evaluation,labels,(),device=torch.device(device),evaluation=True)
    assert a==b and a.optimizer_steps==0
    continued=torch.optim.Adam(restored.parameters(),lr=.001)
    continued.load_state_dict(copy.deepcopy(optimizer.state_dict()))
    first=train_session(policy,optimizer,session,labels,(),device=torch.device(device),clocks_per_chunk=4,teacher_loss='balanced-v2')
    second=train_session(restored,continued,session,labels,(),device=torch.device(device),clocks_per_chunk=4,teacher_loss='balanced-v2')
    assert first==second
    for name,value in policy.state_dict().items():
        torch.testing.assert_close(value,restored.state_dict()[name],rtol=0,atol=0)


def test_first_target_uses_no_current_features_and_eval_changes_no_parameters():
    policy,session,labels=fixture()
    snapshots=[];original=policy.teacher_forecast.forward
    def capture(context,*args,**kwargs):
        snapshots.append(context.detach().clone());return original(context,*args,**kwargs)
    policy.teacher_forecast.forward=capture
    development=replace(session,role='development')
    weights={k:v.clone() for k,v in policy.state_dict().items()}
    train_session(policy,None,development,labels,(),device=torch.device('cpu'),evaluation=True)
    first=snapshots[0];second=snapshots[1];snapshots.clear()
    session.bank.scalar[0,0]=100
    train_session(policy,None,development,labels,(),device=torch.device('cpu'),evaluation=True)
    torch.testing.assert_close(first,snapshots[0],rtol=0,atol=0)
    assert not torch.equal(second,snapshots[1])
    assert all(torch.equal(v,policy.state_dict()[k]) for k,v in weights.items())


@pytest.mark.parametrize('bad',[float('nan'),-.1,1.1])
def test_invalid_ratios_fail_closed(bad):
    with pytest.raises(ValueError,match='ratio'):
        allocation_loss(torch.tensor(.5),bad)


def test_sealed_roles_and_bad_forecast_clocks_fail_closed():
    policy,session,labels=fixture()
    for role in ('test','sealed_test','heldout'):
        with pytest.raises(ValueError):
            train_session(policy,None,replace(session,role=role),labels,(),device=torch.device('cpu'),evaluation=True)
    broken=(replace(labels[0],forecast_close_us=labels[0].forecast_close_us+1),)+labels[1:]
    with pytest.raises(ValueError,match='forecast target'):
        train_session(policy,None,replace(session,role='development'),broken,(),device=torch.device('cpu'),evaluation=True)


def test_full_market_action_scope_does_not_depend_on_attention_rank():
    policy,session,labels=fixture()
    train_session(policy,None,replace(session,role='development'),labels,(),device=torch.device('cpu'),evaluation=True)
    policy.independent_episode_supervision=False
    masks=dict(enter_allowed=torch.ones(2,dtype=torch.bool),exit_allowed=torch.empty(0,dtype=torch.bool),
        stop_allowed=torch.empty(0,dtype=torch.bool),target_allowed=torch.empty(0,dtype=torch.bool))
    _,actual=policy.prepare_market(policy.candle_state.embeddings(),torch.empty(0,dtype=torch.long),masks)
    assert actual['enter_allowed'].all()
    policy.decide(policy.candle_state.embeddings(),torch.zeros(7),torch.empty(0,dtype=torch.long),
        torch.empty(0,11),policy.initial_action_state(device=torch.device('cpu'),dtype=torch.float32),**masks)
    forecasts=policy.forecast_labels()
    assert forecasts.shape==(2,5,4)
    torch.testing.assert_close(forecasts.sum(-1),torch.ones(2,5))


def test_probe_rekeys_only_declared_subset_and_preserves_targets():
    from research.rl_trading.v6.probe_teacher_sequence import subset
    _,session,labels=fixture()
    packed,selected=subset(session,labels,['A'],3_000_000,7_000_000)
    assert packed.listings==('A',) and len(selected)==5
    assert len(packed.bank.close_us)==7
    assert selected[0].forecast_close_us.tolist()==labels[2].forecast_close_us.tolist()
    assert selected[0].allocation_ratio_target==.25
    assert all(d.enter_allowed.shape==(1,) and d.soft_tokens==(0,1) for d in selected)


def test_forecast_label_metrics_count_real_targets_per_horizon():
    policy,session,labels=fixture()
    metrics=train_session(policy,None,replace(session,role='development'),labels,(),
                          device=torch.device('cpu'),evaluation=True)
    assert len(metrics.forecast_label_metrics)==5
    for report,count in zip(metrics.forecast_label_metrics,metrics.forecast_targets):
        assert sum(v['count'] for v in report['labels'].values())==count
        assert np.asarray(report['confusion']).sum()==count
        assert set(report['labels'])=={'ENTRY','WAIT','HOLD','EXIT'}
