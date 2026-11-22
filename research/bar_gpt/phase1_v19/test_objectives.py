import torch
from .objectives import relative_activity_loss


def test_relative_inverse_identity_zero_and_large_valid_values():
    y=torch.tensor([0.,1e-9,1.,1e6,1e15],dtype=torch.float64)[None,:,None].expand(1,5,18).clone()
    valid=torch.ones_like(y,dtype=torch.bool)
    assert relative_activity_loss(y.log1p(),y,valid)==0
    p=y*2+1;code=p.log1p().requires_grad_()
    # Every ratio (p-y)/(1+y) equals1, Huber(beta=.1) gives.95.
    torch.testing.assert_close(relative_activity_loss(code,y,valid),torch.tensor(.95,dtype=torch.float64))
    relative_activity_loss(code,y,valid).backward();assert torch.isfinite(code.grad).all()


def test_invalid_values_excluded_before_exponential():
    y=torch.ones(2,64,18,dtype=torch.float64);valid=torch.ones_like(y,dtype=torch.bool)
    code=y.log1p();valid[:,10]=False;code[:,10]=1e9;y[:,10]=float('nan')
    assert relative_activity_loss(code,y,valid)==0
    valid.zero_();assert relative_activity_loss(code,y,valid)==0


def test_rare_overshoot_has_correct_strong_gradient():
    y=torch.zeros(1,64,18,dtype=torch.float64);valid=torch.ones_like(y,dtype=torch.bool)
    code=torch.zeros_like(y);code[0,40,10]=20.;code.requires_grad_()
    loss=relative_activity_loss(code,y,valid);loss.backward()
    expected=torch.tensor(20.,dtype=torch.float64).exp()/(64*6)
    torch.testing.assert_close(code.grad[0,40,10],expected)
    assert torch.isfinite(loss) and loss>1e6
def test_price_relative_exact_inverse_and_invalid_mask():
    import torch
    from .objectives import relative_price_loss
    raw=torch.full((1,2,18),.001,dtype=torch.float64)
    valid=torch.ones_like(raw,dtype=torch.bool)
    logs=(2*raw).log().requires_grad_()
    loss=relative_price_loss(logs,raw,valid)
    torch.testing.assert_close(loss,torch.tensor(.95,dtype=torch.float64))
    loss.backward();assert torch.isfinite(logs.grad).all()
    valid[:]=False
    assert relative_price_loss(logs,raw*float('nan'),valid)==0


def test_visible_symmetry_without_hidden_underprediction_explosion():
    raw=torch.full((1,2,18),99.,dtype=torch.float64)
    valid=torch.ones_like(raw,dtype=torch.bool);visible=torch.tensor([[True,False]])
    under=torch.full_like(raw,50.).log();over=torch.full_like(raw,200.).log()
    all_visible=torch.ones_like(visible)
    torch.testing.assert_close(relative_activity_loss(under,raw,valid,all_visible),relative_activity_loss(over,raw,valid,all_visible))
    torch.testing.assert_close(relative_activity_loss(under,raw,valid,visible),torch.tensor(.7,dtype=torch.float64))


def test_wick_objective_uses_only_observed_price_triplets():
    from .objectives import wick_loss
    raw=torch.ones(1,2,18,dtype=torch.float64)
    valid=torch.ones_like(raw,dtype=torch.bool);known=torch.ones(1,2,dtype=torch.bool)
    code=torch.zeros_like(raw,requires_grad=True)
    assert wick_loss(code,raw,valid,known)==0
    for b in (0,6,12):raw[...,b+1]=2;raw[...,b+2]=.5
    loss=wick_loss(code,raw,valid,known);loss.backward()
    assert loss>0 and torch.isfinite(code.grad).all()
    valid.zero_();assert wick_loss(code,raw*float('nan'),valid,known)==0

