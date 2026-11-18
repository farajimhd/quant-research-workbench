import copy
import torch
from .model import Config,Autoencoder,select,objective,learning_rate
from ..phase1_v8.data import calibrate
from ..joint_v9.model import structured


def sample():
    torch.manual_seed(8)
    code=torch.randn(4,64,18)*.01;code[...,0]=2.;code[...,6]=2.
    code[...,4]=code[...,5]=code[...,10]=code[...,11]=code[...,16]=code[...,17]=2.
    values=torch.cat((structured(code),torch.randn(4,64,9)),2)
    valid=torch.ones_like(values,dtype=torch.bool);valid[0,3,10:12]=False
    params,_=calibrate(dict(values=values,valid=valid),'joint')
    return values,valid,params


def test_shapes_finite_and_all_learned():
    x,v,p=sample();m=Autoencoder(Config(width=32,layers=1,embedding=64,context=24,precision='fp32'),p)
    a,b,pos,h=select(x,v);z=m.encoder(a,b,pos);out=m.decode(z)
    assert z.shape==(4,64) and out[0].shape==(4,64,18)
    loss,_=objective(m,out,x,v,h);loss.backward()
    assert all(t.grad is not None and torch.isfinite(t.grad).all() for t in m.parameters())
    with torch.no_grad():
        for t in m.encoder.parameters():t.zero_()
    assert torch.count_nonzero(m.encoder(a,b,pos))==0


def test_hidden_values_cannot_enter_encoder():
    x,v,p=sample();m=Autoencoder(Config(width=32,layers=1,embedding=64,context=24,dropout=0,precision='fp32'),p).eval()
    a,b,pos,h=select(x,v);before=m(a,b,pos)
    changed=x.clone();changed[h]=10000.;changed_v=v.clone();changed_v[h]=False
    aa,bb,pp,hh=select(changed,changed_v,positions=pos)
    for old,new in zip(before,m(aa,bb,pp)):torch.testing.assert_close(old,new,rtol=0,atol=0)
    assert h.sum()==4*48 and (pos.sort(1).values==pos).all()


def test_loss_excludes_visible_and_invalid_targets():
    x,v,p=sample();m=Autoencoder(Config(width=32,layers=1,embedding=64,context=24,dropout=0,precision='fp32'),p)
    a,b,pos,h=select(x,v);out=m(a,b,pos);before,_=objective(m,out,x,v,h)
    changed=x.clone();changed[~h]=100.;changed[~v]=100.
    after,_=objective(m,out,changed,v,h);torch.testing.assert_close(before,after,rtol=0,atol=0)


def test_random_masks_and_full_export():
    x,v,p=sample();g=torch.Generator().manual_seed(7)
    a,b,pos,h=select(x,v,generator=g);_,_,other,_=select(x,v,generator=g)
    assert not torch.equal(pos,other)
    m=Autoencoder(Config(width=32,layers=1,embedding=64,context=24,dropout=0,precision='fp32'),p).eval()
    full=select(x,v,64);assert m.encoder(*full[:3]).shape==(4,64)
    enc=copy.deepcopy(m.encoder);torch.testing.assert_close(enc(a,b,pos),m.encoder(a,b,pos),rtol=0,atol=0)


def test_schedule_endpoints():
    assert learning_rate(0,1000)==1e-3
    assert learning_rate(999,1000)==1e-5
    assert learning_rate(1000,1000)==1e-5
    assert all(learning_rate(i,1000)>=learning_rate(i+1,1000) for i in range(999))


def test_independent_attention_initialization():
    x,v,p=sample();m=Autoencoder(Config(width=32,layers=2,embedding=64,context=24),p)
    assert not torch.equal(m.encoder.backbone.layers[0].self_attn.in_proj_weight,m.encoder.backbone.layers[1].self_attn.in_proj_weight)


def test_native_price_bands():
    from .metrics import PriceRelative
    actual=torch.ones(1,18,64,dtype=torch.float64)*10;actual[:,:,:4]=torch.tensor([.01,.5,2.,200.])
    valid=torch.ones_like(actual,dtype=torch.bool);relative=PriceRelative()
    relative.add(actual*.01,actual,valid,torch.zeros(1,dtype=torch.int64))
    r=relative.metrics('raw_mae',('100ms','1s','10s','1m','1h','daily'),tuple(str(i) for i in range(18)))
    assert r['price_relative_counts_by_price_band/below_0.1/100ms/0']==1
    assert abs(r['price_mape_pct/100ms/0']-1)<1e-12


def test_panel_states_and_validation_speed():
    import io,time
    from rich.console import Console
    from .panel import render
    for width,height in ((80,24),(120,35)):
        for state,stage in (('running','training'),('running','masked + all-visible full validation'),('failed','failed')):
            stream=io.StringIO();console=Console(file=stream,width=width,height=height)
            data=dict(state=state,stage=stage,updated=time.time(),validation_windows_per_second=12345,
                      validation_eta_seconds=90,validation_total=47750007,validation_done=10000,
                      epoch_total=186575722,epoch_done=20000,epoch=1,batch_size=1024,workers=8,prefetch=4)
            console.print(render(data,width,height));text=stream.getvalue()
            assert 'BarGPT V10' in text and len(text.splitlines())<=height
            if 'validation' in stage:assert '12,345 windows/s' in text


def test_compact_native_group_reduction():
    from .train import group_sum
    torch.manual_seed(7)
    x=torch.randn(19,64,18,dtype=torch.float64)*1e6;v=torch.rand(19,64,18)>.2
    x[~v]=float('nan');frames=torch.arange(19)%6
    groups=torch.nn.functional.one_hot(frames,6).T.double()
    actual=group_sum(x,v,groups)
    expected=torch.stack([torch.where(v&(frames==f)[:,None,None],x,0).sum((0,1)) for f in range(6)])
    torch.testing.assert_close(actual,expected,rtol=1e-12,atol=1e-7)


def test_qknorm_shapes_and_gradients():
    x,v,p=sample();m=Autoencoder(Config(width=32,layers=2,embedding=64,context=24,attention='qknorm'),p)
    a,b,pos,h=select(x,v);loss,_=objective(m,m(a,b,pos),x,v,h);loss.backward()
    assert all(t.grad is not None and torch.isfinite(t.grad).all() for t in m.parameters())
    with torch.no_grad():
        for block in list(m.encoder.backbone)[:-1]:block.qkv.weight.mul_(1000)
    assert all(torch.isfinite(y).all() for y in m(a,b,pos))


def test_stable_clip_finite_extreme_gradients():
    from .step import stable_clip
    p=torch.nn.Parameter(torch.zeros(2));p.grad=torch.tensor([1e30,-1e30])
    norm=stable_clip([p]);assert torch.isfinite(norm) and torch.isfinite(p.grad).all()
    torch.testing.assert_close(p.grad.norm(),torch.tensor(1.),rtol=1e-6,atol=1e-6)
