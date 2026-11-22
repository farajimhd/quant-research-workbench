import copy
import torch
from ..phase1_v20.test_model import build
from .model import initialize,verify_protected
from .objectives import loss


def test_protected_rows_and_finite_masked_gradients():
    torch.set_num_threads(2);model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    for arm in ('range','both_log','both_symmetric'):
        m=initialize(None,saved,None,arm);opt=torch.optim.AdamW(m.decoder.parameters(),lr=1e-5,weight_decay=0)
        raw=torch.ones(2,64,27);raw[...,1]=1.1;raw[...,2]=.9
        values=raw.log();valid=torch.ones_like(raw,dtype=torch.bool);pos=torch.arange(16).expand(2,-1)
        for _ in range(2):
            opt.zero_grad(set_to_none=True);value,_=loss(m,values,valid,raw,torch.ones(2),pos,None,None);value.backward()
            assert torch.isfinite(value) and all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)
            opt.step()
        verify_protected(m,saved)
        assert all(p.grad is None for p in m.bar.parameters())
        value,_=loss(m,values,torch.zeros_like(valid),raw,torch.ones(2),pos,None,None)
        assert value==0
