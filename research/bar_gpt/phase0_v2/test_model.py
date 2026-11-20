import torch
import pytest
from .model import Autoencoder,Config,objective
from ..phase0_v1.model import Autoencoder as MLP
from ..phase1_v11.test_diagnostic_metrics import fixture


@pytest.mark.parametrize('config',[Config(chunk_bars=13),Config(width=4,layers=1,heads=1,pooling='flatten',chunk_bars=13)])
def test_unchanged_decoder_inputs_loss_and_independent_bars(config):
    params=dict(mean=[0.]*27,scale=[1.]*27)
    torch.manual_seed(17);base=MLP(params)
    torch.manual_seed(17);model=Autoencoder(params,config)
    for k,v in base.decoder.state_dict().items():torch.testing.assert_close(model.decoder.state_dict()[k],v,rtol=0,atol=0)
    _,logs,v,_,_,_=fixture();x=torch.cat((logs.float(),torch.zeros(2,64,9)),-1);valid=torch.cat((v,torch.ones(2,64,9,dtype=torch.bool)),-1)
    z=model.encode(x,valid);assert z.shape==(2,64,16)
    out=model(x,valid);torch.testing.assert_close(model.decode(z)['logs'],out['logs'])
    changed=x.clone();changed[:,1:]+=10
    torch.testing.assert_close(model(changed,valid)['logs'][:,0],out['logs'][:,0])
    loss,_=objective(model,out,x,valid);loss.backward();assert all(torch.isfinite(p.grad).all() for p in model.parameters())
    assert model.encoder.blocks[0].qkv.weight.grad.abs().sum()>0


def test_attention_chunking_parity():
    params=dict(mean=[0.]*27,scale=[1.]*27)
    a=Autoencoder(params,Config(width=16,layers=1,heads=2,chunk_bars=5))
    b=Autoencoder(params,Config(width=16,layers=1,heads=2,chunk_bars=32768));b.load_state_dict(a.state_dict())
    x=torch.randn(3,64,27);v=torch.ones_like(x,dtype=torch.bool)
    torch.testing.assert_close(a(x,v)['logs'],b(x,v)['logs'],rtol=1e-5,atol=1e-6)
