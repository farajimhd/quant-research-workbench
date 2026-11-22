import copy
import torch
from ..phase1_v20.test_model import build
from .model import initialize,verify_protected
from .objectives import loss


def test_protected_rows_and_finite_masked_gradients():
    torch.set_num_threads(2);model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    for arm in ('range','both_log','both_symmetric','metric','metric_log'):
        m=initialize(None,saved,None,arm);opt=torch.optim.AdamW(m.decoder.parameters(),lr=1e-5,weight_decay=0)
        raw=torch.ones(2,64,27);raw[...,1]=1.1;raw[...,2]=.9
        values=raw.log();valid=torch.ones_like(raw,dtype=torch.bool);pos=torch.arange(16).expand(2,-1)
        for _ in range(2):
            opt.zero_grad(set_to_none=True);value,_=loss(m,values,valid,raw,torch.ones(2),pos,None,None);value.backward()
            assert torch.isfinite(value) and all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)
            opt.step()
        verify_protected(m,saved)
        assert all(p.grad is None for p in m.bar.parameters())
        value,_=loss(m,values,torch.zeros_like(valid),raw,torch.ones(2),pos,None,None)
        assert value==0


def test_joint_control_matches_parent_and_metric_trains_encoders():
    from ..phase1_v20.objectives import loss as parent_loss
    model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    values=torch.rand(2,64,27);raw=values.exp();valid=torch.ones_like(values,dtype=torch.bool)
    pos=torch.arange(16).expand(2,-1);old=torch.zeros(2,64,16);scale=torch.ones(6,6);frames=torch.ones(2)
    control=initialize(None,saved,None,'joint_control')
    actual,_=loss(control,values,valid,raw,frames,pos,old,scale)
    expected,_=parent_loss(control,values,valid,raw,frames,pos,old,scale)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    metric=initialize(None,saved,None,'joint_metric');value,_=loss(metric,values,valid,raw,frames,pos,old,scale);value.backward()
    for module in (metric.bar,metric.temporal,metric.decoder):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in module.parameters())
