import torch
import copy
import math
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


@pytest.mark.parametrize('visible_weight',[0,.5])
def test_source_invalid_targets_never_enter_reconstruction_loss(visible_weight):
    _,x,v=example();x=x.float();v[...,10]=False;v[...,24]=False
    m=Autoencoder(Config(width=32,layers=1,embedding=64,dropout=0,visible_weight=visible_weight),parameters())
    a,va,p,h=select(x,v,16);out=m(a,va,p);before,_=objective(m,out,x,v,h,a,va)
    changed=x.clone();changed[~v]=123456.;after,_=objective(m,out,changed,v,h,a,va)
    torch.testing.assert_close(before,after,rtol=0,atol=0);after.backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in m.parameters())


def raw_rows():
    import numpy as np
    from ..v5.shards.phase1_contract import SAMPLE_DTYPE
    rows=np.zeros(3,dtype=SAMPLE_DTYPE);rows['values']=1;rows['valid']=1;rows['timeframe_us']=1000000
    rows['end_us']=1700000000000000+np.arange(64)*1000000;rows['values'][:,:,12:16]=1.01;rows['valid'][0,:4,6:18]=0
    return rows


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required for full raw pipeline')
def test_full_raw_capture_optimizer_rng_resume_and_native_metrics():
    from .full_step import Replay,forward
    from .full_validation import Accumulator
    from ..phase1_v7.full_data import prepare_raw,gpu_inputs
    from ..phase1_v8.data import cache
    from ..phase1_v7.metrics import evaluate
    from ..phase2_v2.train import rng_state,restore_rng
    rows=raw_rows();b=prepare_raw(rows,native=True).to('cuda');values,valid,_=gpu_inputs(b);cached=cache(rows)
    torch.testing.assert_close(values.cpu(),cached['values'],rtol=2e-5,atol=2e-5);assert torch.equal(valid.cpu(),cached['valid'])
    config=Config(width=32,layers=1,embedding=64,dropout=0,readout='split_scale',input_price_scale=10000)
    m=Autoencoder(config,parameters()).cuda();opt=torch.optim.AdamW(m.parameters(),lr=.001,fused=True);replay=Replay(m,b)
    replay(b);opt.step();saved=copy.deepcopy(dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state()))
    expected=replay(b)[0].clone();opt.step();other=Autoencoder(config,parameters()).cuda();other.load_state_dict(saved['model'])
    second=torch.optim.AdamW(other.parameters(),lr=.001,fused=True);second.load_state_dict(saved['optimizer']);restore_rng(saved['rng']);again=Replay(other,b)
    actual=again(b)[0];second.step();torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-6)
    for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-6)
    with torch.no_grad():
        m.eval();out,x,v,h=forward(m,b,16);acc=Accumulator(16);acc.add(m,out,x,v,h,b);result=acc.finish()
        reference=evaluate(inverse(out['logs']).cpu().numpy(),rows,h.cpu().numpy()[...,None])
    assert result['raw_mae']['1s']['trade_close']==pytest.approx(reference['frames']['1000000']['channels']['trade_close']['mae'])


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA inference capture required')
@pytest.mark.parametrize('backbone',['transformer','mlp','conv1d_v1'])
@pytest.mark.parametrize('visible',[16,64])
def test_validation_graph_preserves_private_rng_masks_and_predictions(backbone,visible):
    from .full_step import InferenceReplay,forward
    from ..phase1_v7.full_data import prepare_raw
    b=prepare_raw(raw_rows(),native=True).to('cuda')
    model=Autoencoder(Config(width=32,layers=6 if backbone=='conv1d_v1' else 1,embedding=64,dropout=0,readout='split_scale',backbone=backbone,mlp_density_normalize=True),parameters()).cuda().eval()
    generator=torch.Generator(device='cuda').manual_seed(731);state=generator.get_state()
    with torch.no_grad():expected=forward(model,b,visible,generator,with_objective=True)
    generator.set_state(state);graph=InferenceReplay(model,b,visible,generator,with_objective=True);actual=graph(b)
    assert torch.equal(expected[3],actual[3])
    torch.testing.assert_close(expected[0]['logs'],actual[0]['logs'],rtol=1e-5,atol=1e-6)
    torch.testing.assert_close(expected[4],actual[4],rtol=1e-5,atol=1e-6)
    previous=actual[3].clone();again=graph(b)
    if visible==16:assert not torch.equal(previous,again[3])


@pytest.mark.parametrize('backbone',['mlp','conv1d_v1'])
def test_mlp512_slots_hidden_independence_and_decoder_contract(backbone):
    _,x,v=example();x=x.float();positions=torch.arange(0,64,4).expand(3,-1)
    a,va,p,h=select(x,v,16,positions=positions)
    m=Autoencoder(Config(backbone=backbone,width=64,layers=6 if backbone=='conv1d_v1' else 2,embedding=512,readout='split_scale',dropout=0,visible_weight=.5,input_price_scale=10000),parameters()).eval()
    if backbone=='conv1d_v1':assert not any(isinstance(q,torch.nn.Linear) for q in m.encoder.modules())
    z=m.encoder(a,va,p);assert z.shape==(3,512)
    changed=x.clone();changed[h]=12345;other,ov,_,_=select(changed,v,16,positions=positions)
    torch.testing.assert_close(z,m.encoder(other,ov,p),rtol=0,atol=0)
    # Reordering selected observations while retaining positions leaves slot input unchanged.
    torch.testing.assert_close(z,m.encoder(a.flip(1),va.flip(1),p.flip(1)),rtol=0,atol=0)
    out=m.decode(z);assert out['logs'].shape==(3,64,18) and out['references'].shape==(3,9)
    loss,_=objective(m,out,x,v,h,a,va);loss.backward()
    assert all(q.grad is not None and torch.isfinite(q.grad).all() for q in m.parameters())
    assert m.encoder(x,v,torch.arange(64).expand(3,-1)).shape==(3,512)
    m.config.mlp_density_normalize=True
    torch.testing.assert_close(z,m.encoder(a,va,p),rtol=0,atol=0)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA MLP replay required')
@pytest.mark.parametrize('backbone',['mlp','conv1d_v1'])
def test_mlp_raw_graph_and_resume(backbone):
    from .full_step import Replay
    from ..phase1_v7.full_data import prepare_raw
    from ..phase2_v2.train import rng_state,restore_rng
    b=prepare_raw(raw_rows()).to('cuda');c=Config(backbone=backbone,width=64,layers=6 if backbone=='conv1d_v1' else 2,embedding=512,readout='split_scale',dropout=0,visible_weight=.5)
    m=Autoencoder(c,parameters()).cuda();opt=torch.optim.AdamW(m.parameters(),lr=.0001,fused=True);graph=Replay(m,b)
    graph(b);opt.step();saved=copy.deepcopy(dict(model=m.state_dict(),opt=opt.state_dict(),rng=rng_state()))
    expected=graph(b)[0].clone();opt.step()
    other=Autoencoder(c,parameters()).cuda();other.load_state_dict(saved['model']);second=torch.optim.AdamW(other.parameters(),lr=.0001,fused=True);second.load_state_dict(saved['opt'])
    restore_rng(saved['rng']);again=Replay(other,b);actual=again(b)[0];second.step()
    torch.testing.assert_close(expected,actual,rtol=1e-5,atol=1e-6)
    for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-6)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA host staging required')
def test_reusable_host_staging_order_aliases_tails_and_bounded_allocation():
    from .pipeline import DeviceBatches
    from ..phase1_v7.full_data import prepare_raw
    batches=[]
    for index,size in enumerate([3,3,1,2,3,3,2,1,3,3]):
        rows=raw_rows()[:size].copy();rows['values']*=index+1
        batches.append(prepare_raw(rows,native=True))
    device=DeviceBatches(batches,'cuda');observed=[]
    try:
        for expected in batches:
            got=device.pop()
            assert got.grid.data_ptr()==got.target.data_ptr()==got.raw.data_ptr()
            # Clone on the consumer stream, then prefetch before synchronizing.
            copied=got.grid.clone();device.advance();observed.append(copied.cpu())
        with pytest.raises(StopIteration):device.pop()
        assert device.pin_allocations==8
        for actual,expected in zip(observed,batches):torch.testing.assert_close(actual,expected.grid,rtol=0,atol=0)
    finally:device.close()


@pytest.mark.parametrize('visible',[16,64])
def test_validation_objective_matches_training_formula_with_weighted_tail(visible):
    from .validation_objective import ObjectiveAccumulator
    _,x,v=example();x=x.float();v[0,:8,4]=False
    model=Autoencoder(Config(width=32,layers=1,embedding=64,readout='split_scale',dropout=0,visible_weight=.5),parameters()).eval()
    acc=ObjectiveAccumulator(visible);expected=0.
    for lo,hi in [(0,2),(2,3)]:
        t=x[lo:hi];tv=v[lo:hi];a,va,pos,h=select(t,tv,visible)
        out=model(a,va,pos);loss,_=objective(model,out,t,tv,h,a,va)
        expected+=float(loss.detach())*(hi-lo);acc.add(model,out,t,tv,h)
    result=acc.finish()
    assert result['loss']==pytest.approx(expected/3,rel=1e-6)
    assert all(math.isfinite(value) for value in result.values())


def test_continuation_preserves_data_order_and_allows_explicit_schedule_change():
    from .continuation import validate_continuation
    old=dict(config={'embedding':256},source_revision='old',batch_size=4096,workers=16,learning_rate=1e-4,epochs=100)
    parent=dict(contract=old,epoch=20,step=927672,batch_cursor=16493,origins=3799069768,phase='train')
    new=dict(old,source_revision='new',learning_rate=3e-5,epochs=25,retain_optimizer=True)
    start=validate_continuation(parent,new)
    assert start['step']==927672 and start['batch_cursor']==16493
    with pytest.raises(ValueError,match='workers'):validate_continuation(parent,dict(new,workers=32))
    with pytest.raises(ValueError,match='horizon'):validate_continuation(parent,dict(new,epochs=20))
