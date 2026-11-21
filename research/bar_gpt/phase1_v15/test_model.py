import torch
from .model import Autoencoder
from ..phase1_v12.model import Autoencoder as Original,Config,positions


def test_training_parity_and_duplicate_invariance():
    c=Config(width=32,layers=2,heads=2,precision='fp32')
    torch.manual_seed(17);base=Original(c,[0.]*16,[1.]*16)
    m=Autoencoder(c,[0.]*16,[1.]*16);m.load_state_dict(base.state_dict())
    z=torch.randn(4,64,16);meta=torch.randn(4,64,36);p=positions(4,'cpu')
    a=base(z,meta,p);b=m(z,meta,p)
    torch.testing.assert_close(a,b,atol=0,rtol=0)
    a.square().mean().backward();b.square().mean().backward()
    for x,y in zip(base.parameters(),m.parameters()):torch.testing.assert_close(x.grad,y.grad,atol=0,rtol=0)
    duplicated=p.repeat_interleave(4,dim=1)
    torch.testing.assert_close(m(z,meta,p),m(z,meta,duplicated),atol=1e-6,rtol=1e-5)
    assert (base(z,meta,p)-base(z,meta,duplicated)).abs().max()>1e-6


def test_stable_duplicate_invariance_and_hidden_isolation():
    c=Config(width=32,layers=2,heads=2,precision='fp32');m=Autoencoder(c,[0.]*16,[1.]*16,'stable_balanced')
    z=torch.randn(4,64,16);meta=torch.randn(4,64,36);p=positions(4,'cpu')
    known=torch.zeros(4,64,dtype=torch.bool).scatter(1,p,True)
    torch.testing.assert_close(m(z,meta,p),m(torch.where(known[...,None],z,z+100),meta,p))
    torch.testing.assert_close(m(z,meta,p),m(z,meta,p.repeat_interleave(4,dim=1)),atol=1e-6,rtol=1e-5)
