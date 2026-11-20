from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from ..phase1_v11.transforms import coordinates,decode,PRICES
from ..phase1_v7.full_data import gpu_inputs


@dataclass(frozen=True)
class Config:
    embedding:int=16
    width:int=128
    layers:int=2
    backbone:str='residual_mlp'
    price_factor:float=1000.


class Block(nn.Module):
    def __init__(self,width):
        super().__init__();self.net=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,width*2),nn.GELU(),nn.Linear(width*2,width))
    def forward(self,x):return x+self.net(x)


class Autoencoder(nn.Module):
    def __init__(self,parameters,config=Config()):
        super().__init__();self.config=config
        for key in ('mean','scale'):self.register_buffer(key,torch.tensor(parameters[key],dtype=torch.float32))
        self.register_buffer('loss_factor',torch.tensor([config.price_factor if j in PRICES else 1. for j in range(18)]))
        if config.backbone=='linear':self.encoder=nn.Linear(54,config.embedding)
        elif config.backbone=='residual_mlp':
            self.encoder=nn.Sequential(nn.Linear(54,config.width),nn.GELU(),*[Block(config.width) for _ in range(config.layers)],nn.Linear(config.width,config.embedding))
        else:raise ValueError('Unknown encoder')
        self.decoder=nn.Linear(config.embedding,18)

    def encode(self,values,valid):
        code,cm=coordinates(values[...,:18],valid[...,:18])
        features=torch.cat((code,values[...,18:]),-1);mask=torch.cat((cm,valid[...,18:]),-1)
        scaled=torch.where(mask,(features-self.mean)/self.scale,0)
        # Each row is one bar; no operation can observe another bar/window.
        return self.encoder(torch.cat((scaled,valid.float()),-1))

    def decode(self,embedding):
        normalized=self.decoder(embedding.float());code=normalized*self.scale[:18]+self.mean[:18]
        constrained=torch.stack([code[...,j].clamp_min(0) if j%6 in (2,3,4,5) else code[...,j] for j in range(18)],-1)
        shape=code.shape
        logs=decode(constrained.reshape(-1,1,18),code.new_zeros((code.numel()//18,9)),constrain=False).reshape(shape)
        return dict(logs=logs,code=code,normalized=normalized)

    def forward(self,values,valid):return self.decode(self.encode(values,valid))


def objective(model,out,values,valid):
    code,cm=coordinates(values[...,:18],valid[...,:18]);mask=valid[...,:18]
    error=out['logs']-values[...,:18]
    element=F.smooth_l1_loss(error*model.loss_factor,torch.zeros_like(error),reduction='none')
    axes=tuple(range(error.ndim-1))
    channel=torch.where(mask,element,0).sum(axes)/mask.sum(axes).clamp_min(1)
    target=(code-model.mean[:18])/model.scale[:18]
    ce=F.smooth_l1_loss(out['normalized'],target,reduction='none')
    coordinate=torch.where(cm,ce,0).sum()/cm.sum().clamp_min(1)
    price=channel.reshape(3,6)[:,:4].mean();activity=channel.reshape(3,6)[:,4:].mean()
    return .5*(price+activity)+.1*coordinate,torch.stack((price,activity,coordinate))


def forward_batch(model,b):
    values,valid,_=gpu_inputs(b);out=model(values,valid)
    return out,values,valid
