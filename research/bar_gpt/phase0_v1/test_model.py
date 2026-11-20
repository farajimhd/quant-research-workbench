import torch
from .model import Autoencoder,objective
from ..phase1_v11.test_diagnostic_metrics import fixture
from ..phase1_v11.transforms import inverse


def test_independent_bars_embedding_only_and_gradients():
    _,logs,v,_,_,_=fixture();x=torch.cat((logs.float(),torch.zeros(2,64,9)),-1);valid=torch.cat((v,torch.ones(2,64,9,dtype=torch.bool)),-1)
    model=Autoencoder(dict(mean=[0.]*27,scale=[1.]*27));model.eval()
    z=model.encode(x,valid);assert z.shape==(2,64,16)
    out=model(x,valid);torch.testing.assert_close(model.decode(z)['logs'],out['logs'])
    changed=x.clone();changed[:,1:]+=10
    torch.testing.assert_close(model(changed,valid)['logs'][:,0],out['logs'][:,0])
    loss,_=objective(model,out,x,valid);loss.backward();assert all(torch.isfinite(p.grad).all() for p in model.parameters())


def test_flat_bar_roundtrip_and_source_mask():
    model=Autoencoder(dict(mean=[0.]*27,scale=[1.]*27))
    with torch.no_grad():model.decoder.weight.zero_();model.decoder.bias.zero_();model.decoder.bias[[0,6,12]]=torch.log(torch.tensor(10.))
    out=model.decode(torch.zeros(1,1,16));raw=inverse(out['logs'])
    assert torch.equal(raw[...,0],raw[...,3]);assert torch.equal(raw[...,1],raw[...,2]);assert (raw[...,4]==0).all()
    x=torch.cat((out['logs'].detach(),torch.zeros(1,1,9)),-1);v=torch.ones_like(x,dtype=torch.bool);v[...,4]=False
    loss,_=objective(model,out,x,v);x[...,4]=100000
    other,_=objective(model,out,x,v);torch.testing.assert_close(loss,other)
