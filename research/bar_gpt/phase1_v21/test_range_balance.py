import copy
import torch
from .range_balance import weights_from_counts,attach
from .model import initialize
from .objectives import loss
from ..phase1_v20.test_model import build


def test_weights_preserve_expected_mass_and_empty_bins():
    c=torch.tensor([[100,25,4,1,0,0]]*3)
    w=weights_from_counts(c)
    torch.testing.assert_close((w*c).sum(-1),c.sum(-1).double())
    assert (w[:,4:]==0).all() and w[0,3]>w[0,0]


def test_only_range_losses_change_and_initial_state_is_identical():
    torch.set_num_threads(2);m,t,_=build()
    saved=dict(model=copy.deepcopy(m.state_dict()),contract=dict(bar_config=vars(t.config),temporal_config=vars(m.temporal.config),input_calibration=None))
    a=initialize(None,saved,None,'joint_basis_prefloor_slow_temporal');b=initialize(None,saved,None,'joint_basis_prefloor_range_balanced')
    counts=torch.tensor([[100,25,4,1,2,3]]*3)
    attach(b,dict(counts=counts.tolist(),weights=weights_from_counts(counts).tolist()))
    for k,v in a.state_dict().items():torch.testing.assert_close(v,b.state_dict()[k],rtol=0,atol=0)
    x=torch.rand(2,64,27);raw=x.exp();raw[...,1::6]=2;raw[...,2::6]=1
    args=(x,torch.ones_like(x,dtype=torch.bool),raw,torch.ones(2),torch.arange(16).expand(2,-1),torch.zeros(2,64,16),torch.ones(6,6))
    _,old=loss(a,*args);total,new=loss(b,*args)
    for k in old:
        if k not in ('range','range_bins'):torch.testing.assert_close(old[k],new[k],rtol=0,atol=0)
    assert old['range']!=new['range']
    total.backward();assert all(torch.isfinite(p.grad).all() for p in b.parameters() if p.grad is not None)
