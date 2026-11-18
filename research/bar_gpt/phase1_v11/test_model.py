import torch
from .model import Config,Autoencoder,select,objective
from .transforms import coordinates,references,center,decode,inverse


def parameters():
    return dict(time_mean=[0.]*9,time_scale=[1.]*9,reference_mean=[4.6,5.,2.]*3,reference_scale=[2.]*9)


def example(n=3):
    # Positive prices across six orders of magnitude; exact dojis/zero activity.
    raw=torch.tensor([100.,105.,99.,102.,0.,3.,99.,104.,98.,101.,2.,0.,101.,106.,100.,103.,1.,0.],dtype=torch.float64).repeat(n,64,1)
    raw[1,:,:4]*=1e-6;raw[1,:,6:10]*=1e-6;raw[1,:,12:16]*=1e-6
    raw[2,:,:4]*=1e6;raw[2,:,6:10]*=1e6;raw[2,:,12:16]*=1e6
    logs=raw.clone()
    for b in (0,6,12):logs[...,b:b+4]=raw[...,b:b+4].log();logs[...,b+4:b+6]=raw[...,b+4:b+6].log1p()
    return raw,torch.cat((logs,torch.zeros(n,64,9,dtype=torch.float64)),-1),torch.ones(n,64,27,dtype=torch.bool)


def test_roundtrip_partial_quotes_and_zeros():
    raw,x,v=example()
    # Every nonempty subset of OHLC, never silently lose a valid observation.
    for pattern in range(16):
        v[:,pattern,6:10]=torch.tensor([(pattern>>k)&1 for k in range(4)],dtype=torch.bool)
    code,cm=coordinates(x[...,:18],v[...,:18]);ref,rv=references(x,v,torch.tensor(parameters()['reference_mean']))
    restored=inverse(decode(center(code,ref),ref))
    assert torch.allclose(restored[v[...,:18]],raw[v[...,:18]],rtol=1e-12,atol=1e-12)
    assert (restored[...,4]==0).all()


def test_even_median_valid_zero_and_fallback():
    from .transforms import median
    x=torch.tensor([[[0.,2.],[2.,9.],[4.,0.],[6.,0.]]]);v=torch.tensor([[[1,0],[1,0],[1,0],[1,0]]],dtype=torch.bool)
    m,a=median(x,v,torch.tensor([10.,11.]));assert torch.equal(m,torch.tensor([[3.,11.]]));assert a.tolist()==[[True,False]]


def test_hidden_targets_cannot_change_embedding_or_references():
    _,x,v=example();x=x.float();positions=torch.arange(0,64,4).expand(3,-1)
    a,va,pos,h=select(x,v,16,positions=positions);model=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0),parameters()).eval()
    z=model.encoder(a,va,pos);ref=model.encoder.prepare(a,va)[2]
    changed=x.clone();changed[h]=1234.;b,vb,_,_=select(changed,v,16,positions=positions)
    assert torch.equal(z,model.encoder(b,vb,pos));assert torch.equal(ref,model.encoder.prepare(b,vb)[2])
    out=model.decode(z);assert out['logs'].shape==(3,64,18);assert out['references'].shape==(3,9)


def test_finite_backward_and_export_independence():
    _,x,v=example();x=x.float();model=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0),parameters())
    a,va,pos,h=select(x,v);out=model(a,va,pos);loss,parts=objective(model,out,x,v,h,a,va);loss.backward()
    assert torch.isfinite(loss);assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    model.eval();z=model.encoder(a,va,pos).detach().clone();before=model.decode(z)['logs'];a.fill_(999)
    assert torch.equal(before,model.decode(z)['logs'])
    logs=before;assert (logs[...,6:10]<=logs[...,12:16]).all()
    for b in (0,6,12):
        assert (logs[...,b+1]>=torch.maximum(logs[...,b],logs[...,b+3])).all()
        assert (logs[...,b+2]<=torch.minimum(logs[...,b],logs[...,b+3])).all()
        assert (logs[...,b+4:b+6]>=0).all()
