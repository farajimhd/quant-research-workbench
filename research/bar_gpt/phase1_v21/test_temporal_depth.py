import copy
import torch
from ..phase1_v20.test_model import build
from .model import initialize
from .temporal_basis import restore_basis


def test_extra_attention_preserves_initial_function_and_restores():
    torch.set_num_threads(2);m,t,_=build()
    contract=dict(bar_config=vars(t.config),temporal_config=vars(m.temporal.config),input_calibration=None)
    saved=dict(model=copy.deepcopy(m.state_dict()),contract=contract)
    a=initialize(None,saved,None,'joint_basis_prefloor_slow_temporal')
    b=initialize(None,saved,None,'joint_basis_prefloor_temporal_extra')
    x=torch.randn(2,16,16,requires_grad=True);meta=torch.randn(2,16,36);pos=torch.arange(16).expand(2,-1)
    y=a.temporal(x,meta,pos);z=b.temporal(x,meta,pos)
    torch.testing.assert_close(y,z,rtol=0,atol=0)
    ga,=torch.autograd.grad(y.square().sum(),x)
    gb,=torch.autograd.grad(z.square().sum(),x,retain_graph=True)
    torch.testing.assert_close(ga,gb,rtol=0,atol=0)
    z.square().mean().backward()
    assert b.temporal.blocks.extra.projection.weight.grad.abs().sum()>0
    assert b.temporal.blocks.extra.ff[-1].weight.grad.abs().sum()>0
    for n,v in a.decoder.state_dict().items():torch.testing.assert_close(v,b.decoder.state_dict()[n],rtol=0,atol=0)
    torch.optim.AdamW(b.parameters(),lr=1e-4,weight_decay=0).step()
    restored=restore_basis(dict(model=b.state_dict(),contract=dict(contract,decoder_parameterization='activity_dct64_v1',temporal_lr_multiplier=.1,temporal_extra_identity_block=True)))
    torch.testing.assert_close(restored.temporal(x,meta,pos),b.temporal(x,meta,pos),rtol=0,atol=0)
