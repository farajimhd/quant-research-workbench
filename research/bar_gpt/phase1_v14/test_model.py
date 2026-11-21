import torch
from .model import Autoencoder,fit_inputs
from ..phase1_v12.model import Autoencoder as Original,Config,positions,objective


def test_input_only_zscore_parity_and_target_invariance():
    c=Config(width=32,layers=1,heads=2,precision='fp32')
    mean=torch.arange(16)/10;scale=torch.arange(16)/20+.5
    torch.manual_seed(17);old=Original(c,mean,scale)
    torch.manual_seed(17);m=Autoencoder(c,mean,scale,mean,scale)
    z=torch.randn(4,64,16);meta=torch.randn(4,64,36);p=positions(4,'cpu');v=torch.ones(4,64,dtype=torch.bool)
    torch.testing.assert_close(m(z,meta,p),old(z,meta,p),rtol=0,atol=0)
    pred=torch.randn_like(z);loss=objective(m,pred,z,v,p)[0];raw=m.denormalize(pred)
    m.encoder.input_center.zero_();m.encoder.input_scale.fill_(1)
    torch.testing.assert_close(loss,objective(m,pred,z,v,p)[0],rtol=0,atol=0)
    torch.testing.assert_close(raw,m.denormalize(pred),rtol=0,atol=0)
    known=torch.zeros_like(v).scatter(1,p,True)
    torch.testing.assert_close(m(z,meta,p),m(torch.where(known[...,None],z,z+100),meta,p))


def test_fit_masks_roundtrip_and_no_clipping():
    z=torch.arange(8*64*16,dtype=torch.float32).reshape(8,64,16)/100
    v=torch.ones(8,64,dtype=torch.bool);v[0,0]=False;z[0,0]=1e9
    specs=fit_inputs(z,v,[0.]*16,[1.]*16)
    for s in specs.values():
        c=torch.tensor(s['center']);scale=torch.tensor(s['scale']);x=z[v]
        torch.testing.assert_close(((x-c)/scale)*scale+c,x,atol=1e-5,rtol=1e-5)
    assert max(specs['minmax']['scale'])<100
    c=torch.tensor(specs['minmax']['center']);s=torch.tensor(specs['minmax']['scale'])
    assert (((z[0,0]-c)/s)>1).all()
