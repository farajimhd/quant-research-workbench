import copy
import pytest
import torch
from .model import Forecaster,Config,loss
from .tokenizer import raw_logs
from ..phase1_v11.transforms import coordinates,decode,inverse


def example(n=4,device='cpu'):
    x=torch.randn(n,16,256,device=device)*.1;a=torch.rand(n,16,2,device=device)
    raw=torch.ones(n,64,18,device=device)
    for base in (0,6,12):
        raw[...,base]=2.;raw[...,base+1]=2.1;raw[...,base+2]=1.9;raw[...,base+3]=2.02
    raw[...,12:16]+=.1
    v=torch.ones_like(raw,dtype=torch.bool);v[0,:4,4]=False
    return x,a,dict(logs=raw_logs(raw,v),valid=v,raw=raw)


def test_shapes_embedding_only_decoder_export_and_gradients():
    torch.manual_seed(17);m=Forecaster();x,a,t=example();m.eval()
    z,p=m(x,a);assert z.shape==(4,256) and p.shape==(4,64,18)
    torch.testing.assert_close(m.decoder(z),p,rtol=0,atol=0)
    torch.testing.assert_close(m.encode_context(x,a),z,rtol=0,atol=0)
    exported=copy.deepcopy(m.encoder);decoder=copy.deepcopy(m.decoder)
    torch.testing.assert_close(decoder(exported(x,a)),p,rtol=0,atol=0)
    loss(p,t).backward();assert all(q.grad is not None and torch.isfinite(q.grad).all() for q in m.parameters())
    # Source-invalid target values cannot affect the loss.
    changed=dict(t,logs=t['logs'].clone());changed['logs'][~t['valid']]=12345
    torch.testing.assert_close(loss(p,t),loss(p,changed),rtol=0,atol=0)


def test_raw_roundtrip_difficult_values_and_source_masks():
    _,_,t=example();raw=t['raw'].double()
    for i,scale in enumerate((1e-5,.1,1e3,1e7)):
        for base in (0,6,12):raw[i,:,base:base+4]*=scale
        raw[i,:,4::6]=0 if i==0 else 1e12
    valid=torch.ones_like(raw,dtype=torch.bool)
    logs=raw_logs(raw,valid);code,_=coordinates(logs,valid)
    restored=inverse(decode(code,torch.zeros(4,9)))
    torch.testing.assert_close(restored,raw,rtol=3e-6,atol=1e-7)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA graph required')
def test_cuda_graph_update_and_exact_resume():
    from .cuda_step import CudaStep
    from .metrics import Metrics
    from ..phase2_v2.train import rng_state,restore_rng
    torch.manual_seed(17);m=Forecaster().cuda();x,a,t=example(4,'cuda');metrics=Metrics('cuda')
    opt=torch.optim.AdamW(m.parameters(),lr=1e-4,fused=True);g=CudaStep(m,4,metrics=metrics)
    assert metrics.count==0 and not metrics.sums.any()
    p,l,n=g(x,a,t);metrics.count+=4;opt.step()
    saved=copy.deepcopy(dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state()))
    expected=g(x,a,t)[0].clone();opt.step()
    other=Forecaster().cuda();other.load_state_dict(saved['model']);o=torch.optim.AdamW(other.parameters(),lr=1e-4,fused=True);o.load_state_dict(saved['optimizer'])
    restore_rng(saved['rng']);h=CudaStep(other,4);actual=h(x,a,t)[0].clone();o.step()
    torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-6)
    for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-6)


def test_loss_sensitive_to_price_and_activity():
    _,_,t=example();p=t['logs'].clone();assert float(loss(p,t))==0
    p[...,3]+=.001;assert float(loss(p,t))>0
    p=t['logs'].clone();p[...,4]+=1;assert float(loss(p,t))>0
