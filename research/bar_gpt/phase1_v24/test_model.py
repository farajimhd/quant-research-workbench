import torch
import pytest
from ..phase1_v12.model import Config,positions
from ..phase1_v19.model import Autoencoder
from ..phase1_v22.model import PositionEncoder
from ..phase1_v23.model import expand_temporal
from .model import optimizer,SPECS,EXTREME_SPEC


def test_expansion_from_six_block_parent_and_gradients():
    torch.set_num_threads(2);torch.manual_seed(17)
    old=PositionEncoder(Autoencoder(Config(width=384,layers=6,heads=12,embedding=512),torch.zeros(16),torch.ones(16),'mask_adapter').encoder,32)
    new=expand_temporal(old,768,10)
    z=torch.randn(1,16,16);meta=torch.randn(1,16,36);pos=positions(1,'cpu',16)
    expected=old(z,meta,pos);out=new(z,meta,pos)
    torch.testing.assert_close(out,expected,rtol=1e-4,atol=1e-5)
    out.square().mean().backward()
    assert new.blocks[6].projection.weight.grad.abs().sum()>0
    assert all(torch.isfinite(p.grad).all() for p in new.parameters() if p.grad is not None)


def test_size_and_lr_contracts():
    expected=[492176066,49811906,101242946,198705602,7788482]
    for spec,count in zip(SPECS,expected):
        w,d=spec['width'],spec['depth']
        assert 8*w*w*d+11*w*d+4*w*w+226*w+8642==count
    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__();self.bar=torch.nn.Linear(1,1);self.decoder=torch.nn.Linear(1,1)
            self.temporal=torch.nn.Linear(1,1);self.temporal.config=Config(width=768)
    model=Tiny();opt=optimizer(model,.3)
    assert [g['lr'] for g in opt.param_groups]==[9e-8,3e-7,1.5e-6,3e-6]
    assert len({id(p) for g in opt.param_groups for p in g['params']})==len(list(model.parameters()))


def test_extreme_size_preserves_parent_head_geometry():
    w,d=EXTREME_SPEC['width'],EXTREME_SPEC['depth']
    count=8*w*w*d+11*w*d+4*w*w+226*w+8642
    assert 1.9e9<count<2.1e9
    assert w%384==0 and d>=6


def test_higher_lr_requires_stability_qualification():
    from .qualification import choose_multiplier
    assert choose_multiplier(.3,[dict(multiplier=3.,eligible=False)])==.3
    with pytest.raises(ValueError):choose_multiplier(.3,[dict(multiplier=3.,eligible=False)],True)
    assert choose_multiplier(.3,[dict(multiplier=3.,eligible=True)],True)==3.
