"""Visible-input scale slots, residual encoder, one-affine structured decoder."""
from dataclasses import dataclass
import torch
from torch import nn
from ..phase1_v6.model import PRICES, ACTIVITY, Transform as PreviousTransform


@dataclass(frozen=True)
class Config:
    variant: str = 'canonical'
    width: int = 1024
    embedding: int = 512
    layers: int = 3
    decoder: int = 0
    visible: int = 16
    validity_slots: bool = True


class Transform(PreviousTransform):
    def inverse(self, logs):
        raw=logs.double().clone()
        raw[...,PRICES]=logs[...,PRICES].double().exp()
        raw[...,ACTIVITY]=logs[...,ACTIVITY].double().expm1()
        return raw


def geometry(logs, valid):
    """Invertible OHLC coordinates; reject incompatible valid sources."""
    result=logs.clone()
    for base in (0,6,12):
        o,h,l,c=logs[...,base:base+4].unbind(-1)
        m=valid[...,base:base+4]
        complete=m.all(-1)
        upper=h-torch.maximum(o,c);lower=torch.minimum(o,c)-l
        if ((upper<0)&complete).any() or ((lower<0)&complete).any():
            raise ValueError('Valid source OHLC ordering violation')
        result[...,base+1]=torch.where(complete,upper,0).sqrt()
        result[...,base+2]=torch.where(complete,lower,0).sqrt()
        result[...,base+3]=c-o
    result[...,ACTIVITY]=logs[...,ACTIVITY].sqrt()
    return result


class Residual(nn.Module):
    def __init__(self,width):
        super().__init__()
        self.block=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,width),nn.GELU(),nn.Linear(width,width))
    def forward(self,x):return x+self.block(x)*.25


class Autoencoder(nn.Module):
    def __init__(self,config,mean,scale):
        super().__init__();self.config=config;self.transform=Transform(mean,scale)
        self.input_width=90 if config.variant=='geometry' else 54
        self.backbone=nn.Sequential(nn.Linear(64*self.input_width,config.width),nn.GELU(),
            *[Residual(config.width) for _ in range(config.layers)],nn.LayerNorm(config.width))
        self.export=nn.Linear(config.width,config.embedding-21)
        self.decoder=nn.Linear(config.embedding,64*18)
        # Small shape residuals initially; nonzero wick roots retain gradients.
        nn.init.normal_(self.decoder.weight,std=.0005);nn.init.zeros_(self.decoder.bias)

    def encode(self,values,valid,positions):
        m=valid[...,:18];logs=values[...,:18]
        mixed=logs.clone();mixed[...,ACTIVITY]=logs[...,ACTIVITY].sqrt()
        refs=torch.where(m,mixed,0).sum(1)/m.sum(1).clamp_min(1)
        centered=values.clone();centered[...,:18]=torch.where(m,mixed-refs[:,None],0)
        centered[...,PRICES]*=100
        source=torch.cat((torch.where(valid,centered,0),valid.float()),-1)
        if self.config.variant=='geometry':
            shaped=geometry(logs,m)
            for base in (0,6,12):shaped[...,base]-=refs[:,None,base]
            shaped[...,ACTIVITY]-=refs[:,None,ACTIVITY]
            gm=m.clone()
            for base in (0,6,12):
                gm[...,base:base+4]=m[...,base:base+4].all(-1,keepdim=True)
                shaped[...,base]*=100;shaped[...,base+3]*=100
                shaped[...,base+1:base+3]*=10
            source=torch.cat((source,torch.where(gm,shaped,0),gm.float()),-1)
        grid=source.new_zeros(len(values),64,self.input_width)
        grid.scatter_(1,positions[...,None].expand(-1,-1,self.input_width),source)
        learned=self.export(self.backbone(grid.flatten(1)))
        coverage=torch.stack((m[...,9].float().mean(1),m[...,15].float().mean(1),
            (m[...,9]&m[...,15]).float().mean(1)),1)
        return torch.cat((refs,coverage,learned),-1)

    def decode(self,z):
        # Only exported z and fixed constants; no target metadata or input bypass.
        code=self.decoder(z.float()).reshape(-1,64,18)
        columns=[None]*18
        for base in (0,6,12):
            o=z[:,None,base]+code[...,base]*.01
            c=o+code[...,base+3]*.01
            columns[base]=o;columns[base+3]=c
            columns[base+1]=torch.maximum(o,c)+(code[...,base+1]*.1+.01).square()
            columns[base+2]=torch.minimum(o,c)-(code[...,base+2]*.1+.01).square()
            for j in (base+4,base+5):columns[j]=(z[:,None,j]+code[...,j]).square()
        return torch.stack(columns,-1)

    def forward(self,values,valid,positions):
        z=self.encode(values,valid,positions)
        return z,self.decode(z)


def objective(prediction,target,valid,scale):
    residual=prediction.float()-target.float()
    residual=residual.clone();residual[...,PRICES]*=100
    loss=torch.nn.functional.smooth_l1_loss(residual,torch.zeros_like(residual),reduction='none',beta=1.)
    channel=torch.where(valid,loss,0).sum((0,1))/valid.sum((0,1)).clamp_min(1)
    return .5*(channel[PRICES].mean()+channel[ACTIVITY].mean()),channel
