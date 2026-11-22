import copy
import torch
from ..phase1_v20.test_model import build
from .model import initialize
from .temporal_basis import restore_basis


def test_nonlinear_initial_function_and_encoder_gradient_match():
    torch.set_num_threads(2);m,t,_=build()
    contract=dict(bar_config=vars(t.config),temporal_config=vars(m.temporal.config),input_calibration=None)
    saved=dict(model=copy.deepcopy(m.state_dict()),contract=contract)
    a=initialize(None,saved,None,'joint_basis_prefloor_slow_temporal')
    b=initialize(None,saved,None,'joint_basis_prefloor_nonlinear32')
    x=torch.randn(2,a.decoder.in_features,requires_grad=True)
    y=a.decoder(x);z=b.decoder(x)
    torch.testing.assert_close(y,z,rtol=0,atol=0)
    ga,=torch.autograd.grad(y.square().sum(),x)
    gb,=torch.autograd.grad(z.square().sum(),x,retain_graph=True)
    torch.testing.assert_close(ga,gb,rtol=0,atol=0)
    z.square().mean().backward()
    assert b.decoder.correction_out.weight.grad.abs().sum()>0
    for n,v in a.state_dict().items():torch.testing.assert_close(v,b.state_dict()[n],rtol=0,atol=0)
    opt=torch.optim.AdamW(b.parameters(),lr=1e-4,weight_decay=0);opt.step()
    cp=dict(model=b.state_dict(),contract=dict(contract,decoder_parameterization='activity_dct64_nonlinear32_v1',temporal_lr_multiplier=.1))
    restored=restore_basis(cp)
    torch.testing.assert_close(restored.decoder(x),b.decoder(x),rtol=0,atol=0)
    assert sum(p.numel() for p in b.decoder.parameters())-sum(p.numel() for p in a.decoder.parameters())==(a.decoder.in_features+1)*32+33*1152
