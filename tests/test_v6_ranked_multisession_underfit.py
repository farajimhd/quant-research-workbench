from dataclasses import replace, asdict
import copy
import numpy as np
import pytest
import torch
from research.rl_trading.v6.run_ranked_multisession_underfit import selected_targets, verify_coverage
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.run_ranked_teacher_underfit import passes
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.temporal_encoders import replace_encoder
from research.rl_trading.v6.teacher_forecast import configure
from research.rl_trading.v6.training import train_session
from test_rl_trading_v6_teacher_forecast import fixture


@pytest.mark.parametrize('checkpointing',[False,True])
def test_capacity512_actual_training_and_checkpoint_replay(checkpointing):
    from research.rl_trading.v6.run_ranked_teacher_generalization import build_policy
    from research.rl_trading.v6.market_attention import MarketAttentionConfig
    torch.set_num_threads(2)
    _,session,labels=fixture('cpu')
    labels=tuple(replace(d,forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for d in labels)
    ranking=MarketAttentionConfig(top_r=1,market_tokens=2,heads=2)
    policy=build_policy(ranking,torch.device('cpu'),width=512)
    policy.encoder.activation_checkpointing=checkpointing
    optimizer=torch.optim.AdamW(policy.parameters(),lr=.001)
    fitted=train_session(policy,optimizer,session,labels,(),device=torch.device('cpu'),teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))
    assert fitted.optimizer_steps>0 and np.isfinite(fitted.mean_loss)
    restored=build_policy(ranking,torch.device('cpu'),width=512)
    restored.encoder.activation_checkpointing=checkpointing
    restored.load_state_dict(policy.state_dict(),strict=True)
    def evaluate(p):
        return asdict(train_session(p,None,session,labels,(),device=torch.device('cpu'),evaluation=True,evaluate_train=True,teacher_loss='branch-balanced-v3',regression_weights=(0.,0.)))
    assert evaluate(policy)==evaluate(restored)


def test_checkpointing_preserves_temporal_outputs_gradients_and_update():
    from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
    torch.set_num_threads(2);torch.manual_seed(71)
    baseline=TemporalCandleEncoder(16);checked=copy.deepcopy(baseline)
    checked.activation_checkpointing=True
    history=torch.randn(3,120,16);present=torch.ones(3,120,dtype=torch.bool)
    present[0,:30]=False;present[2]=False
    a=history.clone().requires_grad_();b=history.clone().requires_grad_()
    first=baseline.encode_history(a,present);second=checked.encode_history(b,present)
    assert torch.equal(first,second)
    first.square().sum().backward();second.square().sum().backward()
    assert torch.equal(a.grad,b.grad)
    for x,y in zip(baseline.parameters(),checked.parameters()):
        assert (x.grad is None)==(y.grad is None)
        if x.grad is not None:assert torch.equal(x.grad,y.grad)
    torch.optim.AdamW(baseline.parameters(),lr=.001).step()
    torch.optim.AdamW(checked.parameters(),lr=.001).step()
    assert all(torch.equal(v,checked.state_dict()[k]) for k,v in baseline.state_dict().items())


def test_day_cache_and_unique_target_binding():
    _,s,t = fixture()
    d=t[0]; key=[d.close_us,d.episode_uid,bool(len(d.held_index))]
    plan=dict(rows=[dict(day='train-day',cache_sha256='receipt',key=key)])
    assert len(selected_targets(plan,'train-day','receipt',t)) == 1
    with pytest.raises(ValueError,match='binding'):
        selected_targets(plan,'train-day','different',t)
    with pytest.raises(ValueError,match='binding'):
        selected_targets(plan,'other-day','receipt',t)
    plan['rows'].append(copy.deepcopy(plan['rows'][0]))
    with pytest.raises(ValueError,match='identity'):
        selected_targets(plan,'train-day','receipt',t)


def test_missing_held_labels_cannot_pass_pool_preflight():
    _,s,t=fixture()
    t=tuple(replace(d,forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for d in t)
    with pytest.raises(ValueError,match='coverage'):
        verify_coverage(dict(current_counts=[32]*4,future_counts=[[32]*4]*5),[(s,t)])


def test_actual_cpu_training_resets_market_axes_across_sessions_and_replays():
    torch.set_num_threads(2)
    policy,session,labels=fixture('cpu')
    replace_encoder(policy,'tcn',structured=True);configure(policy,hierarchical=True,shared_heads=False)
    policy.independent_episode_supervision=True
    labels=tuple(replace(d,forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for d in labels)
    full,t=subset(session,labels,session.listings,3_000_000,7_000_000)
    one,u=subset(session,labels,('A',),3_000_000,7_000_000)
    optimizer=torch.optim.AdamW(policy.parameters(),lr=.001)
    def evaluate():
        reports=[]
        for s,ds in ((full,t),(one,u)):
            evidence={}
            r=asdict(train_session(policy,None,s,ds,(),device=torch.device('cpu'),evaluation=True,
                evaluate_train=True,teacher_loss='branch-balanced-v3',regression_weights=(0.,0.),
                regression_evidence=evidence))
            r.update(evidence);reports.append(r)
        return reports
    for s,ds in ((full,t),(one,u)):
        train_session(policy,optimizer,s,ds,(),device=torch.device('cpu'),teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))
        assert len(policy.ranker.seen)==len(s.listings)
    first=evaluate(); second=evaluate()
    assert first==second
    for r in first:
        assert r['allocation_error_sum']/r['allocation_weight']==r['allocation_ratio_mae']
    pooled=pool_gate_metrics(first)
    assert pooled['allocation_targets']==10
    assert not passes(pooled) # Missing held classes never establish success.


def test_exporting_regression_evidence_preserves_training_and_weights():
    torch.set_num_threads(2)
    policy,session,labels=fixture('cpu')
    replace_encoder(policy,'tcn',structured=True);configure(policy,hierarchical=True,shared_heads=False)
    policy.independent_episode_supervision=True
    labels=tuple(replace(d,sample_weight=.25 if i%2 else .75,
        forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for i,d in enumerate(labels))
    session,labels=subset(session,labels,session.listings,3_000_000,7_000_000)
    exported=copy.deepcopy(policy)
    a=torch.optim.AdamW(policy.parameters(),lr=.001)
    b=torch.optim.AdamW(exported.parameters(),lr=.001)
    args=dict(device=torch.device('cpu'),teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))
    torch.manual_seed(123)
    baseline=train_session(policy,a,session,labels,(),**args)
    evidence={};torch.manual_seed(123)
    actual=train_session(exported,b,session,labels,(),regression_evidence=evidence,**args)
    assert baseline==actual
    assert policy.state_dict().keys()==exported.state_dict().keys()
    assert all(torch.equal(v,exported.state_dict()[k]) for k,v in policy.state_dict().items())
    assert evidence['allocation_weight']==sum(d.sample_weight for d in labels if d.allocation_ratio_target is not None)
    assert evidence['allocation_error_sum']/evidence['allocation_weight']==actual.allocation_ratio_mae


def test_resource_pacing_preserves_real_training_and_evaluation():
    torch.set_num_threads(2)
    policy,session,labels=fixture('cpu')
    replace_encoder(policy,'tcn',structured=True);configure(policy,hierarchical=True,shared_heads=False)
    policy.independent_episode_supervision=True
    labels=tuple(replace(d,forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for d in labels)
    session,labels=subset(session,labels,session.listings,3_000_000,7_000_000)
    paced=copy.deepcopy(policy);calls=[]
    paced.resource_pacer=lambda:calls.append(True)
    args=dict(device=torch.device('cpu'),teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))
    a=torch.optim.AdamW(policy.parameters(),lr=.001)
    b=torch.optim.AdamW(paced.parameters(),lr=.001)
    torch.manual_seed(123);baseline=train_session(policy,a,session,labels,(),**args)
    torch.manual_seed(123);actual=train_session(paced,b,session,labels,(),**args)
    assert calls and baseline==actual
    assert all(torch.equal(v,paced.state_dict()[k]) for k,v in policy.state_dict().items())
    calls.clear()
    first=train_session(policy,None,session,labels,(),evaluation=True,evaluate_train=True,**args)
    second=train_session(paced,None,session,labels,(),evaluation=True,evaluate_train=True,**args)
    assert calls and first==second
