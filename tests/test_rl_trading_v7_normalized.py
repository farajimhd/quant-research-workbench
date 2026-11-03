import numpy as np
import torch
from research.rl_trading.v7.model import Policy
from research.rl_trading.v7.experiments.normalized import NormalizedPolicy


def test_scaling_preserves_masks_and_finite_gradient():
    torch.set_num_threads(1)
    torch.manual_seed(1917)
    base=Policy(width=16,heads=4)
    torch.manual_seed(1917)
    model=NormalizedPolicy(width=16,heads=4)
    assert all(torch.equal(v,model.state_dict()[k]) for k,v in base.state_dict().items())
    raw=torch.zeros(2,5,98);groups=raw.reshape(2,5,7,14)
    groups[...,:3]=.01;groups[...,3:5]=10.;groups[...,5:]=1.
    observed=[]
    hook=model.frame[0].register_forward_pre_hook(lambda m,args:observed.append(args[0].detach().clone()))
    mask=torch.ones(2,7,dtype=torch.bool)
    dist,value=model(raw,torch.zeros(2,8),torch.zeros(2,8),torch.ones(2,5,dtype=torch.bool),mask)
    hook.remove()
    assert torch.allclose(observed[0],torch.ones_like(observed[0]))
    (value.square().mean()-dist.log_prob(torch.tensor([1,6])).mean()).backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
