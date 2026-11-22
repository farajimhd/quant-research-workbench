import copy
import torch
from ..phase1_v20.test_model import build
from .model import initialize,verify_protected
from .objectives import loss


def test_protected_rows_and_finite_masked_gradients():
    torch.set_num_threads(2);model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    for arm in ('range','both_log','both_symmetric','metric','metric_log','body_direction'):
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
    for arm in ('joint_metric_log','joint_repaired','joint_separated'):
        metric=initialize(None,saved,None,arm);value,_=loss(metric,values,valid,raw,frames,pos,old,scale);value.backward()
        assert torch.isfinite(value)
        for module in (metric.bar,metric.temporal,metric.decoder):
            assert any(p.grad is not None and p.grad.abs().sum()>0 for p in module.parameters())


def test_loss_routes_change_gradients_not_predictions_or_loss_values():
    from .objectives import routed_output
    m,teacher,_=build();out=m.decode(torch.randn(2,m.decoder.in_features))
    level=routed_output(out,(0,4,5));torch.testing.assert_close(level['logs'],out['logs'],rtol=0,atol=0)
    level['logs'].sum().backward();g=m.decoder.weight.grad.reshape(64,18,-1)
    assert g[:,[1,2,3,7,8,9,13,14,15]].count_nonzero()==0
    assert g[:,[0,6,12]].abs().sum()>0
    saved=dict(model=copy.deepcopy(m.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(m.temporal.config),input_calibration=None))
    x=torch.rand(2,64,27);raw=x.exp();v=torch.ones_like(x,dtype=torch.bool);pos=torch.arange(16).expand(2,-1)
    args=(x,v,raw,torch.ones(2),pos,torch.zeros(2,64,16),torch.ones(6,6))
    normal=initialize(None,saved,None,'joint_repaired');separated=initialize(None,saved,None,'joint_separated')
    expected,_=loss(normal,*args);actual,_=loss(separated,*args)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)


def test_selective_head_has_exact_channel_isolation():
    from .selective import combine
    m,_,_=build();p=dict(model=copy.deepcopy(m.state_dict()));c=copy.deepcopy(p)
    c['model']['decoder.weight']+=.01;c['model']['decoder.bias']+=.02
    result=combine(p,c);rows=torch.arange(1152)%18==4
    for name in ('decoder.weight','decoder.bias'):
        torch.testing.assert_close(result['model'][name][rows],p['model'][name][rows],rtol=0,atol=0)
        torch.testing.assert_close(result['model'][name][~rows],c['model'][name][~rows],rtol=0,atol=0)


def test_prefloor_changes_only_auxiliary_activity_losses():
    model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    saved['model']['decoder.weight'][4].zero_()
    saved['model']['decoder.bias'][4]=-100.
    baseline=initialize(None,saved,None,'joint_repaired')
    candidate=initialize(None,saved,None,'joint_prefloor')
    x=torch.rand(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
    args=(x,v,x.exp(),torch.ones(2),torch.arange(16).expand(2,-1),torch.zeros(2,64,16),torch.ones(6,6))
    for key in baseline(x,v,args[4])[2]:
        torch.testing.assert_close(baseline(x,v,args[4])[2][key],candidate(x,v,args[4])[2][key],rtol=0,atol=0)
    _,old=loss(baseline,*args);value,new=loss(candidate,*args)
    assert old['activity']!=new['activity']
    for key in ('range','wick','range_bins','joint_base','candle_direction'):
        torch.testing.assert_close(old[key],new[key],rtol=0,atol=0)
    value.backward()
    assert all(torch.isfinite(p.grad).all() for p in candidate.parameters() if p.grad is not None)
    # All-invalid entries cannot contribute even to the pre-floor auxiliary loss.
    invalid=list(args);invalid[1]=torch.zeros_like(v)
    _,parts=loss(candidate,*invalid)
    assert parts['activity']==0 and parts['activity_direction']==0


def test_activity_weight_changes_only_declared_loss_family():
    model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    m=initialize(None,saved,None,'joint_repaired')
    x=torch.rand(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
    args=(x,v,x.exp(),torch.ones(2),torch.arange(16).expand(2,-1),torch.zeros(2,64,16),torch.ones(6,6))
    old,parts=loss(m,*args);m.activity_weight=10.
    new,_=loss(m,*args)
    torch.testing.assert_close(new-old,9*(parts['activity']+.1*parts['activity_direction']))
    new.backward()
    assert all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)


def test_activity_frame_balance_preserves_other_objectives():
    model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    m=initialize(None,saved,None,'joint_repaired');x=torch.rand(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
    args=(x,v,x.exp(),torch.tensor([1,2]),torch.arange(16).expand(2,-1),torch.zeros(2,64,16),torch.ones(6,6))
    original,old=loss(m,*args)
    m.register_buffer('activity_frame_codes',torch.tensor([1,2]),persistent=False)
    m.register_buffer('activity_frame_weights',torch.ones(2),persistent=False)
    same,_=loss(m,*args);torch.testing.assert_close(same,original)
    m.activity_frame_weights.copy_(torch.tensor([.5,2.]));weighted,new=loss(m,*args)
    for k in ('range','wick','range_bins','joint_base','candle_direction'):torch.testing.assert_close(new[k],old[k],rtol=0,atol=0)
    weighted.backward();assert torch.isfinite(weighted)
    assert 'activity_frame_weights' not in m.state_dict()
