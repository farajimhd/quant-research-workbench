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
        # Fixed channel slices avoid implicit CPU index tensors during CUDA capture.
        for base in (0,6,12):
            raw[...,base:base+4]=logs[...,base:base+4].double().exp()
            raw[...,base+4:base+6]=logs[...,base+4:base+6].double().expm1()
        return raw


def geometry(logs, valid,check=True):
    """Invertible OHLC coordinates; reject incompatible valid sources."""
    result=logs.clone()
    for base in (0,6,12):
        o,h,l,c=logs[...,base:base+4].unbind(-1)
        m=valid[...,base:base+4]
        complete=m.all(-1)
        upper=h-torch.maximum(o,c);lower=torch.minimum(o,c)-l
        if check and (((upper<0)&complete).any() or ((lower<0)&complete).any()):
            raise ValueError('Valid source OHLC ordering violation')
        result[...,base+1]=torch.where(complete,upper,0).sqrt()
        result[...,base+2]=torch.where(complete,lower,0).sqrt()
        result[...,base+3]=c-o
    for base in (0,6,12):result[...,base+4:base+6]=logs[...,base+4:base+6].sqrt()
    return result


class Residual(nn.Module):
    def __init__(self,width):
        super().__init__()
        self.block=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,width),nn.GELU(),nn.Linear(width,width))
    def forward(self,x):return x+self.block(x)*.25


def quote_geometry(logs,valid,check=True):
    """Complete visible paired quotes only; canonical partial fields remain inputs."""
    complete=valid[...,6:10].all(-1)&valid[...,12:16].all(-1)
    bo,bh,bl,bc=logs[...,6:10].unbind(-1)
    ao,ah,al,ac=logs[...,12:16].unbind(-1)
    gaps=torch.stack((ao-bo,ah-torch.maximum(torch.maximum(ao,ac),bh),al-bl,ac-bc),-1)
    mask=complete[...,None].expand_as(gaps)
    if check and ((gaps<0)&mask).any():raise ValueError('Valid crossed source quote: joint representation is not applicable')
    return torch.where(mask,gaps,0).sqrt(),mask


class Autoencoder(nn.Module):
    def __init__(self,config,mean,scale):
        super().__init__();self.config=config;self.transform=Transform(mean,scale)
        self.joint=config.variant=='geometry_joint'
        self.input_width=98 if self.joint else (90 if config.variant=='geometry' else 54)
        self.backbone=nn.Sequential(nn.Linear(64*self.input_width,config.width),nn.GELU(),
            *[Residual(config.width) for _ in range(config.layers)],nn.LayerNorm(config.width))
        self.export=nn.Linear(config.width,config.embedding-(25 if self.joint else 21))
        self.decoder=nn.Linear(config.embedding,64*18)
        # Small shape residuals initially; nonzero wick roots retain gradients.
        nn.init.normal_(self.decoder.weight,std=.0005);nn.init.zeros_(self.decoder.bias)

    def encode(self,values,valid,positions):
        grid,slots=Autoencoder.prepare(self,values,valid,positions)
        return Autoencoder.encode_prepared(self,grid,slots)

    def prepare(self,values,valid,positions,check=True):
        """Deterministic CPU-worker preparation, identical to the original graph."""
        m=valid[...,:18];logs=values[...,:18]
        mixed=logs.clone()
        for base in (0,6,12):mixed[...,base+4:base+6]=logs[...,base+4:base+6].sqrt()
        refs=torch.where(m,mixed,0).sum(1)/m.sum(1).clamp_min(1)
        centered=values.clone();centered[...,:18]=torch.where(m,mixed-refs[:,None],0)
        for base in (0,6,12):centered[...,base:base+4]*=100
        source=torch.cat((torch.where(valid,centered,0),valid.float()),-1)
        pair_refs=None
        if self.config.variant in ('geometry','geometry_joint'):
            shaped=geometry(logs,m,check)
            for base in (0,6,12):shaped[...,base]-=refs[:,None,base]
            for base in (0,6,12):shaped[...,base+4:base+6]-=refs[:,None,base+4:base+6]
            gm=m.clone()
            for base in (0,6,12):
                gm[...,base:base+4]=m[...,base:base+4].all(-1,keepdim=True)
                shaped[...,base]*=100;shaped[...,base+3]*=100
                shaped[...,base+1:base+3]*=10
            source=torch.cat((source,torch.where(gm,shaped,0),gm.float()),-1)
        if self.joint:
            pair,pm=quote_geometry(logs,m,check)
            pair_refs=torch.where(pm,pair,0).sum(1)/pm.sum(1).clamp_min(1)
            source=torch.cat((source,torch.where(pm,(pair-pair_refs[:,None])*100,0),pm.float()),-1)
        if positions is None:
            if values.shape[1]!=64:raise ValueError('Ordered-grid shortcut requires all 64 bars')
            grid=source
        else:
            grid=source.new_zeros(len(values),64,self.input_width)
            grid.scatter_(1,positions[...,None].expand(-1,-1,self.input_width),source)
        coverage=torch.stack((m[...,9].float().mean(1),m[...,15].float().mean(1),
            (m[...,9]&m[...,15]).float().mean(1)),1)
        slots=torch.cat((refs,coverage,pair_refs),-1) if self.joint else torch.cat((refs,coverage),-1)
        return grid.flatten(1),slots

    def encode_prepared(self,grid,slots,precision='fp32'):
        with torch.autocast(grid.device.type,dtype=torch.bfloat16,enabled=precision=='bf16'):
            hidden=self.backbone(grid)
        # Preserve absolute scale and small residuals at the exported boundary.
        with torch.autocast(grid.device.type,enabled=False):
            learned=self.export(hidden.float())
            return torch.cat((slots.float(),learned),-1)

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
        if self.joint:
            ao=columns[6]+(z[:,None,21]+code[...,12]*.01).square()
            ac=columns[9]+(z[:,None,24]+code[...,15]*.01).square()
            ah=torch.maximum(torch.maximum(ao,ac),columns[7])+(z[:,None,22]+code[...,13]*.01).square()
            al=torch.minimum(torch.minimum(ao,ac),columns[8]+(z[:,None,23]+code[...,14]*.01).square())
            columns[12:16]=[ao,ah,al,ac]
        return torch.stack(columns,-1)

    def forward(self,values,valid,positions):
        z=self.encode(values,valid,positions)
        return z,self.decode(z)


def objective(prediction,target,valid,scale):
    residual=prediction.float()-target.float()
    residual=residual.clone()
    for base in (0,6,12):residual[...,base:base+4]*=100
    loss=torch.nn.functional.smooth_l1_loss(residual,torch.zeros_like(residual),reduction='none',beta=1.)
    channel=torch.where(valid,loss,0).sum((0,1))/valid.sum((0,1)).clamp_min(1)
    price=torch.cat([channel[base:base+4] for base in (0,6,12)])
    activity=torch.cat([channel[base+4:base+6] for base in (0,6,12)])
    return .5*(price.mean()+activity.mean()),channel
