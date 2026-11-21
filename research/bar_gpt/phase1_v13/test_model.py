import torch
from .model import Autoencoder, StableAttention
from ..phase1_v12.model import Config, positions, objective


def test_stability_and_hidden_isolation():
    torch.manual_seed(17)
    m = Autoencoder(Config(width=32,layers=1,heads=2,precision='fp32'), [0.]*16,[1.]*16)
    z=torch.randn(4,64,16);meta=torch.randn(4,64,36);p=positions(4,'cpu')
    known=torch.zeros(4,64,dtype=torch.bool).scatter(1,p,True)
    a=m(z,meta,p)
    torch.testing.assert_close(a,m(torch.where(known[...,None],z,z+100),meta,p))
    loss,_=objective(m,a,z,torch.ones_like(known),p);loss.backward()
    assert all(x.grad is not None and torch.isfinite(x.grad).all() for x in m.parameters())
    b=StableAttention(32,2)
    with torch.no_grad():b.qkv.weight[:64].mul_(10000);b.qkv.bias[:64].mul_(10000)
    x=torch.randn(4,17,32,requires_grad=True);b(x).square().mean().backward()
    assert torch.isfinite(x.grad).all()
