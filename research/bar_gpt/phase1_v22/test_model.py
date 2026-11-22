import copy
import torch
from ..phase0_v2.model import FeatureAttention,Config as BarConfig
from ..phase1_v12.model import Config,positions
from ..phase1_v19.model import Autoencoder
from .model import widen_features,PositionEncoder


def test_widen_preserves_feature_outputs():
    torch.manual_seed(41)
    bar=torch.nn.Module();bar.config=BarConfig(width=4,heads=1,layers=1,embedding=16,pooling='flatten')
    bar.encoder=FeatureAttention(bar.config);x=torch.randn(13,54);expected=bar.encoder(x)
    widen_features(bar);torch.testing.assert_close(bar.encoder(x),expected,rtol=2e-5,atol=2e-6)
    bar.encoder(x).square().mean().backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in bar.parameters())


def test_position_encoder_shapes_and_roundtrip():
    torch.manual_seed(42)
    parent=Autoencoder(Config(embedding=512),torch.zeros(16),torch.ones(16),'mask_adapter').encoder
    for n in (8,16,32):
        model=PositionEncoder(parent,n);decoder=torch.nn.Linear(n,18)
        for count in (16,64):
            pos=positions(2,'cpu',count);z=torch.randn(2,count,16);meta=torch.randn(2,count,36)
            embedding=model(z,meta,pos);assert embedding.shape==(2,64,n)
            out=decoder(embedding);assert out.shape==(2,64,18)
            out.square().mean().backward();assert torch.isfinite(model.readout.weight.grad).all()
            copy_model=copy.deepcopy(model);torch.testing.assert_close(copy_model(z,meta,pos),embedding)


def test_full_loss_no_hidden_input_and_restore():
    from ..phase1_v20.test_model import build as fixture
    from ..phase1_v21.objectives import loss
    from .model import build,restore
    parent,teacher,_=fixture()
    saved=dict(model=parent.state_dict(),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(parent.temporal.config),input_calibration=None))
    for kind in ('global256','slots8','slots32'):
        m=build(saved,kind);x=torch.rand(2,64,27);valid=torch.ones_like(x,dtype=torch.bool);pos=torch.arange(16).expand(2,-1)
        actual=m(x,valid,pos);changed=x.clone();changed[:,16:]+=100
        torch.testing.assert_close(m(changed,valid,pos)[2]['logs'],actual[2]['logs'],rtol=0,atol=0)
        objective,_=loss(m,x,valid,x.exp(),torch.ones(2),pos,torch.zeros(2,64,16),torch.ones(6,6))
        objective.backward();assert torch.isfinite(objective)
        assert all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)
        restored=restore(dict(parent_template=saved,architecture=dict(kind=kind,wide=False),model=m.state_dict()))
        torch.testing.assert_close(restored(x,valid,pos)[2]['logs'],actual[2]['logs'])
        empty,_=loss(m,x,torch.zeros_like(valid),x.exp(),torch.ones(2),pos,torch.zeros(2,64,16),torch.ones(6,6))
        assert empty==0

