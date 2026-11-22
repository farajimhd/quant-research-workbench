import copy
import pytest
import torch
from ..phase1_v12.model import Config,positions
from ..phase1_v19.model import Autoencoder
from ..phase1_v22.model import PositionEncoder
from .model import expand_temporal


@pytest.mark.parametrize('width,depth',[(256,4),(384,6)])
def test_function_preserving_expansion_and_gradients(width,depth):
    torch.set_num_threads(2);torch.manual_seed(17)
    old=PositionEncoder(Autoencoder(Config(width=128,layers=2,heads=4,embedding=512),torch.zeros(16),torch.ones(16),'mask_adapter').encoder,32)
    expanded=expand_temporal(old,width,depth)
    assert expanded.config.width==width and expanded.config.layers==depth
    for visible in (16,64):
        z=torch.randn(2,visible,16);meta=torch.randn(2,visible,36);pos=positions(2,'cpu',visible)
        expected=old(z,meta,pos);actual=expanded(z,meta,pos)
        torch.testing.assert_close(actual,expected,rtol=3e-5,atol=3e-6)
        actual.square().mean().backward()
        assert all(torch.isfinite(p.grad).all() for p in expanded.parameters() if p.grad is not None)
        assert expanded.blocks[2].projection.weight.grad.abs().sum()>0
        duplicate=copy.deepcopy(expanded);torch.testing.assert_close(duplicate(z,meta,pos),actual)


def test_invalid_expansion():
    old=PositionEncoder(Autoencoder(Config(embedding=512),torch.zeros(16),torch.ones(16),'mask_adapter').encoder,32)
    with pytest.raises(ValueError):expand_temporal(old,192,4)


def test_whole_model_hidden_isolation_and_restore():
    from ..phase1_v20.test_model import build as fixture
    from ..phase1_v22.model import build as base_build
    from .model import build,restore
    parent,teacher,_=fixture()
    template=dict(model=parent.state_dict(),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(parent.temporal.config),input_calibration=None))
    base=base_build(template,'slots32',True)
    saved=dict(model=base.state_dict(),parent_template=template,architecture=dict(kind='slots32',wide=True),contract={})
    model=build(saved,256,4);x=torch.rand(2,64,27);valid=torch.ones_like(x,dtype=torch.bool);pos=positions(2,'cpu',16)
    expected=base(x,valid,pos)[2]['code'];out=model(x,valid,pos)[2]['code']
    torch.testing.assert_close(out,expected,rtol=3e-5,atol=3e-6)
    changed=x.clone();visible=torch.zeros(2,64,dtype=torch.bool).scatter(1,pos,True);changed[~visible]+=100
    torch.testing.assert_close(model(changed,valid,pos)[2]['code'],out,rtol=0,atol=0)
    torch.testing.assert_close(model.decoder.weight,base.decoder.weight,rtol=0,atol=0)
    recovered=restore(dict(model=model.state_dict(),parent_template=saved,architecture=dict(width=256,depth=4)))
    torch.testing.assert_close(recovered(x,valid,pos)[2]['code'],out)
    from .model import optimizer
    groups=optimizer(model,width_scaled=True).param_groups
    assert [g['lr'] for g in groups]==[3e-7,5e-6,1.5e-4,3e-4]
    assigned=[id(p) for g in groups for p in g['params']]
    assert len(assigned)==len(set(assigned))==len(list(model.parameters()))
