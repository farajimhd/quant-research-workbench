"""Experimental optimizer coordinates with an explicit checkpoint contract."""
import math
import torch
from torch import nn
from torch.nn import functional as F


def dct64(device=None, dtype=torch.float64):
    k=torch.arange(64,device=device,dtype=dtype)[:,None]
    t=torch.arange(64,device=device,dtype=dtype)[None,:]
    basis=torch.cos(math.pi*(t+.5)*k/64)*math.sqrt(2/64)
    basis[0]/=math.sqrt(2)
    return basis


class ActivityBasisLinear(nn.Module):
    """One affine embedding readout, with six activity streams in DCT coordinates.

    Prices retain native parameters. The fixed inverse basis uses no data or
    references. Distinct state keys make legacy native-head restores fail closed.
    """
    def __init__(self, native):
        super().__init__()
        if native.out_features!=64*18 or native.bias is None:
            raise ValueError('Expected 64 by 18 affine decoder')
        self.in_features=native.in_features;self.out_features=native.out_features
        q=dct64(native.weight.device)
        indices=torch.tensor([4,5,10,11,16,17],device=native.weight.device)
        w=native.weight.detach().double().reshape(64,18,-1).clone()
        b=native.bias.detach().double().reshape(64,18).clone()
        w[:,indices]=torch.einsum('kt,tci->kci',q,w[:,indices])
        b[:,indices]=q@b[:,indices]
        self.coefficient_weight=nn.Parameter(w.reshape_as(native.weight).to(native.weight.dtype))
        self.coefficient_bias=nn.Parameter(b.flatten().to(native.bias.dtype))
        self.register_buffer('inverse_basis',q.T.to(native.weight.dtype))
        self.register_buffer('activity_indices',indices)

    def forward(self,x):
        z=F.linear(x,self.coefficient_weight,self.coefficient_bias).reshape(-1,64,18)
        activity=torch.einsum('tk,bkc->btc',self.inverse_basis,z[:,:,self.activity_indices])
        return z.index_copy(2,self.activity_indices,activity).flatten(1)


class ActivityBasisNonlinear(ActivityBasisLinear):
    """One embedding-only head with a small zero-initialized nonlinear correction."""
    def __init__(self,native,rank=32):
        super().__init__(native)
        self.correction_in=nn.Linear(self.in_features,rank,device=native.weight.device,dtype=native.weight.dtype)
        self.correction_out=nn.Linear(rank,self.out_features,device=native.weight.device,dtype=native.weight.dtype)
        nn.init.zeros_(self.correction_out.weight);nn.init.zeros_(self.correction_out.bias)

    def forward(self,x):
        return super().forward(x)+self.correction_out(F.gelu(self.correction_in(x)))


def restore_basis(saved):
    """Restore only the declared basis schema; never treat coefficients as bars."""
    from ..phase1_v20.encoders import restore
    kind=saved['contract'].get('decoder_parameterization')
    if kind not in ('activity_dct64_v1','activity_dct64_nonlinear32_v1'):
        raise ValueError('Explicit activity basis contract required')
    state=saved['model'];w=state['decoder.coefficient_weight'];b=state['decoder.coefficient_bias']
    template={k:v for k,v in state.items() if not k.startswith('decoder.')}
    template.update({'decoder.weight':torch.zeros_like(w),'decoder.bias':torch.zeros_like(b)})
    model=restore(dict(saved,model=template))
    model.decoder=(ActivityBasisNonlinear(model.decoder) if kind=='activity_dct64_nonlinear32_v1' else ActivityBasisLinear(model.decoder))
    torch.testing.assert_close(state['decoder.inverse_basis'].cpu(),model.decoder.inverse_basis.cpu(),rtol=0,atol=0)
    torch.testing.assert_close(state['decoder.activity_indices'].cpu(),model.decoder.activity_indices.cpu(),rtol=0,atol=0)
    model.load_state_dict(state,strict=True)
    model.activity_change_prefloor=True;model.repair_arm='joint_repaired'
    model.temporal_lr_multiplier=saved['contract'].get('temporal_lr_multiplier',1.)
    if not 0<model.temporal_lr_multiplier<=1:raise ValueError('Temporal LR contract')
    if saved['contract'].get('range_calibration') is not None:
        from .range_balance import attach
        attach(model,saved['contract']['range_calibration'])
    return model

