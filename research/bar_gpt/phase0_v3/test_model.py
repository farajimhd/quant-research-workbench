import torch
from .model import Autoencoder,relative_input
from ..phase0_v2.model import Config,Autoencoder as Previous


def test_reference_roundtrip_zero_tiny_and_large():
    q=torch.tensor([0.,1e-9,.01,1.,100.,1e12,1e30],dtype=torch.float64)
    for r in (.01,1.,100.,1e6):
        reference=torch.tensor(r,dtype=torch.float64)
        z=relative_input(q.log1p(),reference)
        torch.testing.assert_close(z.expm1()*r,q,rtol=1e-13,atol=1e-18)
        assert z[0]==0 and torch.isfinite(z).all()


def test_control_matches_teacher_and_decoder_has_no_reference_dependency():
    c=Config(width=4,layers=1,heads=1,pooling='flatten');p=dict(mean=torch.zeros(27),scale=torch.ones(27))
    base=Previous(p,c);m=Autoencoder(p,c);m.load_state_dict(base.state_dict(),strict=False)
    x=torch.rand(3,64,27);v=torch.ones_like(x,dtype=torch.bool)
    torch.testing.assert_close(m(x,v)['logs'],base(x,v)['logs'],rtol=0,atol=0)
    changed=Autoencoder(p,c,dict(reference=[100.]*6,mean=[0.]*6,scale=[1.]*6));changed.load_state_dict(base.state_dict(),strict=False)
    e=torch.randn(2,16)
    torch.testing.assert_close(changed.decode(e)['logs'],base.decode(e)['logs'],rtol=0,atol=0)
    out=changed(x,v);out['logs'].square().mean().backward()
    assert all(p.grad is None or torch.isfinite(p.grad).all() for p in changed.parameters())
