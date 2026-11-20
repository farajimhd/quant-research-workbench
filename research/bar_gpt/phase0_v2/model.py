from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from ..phase0_v1.model import Autoencoder as BaseAutoencoder,Config as BaseConfig,forward_batch,objective


@dataclass(frozen=True)
class Config(BaseConfig):
    width:int=32
    layers:int=2
    backbone:str='feature_attention'
    heads:int=4
    chunk_bars:int=16384
    pooling:str='mean'


class AttentionBlock(nn.Module):
    def __init__(self,width,heads):
        super().__init__();self.heads=heads;self.width=width
        self.norm1=nn.LayerNorm(width);self.qkv=nn.Linear(width,3*width);self.projection=nn.Linear(width,width)
        self.norm2=nn.LayerNorm(width);self.ff=nn.Sequential(nn.Linear(width,2*width),nn.GELU(),nn.Linear(2*width,width))
    def forward(self,x):
        n,t,d=x.shape
        q,k,v=self.qkv(self.norm1(x)).reshape(n,t,3,self.heads,d//self.heads).permute(2,0,3,1,4).unbind(0)
        h=F.scaled_dot_product_attention(q,k,v,dropout_p=0.).transpose(1,2).reshape(n,t,d)
        x=x+self.projection(h)
        return x+self.ff(self.norm2(x))


class FeatureAttention(nn.Module):
    def __init__(self,c):
        super().__init__()
        if c.width%c.heads or c.layers<1 or c.chunk_bars<1 or c.chunk_bars>32768:raise ValueError('Invalid attention geometry/chunk')
        self.config=c
        self.value_weight=nn.Parameter(torch.randn(18,c.width)*.1)
        self.feature_identity=nn.Parameter(torch.randn(18,c.width)*.02)
        # Same remaining36 inputs: nine standardized time fields and27 validity flags.
        self.metadata=nn.Linear(36,c.width)
        self.blocks=nn.Sequential(*[AttentionBlock(c.width,c.heads) for _ in range(c.layers)])
        if c.pooling not in ('mean','flatten'):raise ValueError('Unknown readout')
        self.readout=nn.Linear(c.width*(18 if c.pooling=='flatten' else 1),c.embedding)
    def encode_chunk(self,x):
        h=x[:,:18,None]*self.value_weight+self.feature_identity+self.metadata(x[:,18:])[:,None]
        h=self.blocks(h)
        return self.readout(h.flatten(1) if self.config.pooling=='flatten' else h.mean(1))
    def forward(self,x):
        shape=x.shape[:-1];x=x.reshape(-1,54)
        # Bounded attention workspace and CUDA grid sizes; never mixes different bars.
        return torch.cat([self.encode_chunk(part) for part in x.split(self.config.chunk_bars)],0).reshape(*shape,self.config.embedding)


class Autoencoder(BaseAutoencoder):
    def __init__(self,parameters,config=Config()):
        # Reproduce the MLP's exact seeded decoder initialization, then replace only encoder.
        super().__init__(parameters,BaseConfig())
        self.config=config;self.encoder=FeatureAttention(config)
