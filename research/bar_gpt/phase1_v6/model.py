"""Decoder consumes only a compact exported vector and fixed position parameters."""
from dataclasses import dataclass, asdict
import torch
from torch import nn
from ..phase1_v5_raw.model import Encoder, Config as TokenConfig

PRICES = [0,1,2,3,6,7,8,9,12,13,14,15]
ACTIVITY = [4,5,10,11,16,17]


@dataclass(frozen=True)
class Config:
    variant: str = 'pooled'
    width: int = 128
    embedding: int = 256
    layers: int = 3
    decoder: int = 256
    visible: int = 16


class Transform(nn.Module):
    def __init__(self, mean, scale):
        super().__init__()
        self.register_buffer('mean',torch.as_tensor(mean,dtype=torch.float32))
        self.register_buffer('scale',torch.as_tensor(scale,dtype=torch.float32))

    @staticmethod
    def logs(raw, valid):
        safe=torch.where(valid,raw,torch.ones_like(raw)).double()
        if not torch.isfinite(safe).all() or (safe[...,PRICES]<=0).any() or (safe[...,ACTIVITY]<0).any():
            raise ValueError('Invalid valid market data; no clipping or silent sanitization')
        result=safe.clone()
        result[...,PRICES]=safe[...,PRICES].log()
        result[...,ACTIVITY]=safe[...,ACTIVITY].log1p()
        return torch.where(valid,result,0)

    def inverse(self, normalized):
        # No raw target, per-window reference, frame or target metadata accepted.
        logs=normalized.double()*self.scale.double()+self.mean.double()
        raw=logs.clone()
        raw[...,PRICES]=logs[...,PRICES].exp()
        raw[...,ACTIVITY]=logs[...,ACTIVITY].expm1()
        return raw


class Autoencoder(nn.Module):
    def __init__(self, config, mean, scale):
        super().__init__()
        self.config=config
        if config.variant not in ('pooled','scale_slots'):raise ValueError(config.variant)
        self.transform=Transform(mean,scale)
        token=TokenConfig(width=config.width,embedding_width=256,layers=config.layers,
            feedforward=config.width*4,decoder_width=config.decoder,feature_drop=0.)
        self.backbone=Encoder(token)
        self.export=nn.Linear(256,config.embedding-(18 if config.variant=='scale_slots' else 0))
        self.decoder=nn.Sequential(nn.Linear(config.embedding,config.decoder),nn.GELU(),
            nn.Linear(config.decoder,config.decoder),nn.GELU(),nn.Linear(config.decoder,64*18))

    def encode(self, values, valid, positions):
        if self.config.variant=='scale_slots':
            m=valid[...,:18]
            # Statistics of VISIBLE INPUTS only. They are part of the exported vector.
            refs=torch.where(m,values[...,:18],0).sum(1)/m.sum(1).clamp_min(1)
            centered=values.clone()
            centered[...,:18]=torch.where(m,values[...,:18]-refs[:,None],0)
            learned=self.export(self.backbone(centered,valid,positions))
            return torch.cat((refs,learned),-1)
        return self.export(self.backbone(values,valid,positions))

    def decode(self, embedding):
        with torch.autocast(device_type=embedding.device.type,enabled=False):
            prediction=self.decoder(embedding.float()).reshape(-1,64,18)
            if self.config.variant=='scale_slots':prediction=prediction+embedding[:,:18,None].transpose(1,2)
            return prediction

    def forward(self, values, valid, positions):
        z=self.encode(values,valid,positions)
        return z,self.decode(z)


def objective(prediction, target, valid, scale):
    # Price log-relative errors; activity in log1p units. No global-std loss attenuation.
    residual=(prediction.float()-target.float())*scale.float()
    loss=torch.nn.functional.smooth_l1_loss(residual,torch.zeros_like(residual),reduction='none',beta=.01)
    n=valid.sum((0,1))
    channel=torch.where(valid,loss,0).sum((0,1))/n.clamp_min(1)
    return .5*(channel[PRICES].mean()+channel[ACTIVITY].mean()), channel
