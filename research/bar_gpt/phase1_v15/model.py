import math
import torch
from torch.nn import functional as F
from ..phase0_v2.model import AttentionBlock
from ..phase1_v12.model import Autoencoder as Base


class CountAttention(AttentionBlock):
    def __init__(self,width,heads,stable=False,balanced=True):
        super().__init__(width,heads);self.stable=stable;self.balanced=balanced

    def attention(self,x):
        n,t,d=x.shape
        q,k,v=self.qkv(self.norm1(x)).reshape(n,t,3,self.heads,d//self.heads).permute(2,0,3,1,4).unbind(0)
        if self.stable:
            q=F.normalize(q,dim=-1,eps=1e-6)*math.sqrt(d//self.heads)
            k=F.normalize(k,dim=-1,eps=1e-6)*math.sqrt(d//self.heads)
        bias=None
        if self.balanced and t!=17:
            # One CLS versus N bar tokens. Match training's CLS/bar-group mass.
            # Depends only on the visible count, never hidden values/statistics.
            bias=x.new_zeros(1,1,1,t,dtype=torch.float32)
            bias[...,0]=math.log((t-1)/16.)
        h=F.scaled_dot_product_attention(q,k,v,attn_mask=bias,dropout_p=0.)
        return self.projection(h.transpose(1,2).reshape(n,t,d))

    def forward(self,x):
        if self.stable:
            with torch.autocast(x.device.type,enabled=False):x=x.float()+self.attention(x.float())
        else:x=x+self.attention(x)
        return x+self.ff(self.norm2(x))


class Autoencoder(Base):
    def __init__(self,c,mean,scale,variant='balanced'):
        super().__init__(c,mean,scale);self.variant=variant
        if variant not in ('original','balanced','stable','stable_balanced'):raise ValueError('Unknown variant')
        for i in range(c.layers):
            old=self.encoder.blocks[i]
            new=CountAttention(c.width,c.heads,stable=variant.startswith('stable'),balanced=variant in ('balanced','stable_balanced'))
            new.load_state_dict(old.state_dict());self.encoder.blocks[i]=new
