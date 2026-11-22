import torch
from .model import initialize
from ..phase1_v19.model import Autoencoder as Window
from ..phase1_v12.model import Config,positions
from ..phase0_v2.model import Autoencoder as Bar,Config as BarConfig


def build():
    teacher=Bar(dict(mean=torch.zeros(27),scale=torch.ones(27)),BarConfig(width=4,layers=1,heads=1,pooling='flatten'))
    window=Window(Config(),torch.zeros(16),torch.ones(16),'count_nullspace')
    with torch.no_grad():window.count_basis.normal_(std=.1);window.count_head.weight.normal_(std=.01)
    saved=dict(contract=dict(config=vars(window.config),architecture='count_nullspace'),model=window.state_dict())
    return initialize(teacher,saved,dict(mean=[0.]*16,scale=[1.]*16)),teacher,window


def test_one_decoder_hidden_isolation_and_joint_gradients():
    torch.set_num_threads(2);torch.manual_seed(13);m,_,_=build()
    assert [name for name,_ in m.named_modules() if name.endswith('decoder')]==['decoder']
    x=torch.rand(2,64,27);v=torch.ones_like(x,dtype=torch.bool);pos=positions(2,'cpu',16)
    output=m(x,v,pos);changed=x.clone();hidden=~torch.zeros(2,64,dtype=torch.bool).scatter(1,pos,True);changed[hidden]=100
    altered=m(changed,v,pos)
    torch.testing.assert_close(output[2]['logs'],altered[2]['logs'],rtol=0,atol=0)
    output[2]['logs'].square().mean().backward()
    for module in (m.bar.encoder,m.temporal,m.decoder):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in module.parameters())
        assert all(p.grad is None or torch.isfinite(p.grad).all() for p in module.parameters())


def test_composed_single_head_matches_old_two_linear_maps():
    m,teacher,window=build();e=torch.randn(3,256)
    expected=teacher.decode(window.denormalize(window.decode(e)))
    torch.testing.assert_close(m.decode(e)['code'],expected['code'],rtol=1e-4,atol=2e-6)


def test_real_objective_gradients_and_source_masks():
    from .objectives import loss
    m,teacher,_=build();x=torch.zeros(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
    raw=torch.ones(2,64,18);frames=torch.full((2,),1000000);p=positions(2,'cpu',16)
    old=teacher.encode(x,v).detach();value,_=loss(m,x,v,raw,frames,p,old,torch.ones(6,6))
    value.backward();assert torch.isfinite(value)
    assert all(q.grad is None or torch.isfinite(q.grad).all() for q in m.parameters())
    v.zero_();value,_=loss(m,x,v,raw*float('nan'),frames,p,old,torch.ones(6,6))
    assert value==0
