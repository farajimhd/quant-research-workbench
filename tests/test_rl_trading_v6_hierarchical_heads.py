from dataclasses import asdict, replace
import numpy as np
import polars as pl
import pytest
import torch

from research.rl_trading.v6.teacher_forecast import configure, ForecastWindows
from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads, HierarchicalForecast, sequence_losses
from research.rl_trading.v6.training import train_session
from test_rl_trading_v6_teacher_forecast import fixture


def test_known_position_gates_actions_and_quality_is_independent():
    heads=HierarchicalTickerHeads(8)
    x=torch.randn(2,8);held=torch.tensor([False,True])
    before=heads(x,held)
    assert torch.isneginf(before.logits[0,2:]).all()
    assert torch.isneginf(before.logits[1,:2]).all()
    assert before.quality.shape==(2,2)
    with torch.no_grad():heads.quality.bias.add_(10)
    after=heads(x,held)
    torch.testing.assert_close(before.logits,after.logits,rtol=0,atol=0)
    assert not torch.equal(before.quality,after.quality)


def test_future_hierarchy_normalizes_and_reads_only_previous_labels():
    heads=HierarchicalTickerHeads(8);model=HierarchicalForecast(8,heads)
    assert model.entry is heads.entry and model.exit is heads.exit
    gru=torch.nn.GRUCell(19,8);x=torch.randn(2,8)
    p=torch.zeros(2,5,4);p[:,:,1]=1
    first=model(x,gru,previous_targets=p)
    changed=p.clone();changed[:,2]=torch.tensor([1.,0,0,0])
    second=model(x,gru,previous_targets=changed)
    torch.testing.assert_close(first.exp().sum(-1),torch.ones(2,5))
    torch.testing.assert_close(first[:,:3],second[:,:3],rtol=0,atol=0)
    assert not torch.equal(first[:,3],second[:,3])
    torch.testing.assert_close(model(x,gru),model(x,gru),rtol=0,atol=0)


def test_saved_action_is_not_inferred_from_low_quality_probability():
    frame=pl.DataFrame(dict(listing_id=['A'],time_us=[100],action=['ENTRY'],teacher_probabilities=[[.1,.9,0.,0.]]))
    windows=ForecastWindows.from_frame(frame)
    assert windows.action_window(0).tolist()==[0]
    assert windows.probabilities.argmax(-1).tolist()==[1]
    logits=torch.tensor([[[-.1,-3.,-4.,-5.]]],requires_grad=True)
    losses=sequence_losses(logits,torch.tensor([[[.1,.2]]]),torch.tensor([[0]]),torch.tensor([[[.1,.9,0.,0.]]]),torch.ones(4))
    assert losses[0].item()==pytest.approx(.1)
    assert losses[1].item()==0


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_real_core_trains_new_heads_and_exact_checkpoint_reload(device):
    policy,session,original=fixture(device)
    configure(policy,hierarchical=True)
    n=len(original);actions=np.arange(n,dtype=np.int64)%4
    p=np.zeros((n,4),np.float32)
    for i,a in enumerate(actions):
        p[i,a]=.8;p[i,{0:1,1:0,2:3,3:2}[int(a)]]=.2
    labels=tuple(replace(d,token=1 if actions[i]==0 else 0,
        allocation_ratio_target=.25 if actions[i]==0 else None,
        soft_probabilities=(.2,.8) if actions[i]==0 else (1.,0.),
        forecast_actions=actions[i:i+5],forecast_probabilities=p[i:i+5]) for i,d in enumerate(original))
    before={k:v.detach().clone() for k,v in policy.state_dict().items()}
    optimizer=torch.optim.Adam(policy.parameters(),lr=.001)
    metrics=train_session(policy,optimizer,session,labels,(),device=torch.device(device),clocks_per_chunk=4,teacher_loss='balanced-v2')
    for name in ('decoder.heads.entry.weight','decoder.heads.exit.weight','decoder.heads.quality.weight',
                 'teacher_forecast.reference_held.weight','action_gru.weight_hh','decoder.size_head.weight'):
        assert not torch.equal(before[name],policy.state_dict()[name]),name
    assert metrics.action_quality_counts['ENTRY']==3
    assert metrics.forecast_quality_counts[0]['EXIT']==3
    for report,count in zip(metrics.forecast_label_metrics,metrics.forecast_targets):
        assert sum(v['count'] for v in report['labels'].values())==count
    development=replace(session,role='development')
    expected=asdict(train_session(policy,None,development,labels,(),device=torch.device(device),evaluation=True))
    restored,_,_=fixture(device);configure(restored,hierarchical=True)
    restored.load_state_dict(policy.state_dict(),strict=True)
    actual=asdict(train_session(restored,None,development,labels,(),device=torch.device(device),evaluation=True))
    assert expected==actual
    assert len(list(policy.parameters()))==len({id(p) for p in policy.parameters()})
    with pytest.raises(RuntimeError):
        legacy,_,_=fixture(device);legacy.load_state_dict(policy.state_dict(),strict=True)


def test_hierarchy_rejects_missing_explicit_actions_and_sealed_roles():
    policy,session,labels=fixture();configure(policy,hierarchical=True)
    with pytest.raises(ValueError,match='explicit saved'):
        train_session(policy,None,replace(session,role='development'),labels,(),device=torch.device('cpu'),evaluation=True)
    with pytest.raises(ValueError):
        train_session(policy,None,replace(session,role='sealed_test'),labels,(),device=torch.device('cpu'),evaluation=True)
