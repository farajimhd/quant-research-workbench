import torch
from .objectives import activity_terms, ACTIVITY
from .metrics import ActivityMetrics, scalars
from ..v5.shards.phase1_contract import FRAMES
from ..phase1_v5_raw.engine import FRAME_NAMES


def test_activity_sign_zero_and_invalid_pairs():
    raw=torch.zeros(1,4,18,dtype=torch.float64)
    raw[...,list(ACTIVITY)]=torch.tensor([0.,2.,1.,1.],dtype=torch.float64)[None,:,None]
    valid=torch.ones_like(raw,dtype=torch.bool)
    metric=ActivityMetrics('cpu');metric.add(raw.log1p(),raw,valid,torch.tensor([FRAMES[0]]))
    values=scalars(metric.result());p=FRAME_NAMES[0]+'/trade_volume/'
    assert values[p+'pairs']==3
    assert values[p+'direction_pct']==100
    assert values[p+'moving_direction_pct']==100
    assert values[p+'zero_targets']==1
    valid[:,1,4]=False
    metric=ActivityMetrics('cpu');metric.add(raw.log1p(),raw,valid,torch.tensor([FRAMES[0]]))
    values=scalars(metric.result());assert values[p+'pairs']==1
    # Missing middle candles do not create new adjacency.
    assert values[p+'flat_count']==1


def test_loss_masks_and_gradient_without_reference_inputs():
    raw=torch.ones(2,64,18,dtype=torch.float64)
    valid=torch.ones_like(raw,dtype=torch.bool);valid[:,5,4]=False
    known=torch.ones(2,64,dtype=torch.bool)
    pred=raw.log1p().float().requires_grad_()
    terms=activity_terms(pred,raw,valid,known)
    assert all(float(x)==0 for x in terms)
    other=raw.clone();other[:,5,4]=9999999
    for a,b in zip(terms,activity_terms(pred,other,valid,known)):torch.testing.assert_close(a,b)
    perturbed=pred+1
    level,delta,tail=activity_terms(perturbed,raw,valid,known)
    assert level>0 and tail>0 and delta==0
    (level+delta+tail).backward();assert torch.isfinite(pred.grad).all()


def test_magnitude_wrong_despite_perfect_direction():
    raw=torch.ones(1,4,18,dtype=torch.float64)
    raw[...,list(ACTIVITY)]=torch.arange(4,dtype=torch.float64)[None,:,None]
    pred=raw.log1p()+20
    metric=ActivityMetrics('cpu');metric.add(pred,raw,torch.ones_like(raw,dtype=torch.bool),torch.tensor([FRAMES[0]]))
    s=scalars(metric.result());p=FRAME_NAMES[0]+'/trade_volume/'
    assert s[p+'moving_direction_pct']==100
    assert s[p+'raw_mae']>1e8
    assert s[p+'overshoot_10x_count']==4
