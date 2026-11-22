"""Opt-in, parameter-preserving attention kernels for V26 only."""
import math
from types import MethodType
import torch
from torch.nn import functional as F
from ..phase0_v2.model import AttentionBlock
from ..phase1_v15.model import CountAttention


def attention(q,k,v,mode,bias=None):
    d=q.shape[-1]
    if ('tiny' in mode and q.shape[-2]<=18 and d<=8 and ('fp32' not in mode or q.dtype==torch.float32)) or (mode=='dense' and q.dtype==torch.float32):
        # Explicit FP32 softmax for tiny feature attention. No reduced precision.
        with torch.autocast(q.device.type,enabled=False):
            scores=(q.float() @ k.float().transpose(-2,-1))*(d**-.5)
            if bias is not None:scores=scores+bias
            return (scores.softmax(-1) @ v.float()).to(q.dtype)
    if 'pad' in mode and d%8:
        # Zero padding preserves dot products; retain ORIGINAL inverse-sqrt scale.
        n=8-d%8
        return F.scaled_dot_product_attention(F.pad(q,(0,n)),F.pad(k,(0,n)),F.pad(v,(0,n)),attn_mask=bias,scale=d**-.5)[...,:d]
    return F.scaled_dot_product_attention(q,k,v,attn_mask=bias,dropout_p=0.)


def block_forward(self,x):
    n,t,d=x.shape
    q,k,v=self.qkv(self.norm1(x)).reshape(n,t,3,self.heads,d//self.heads).permute(2,0,3,1,4).unbind(0)
    h=attention(q,k,v,self._v26_kernel).transpose(1,2).reshape(n,t,d)
    x=x+self.projection(h)
    return x+self.ff(self.norm2(x))


def install(model,teacher,mode):
    if mode not in ('baseline','tiny','pad','tiny_pad','tiny_fp32','dense'):raise ValueError(mode)
    for root in (model,teacher):
        for module in root.modules():
            # CountAttention has extra scale/mass semantics: never replace it.
            if type(module) is AttentionBlock:
                module._v26_kernel=mode
                module.forward=MethodType(AttentionBlock.forward if mode=='baseline' else block_forward,module)
            elif type(module) is CountAttention:
                module._v26_kernel=mode
                module.attention=MethodType(count_attention if mode=='dense' else CountAttention.attention,module)


def count_attention(self,x):
    n,t,d=x.shape
    q,k,v=self.qkv(self.norm1(x)).reshape(n,t,3,self.heads,d//self.heads).permute(2,0,3,1,4).unbind(0)
    if self.stable:
        q=F.normalize(q,dim=-1,eps=1e-6)*math.sqrt(d//self.heads)
        k=F.normalize(k,dim=-1,eps=1e-6)*math.sqrt(d//self.heads)
    bias=None
    if self.balanced and t!=17:
        bias=x.new_zeros(1,1,1,t,dtype=torch.float32);bias[...,0]=math.log((t-1)/16.)
    h=attention(q,k,v,self._v26_kernel,bias)
    return self.projection(h.transpose(1,2).reshape(n,t,d))

