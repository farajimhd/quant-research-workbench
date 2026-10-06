import numpy as np
import pytest
import torch
from research.rl_trading.v6.run_hierarchy_bias_campaign import product_logit,HierarchyTeacher,factor_epoch
from research.rl_trading.v6.run_bias_campaign import tensors


def test_underfit_gate_rejects_any_collapsed_action_or_hierarchy_head():
    from research.rl_trading.v6.run_hierarchy_bias_campaign import underfit_passes
    actions={name:{'f1':.96} for name in ('ENTRY','WAIT','HOLD','EXIT')}
    assert underfit_passes(actions,{'timing':{'f1':.95}})
    actions['ENTRY']['f1']=0
    assert not underfit_passes(actions)
    actions['ENTRY']['f1']=.96
    assert not underfit_passes(actions,{'timing':{'f1':.94}})


def test_joint_entry_probability_is_product_with_finite_extreme_gradients():
    timing=torch.tensor([-1000.,-2.,0.,2.,1000.],requires_grad=True)
    gate=torch.tensor([0.,2.,0.,-2.,1000.],requires_grad=True)
    logit=product_logit(timing,gate)
    torch.testing.assert_close(logit.sigmoid(),timing.sigmoid()*gate.sigmoid(),atol=2e-7,rtol=1e-6)
    torch.nn.functional.binary_cross_entropy_with_logits(logit,torch.ones(5)).backward()
    assert torch.isfinite(logit).all() and torch.isfinite(timing.grad).all() and torch.isfinite(gate.grad).all()


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_real_factor_optimizer_updates_timing_gate_exit_without_other_auxiliary(device):
    torch.set_num_threads(2);torch.manual_seed(17);n=16
    action=np.tile(np.arange(4),4);held=np.zeros((n,11),np.float32);held[action>=2,0]=1
    source=dict(features=np.random.default_rng(17).normal(size=(n+1,147)).astype(np.float32),
        windows=np.arange(1,n+1)[:,None].repeat(120,axis=1),market=np.zeros((n,147),np.float32),held=held,
        action=action,weight=np.ones(n,np.float32),future=np.full((n,5),-1),quality=np.zeros(n,np.float32),
        ratio=np.full(n,np.nan,np.float32),episode=['fixture']*n)
    data=tensors(source,dict(mean=[0.]*147,std=[1.]*147),device)
    eligible=action!=1;eligible[1]=True
    data.update(entry_1a=torch.as_tensor((action==0)|(action==3),device=device),episode_selected=torch.as_tensor(eligible,device=device))
    model=HierarchyTeacher('tcn',width=8).to(device);before={k:v.detach().clone() for k,v in model.named_parameters()}
    result=factor_epoch(model,data,torch.optim.AdamW(model.parameters(),lr=.001),epoch=1,batch_size=16)
    assert result['updates']==1 and not result['auxiliary_size_quality_future_supervised']
    for name in ('timing.weight','eligibility.weight','heads.exit.weight'):
        assert not torch.equal(before[name],dict(model.named_parameters())[name])
    for name,value in model.named_parameters():
        if name.startswith(('size.','forecast.','forecast_gru.','heads.quality.')):torch.testing.assert_close(value,before[name],atol=0,rtol=0)
