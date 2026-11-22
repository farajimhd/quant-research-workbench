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


def test_new_block_lr_preserves_initial_state_and_pretrained_rates():
    from .experiment import optimizer
    m,t,_=build();contract=dict(bar_config=vars(t.config),temporal_config=vars(m.temporal.config),input_calibration=None)
    saved=dict(model=copy.deepcopy(m.state_dict()),contract=contract)
    torch.manual_seed(17);a=initialize(None,saved,None,'joint_basis_prefloor_temporal_extra')
    torch.manual_seed(17);b=initialize(None,saved,None,'joint_basis_prefloor_temporal_extra_fast')
    for k,v in a.state_dict().items():torch.testing.assert_close(v,b.state_dict()[k],rtol=0,atol=0)
    opt=optimizer(b,1e-6,3e-8)
    import pytest
    assert [g['lr'] for g in opt.param_groups]==pytest.approx([3e-8,1e-7,1e-6,1e-5],rel=1e-14,abs=0)
    ids=[id(p) for g in opt.param_groups for p in g['params']]
    assert len(ids)==len(set(ids))==len(list(b.parameters()))
    assert {id(p) for p in opt.param_groups[-1]['params']}=={id(p) for p in b.temporal.blocks.extra.parameters()}
    restored=restore_basis(dict(model=b.state_dict(),contract=dict(contract,decoder_parameterization='activity_dct64_v1',temporal_lr_multiplier=.1,temporal_extra_identity_block=True,new_block_lr_multiplier=10.)))
    assert restored.new_block_lr_multiplier==10.
    resumed=optimizer(restored,1e-6,3e-8);resumed.load_state_dict(opt.state_dict())
