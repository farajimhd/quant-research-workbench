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
