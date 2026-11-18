import torch
import copy
import pytest
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


def test_all_partial_bid_ask_combinations():
    raw,x,v=example();raw=raw[:1].repeat(4,1,1);x=x[:1].repeat(4,1,1);v=v[:1].repeat(4,1,1)
    for pattern in range(256):
        row,bar=divmod(pattern,64)
        for j,k in enumerate((6,7,8,9,12,13,14,15)):v[row,bar,k]=bool((pattern>>j)&1)
    code,_=coordinates(x[...,:18],v[...,:18]);ref,_=references(x,v,torch.zeros(9))
    restored=inverse(decode(center(code,ref),ref));torch.testing.assert_close(restored[v[...,:18]],raw[v[...,:18]],rtol=1e-12,atol=1e-12)


@pytest.mark.parametrize('readout',['latents','include_reference','split_scale'])
def test_hidden_targets_cannot_change_embedding_or_references(readout):
    _,x,v=example();x=x.float();positions=torch.arange(0,64,4).expand(3,-1)
    a,va,pos,h=select(x,v,16,positions=positions);model=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0,readout=readout),parameters()).eval()
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


def test_optimizer_rng_resume_parity():
    from ..phase2_v2.train import rng_state,restore_rng
    from .step import backward
    _,x,v=example();x=x.float();c=Config(width=32,layers=1,embedding=64,dropout=.05)
    model=Autoencoder(c,parameters());opt=torch.optim.AdamW(model.parameters(),lr=.001)
    backward(model,x,v);opt.step()
    saved=copy.deepcopy(dict(model=model.state_dict(),optimizer=opt.state_dict(),rng=rng_state()))
    opt.zero_grad(set_to_none=True);expected=backward(model,x,v)[0];opt.step()
    other=Autoencoder(c,parameters());other.load_state_dict(saved['model']);second=torch.optim.AdamW(other.parameters(),lr=.001);second.load_state_dict(saved['optimizer'])
    restore_rng(saved['rng']);actual=backward(other,x,v)[0];second.step()
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    for p,q in zip(model.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=0,atol=0)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required for real capture')
@pytest.mark.parametrize('readout',['latents','split_scale'])
def test_cuda_graph_random16_backward(readout):
    from .step import Replay
    _,x,v=example();x=x.float().cuda();v=v.cuda()
    model=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0,readout=readout),parameters()).cuda()
    replay=Replay(model,x,v);a=replay(x,v)[0].clone();b=replay(x,v)[0].clone()
    assert torch.isfinite(a)&torch.isfinite(b)


def test_split_scale_is_learned_embedding_and_token_count_invariant():
    _,x,v=example();x=x.float();x[...,:18]=x[:,:1,:18].clone().expand(-1,64,-1)
    m=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0,readout='split_scale'),parameters()).eval()
    a,va,p,h=select(x,v,16);z=m.encoder(a,va,p)
    full=m.encoder(x,v,torch.arange(64).expand(len(x),-1))
    torch.testing.assert_close(z[:,-32:],full[:,-32:],rtol=0,atol=0)
    torch.testing.assert_close(m.decode(z)['references'],m.decode(full)['references'],rtol=0,atol=0)
    loss,_=objective(m,m.decode(z),x,v,h,a,va);loss.backward()
    assert m.encoder.scale_projection.weight.grad is not None
    assert m.reference_head.weight.grad is not None


def test_basispoint_input_conditioning_preserves_transform_and_output_units():
    _,x,v=example();x=x.float();code,cm=coordinates(x[...,:18],v[...,:18]);ref,_=references(x,v,torch.zeros(9))
    m=Autoencoder(Config(width=32,layers=1,embedding=64,input_price_scale=10000),parameters())
    relative=center(code,ref);conditioned=torch.asinh(relative*m.encoder.input_factor)
    torch.testing.assert_close(torch.sinh(conditioned)/m.encoder.input_factor,relative,rtol=2e-6,atol=2e-6)
    assert m.encoder.factor[0]==100 and m.encoder.input_factor[0]==10000
    a,va,pos,h=select(x,v);loss,_=objective(m,m(a,va,pos),x,v,h,a,va);loss.backward();assert torch.isfinite(loss)
