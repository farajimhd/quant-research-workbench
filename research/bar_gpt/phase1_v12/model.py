from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from ..phase0_v2.model import AttentionBlock


@dataclass(frozen=True)
class Config:
    width:int=128
    layers:int=2
    heads:int=4
    embedding:int=256
    decoder_width:int=64
    visible:int=16
    precision:str='bf16'
    visible_weight:float=.1


def positions(n,device,visible=16,generator=None):
    if visible==64:return torch.arange(64,device=device).expand(n,-1)
    return torch.rand(n,64,device=device,generator=generator).argsort(1)[:,:visible].sort(1).values


def gather(x,pos):
    return x.gather(1,pos[...,None].expand(-1,-1,x.shape[-1]))


class Encoder(nn.Module):
    def __init__(self,c,mean,scale):
        super().__init__();self.config=c
        self.register_buffer('mean',torch.as_tensor(mean,dtype=torch.float32).clone())
        self.register_buffer('scale',torch.as_tensor(scale,dtype=torch.float32).clone())
        if self.mean.shape!=(16,) or self.scale.shape!=(16,) or not (self.scale>0).all():raise ValueError('Invalid training latent calibration')
        self.stem=nn.Linear(52,c.width)  #16 latents,9 time,27 source validity
        self.position=nn.Parameter(torch.randn(1,64,c.width)*.01)
        self.cls=nn.Parameter(torch.randn(1,1,c.width)*.01)
        self.blocks=nn.Sequential(*[AttentionBlock(c.width,c.heads) for _ in range(c.layers)],nn.LayerNorm(c.width))
        self.readout=nn.Linear(c.width,c.embedding)

    def forward(self,visible_latents,visible_metadata,pos):
        # API accepts selected tokens only. Hidden targets never enter this module.
        x=torch.cat(((visible_latents-self.mean)/self.scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1))[:,0]
        return self.readout(h.float())


class Autoencoder(nn.Module):
    def __init__(self,c,mean,scale):
        super().__init__();self.config=c;self.encoder=Encoder(c,mean,scale)
        d=c.decoder_width
        self.decoder=nn.ModuleDict(dict(project=nn.Linear(c.embedding,d),body=nn.Sequential(nn.Linear(d,d),nn.GELU(),nn.Linear(d,16))))
        self.queries=nn.Parameter(torch.randn(1,64,d)*.02)

    def decode(self,embedding):
        # Only the exported embedding and fixed learned position queries. FP32 output.
        h=F.gelu(self.decoder['project'](embedding.float())[:,None]+self.queries)
        return self.decoder['body'](h)

    def forward(self,latent,metadata,pos):
        return self.decode(self.encoder(gather(latent,pos),gather(metadata,pos),pos))

    def denormalize(self,prediction):return prediction*self.encoder.scale+self.encoder.mean


def objective(model,prediction,latent,valid,pos):
    target=(latent-model.encoder.mean)/model.encoder.scale
    element=F.smooth_l1_loss(prediction,target,reduction='none').mean(-1)
    known=torch.zeros_like(valid).scatter(1,pos,True)
    def mean(mask):return torch.where(mask,element,0).sum()/mask.sum().clamp_min(1)
    hidden=mean(valid&~known);visible=mean(valid&known)
    loss=hidden+model.config.visible_weight*visible if pos.shape[1]<64 else visible
    return loss,torch.stack((hidden,visible))
