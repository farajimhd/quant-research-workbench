"""Experimental optimizer coordinates; not yet enabled in the training launcher."""
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

