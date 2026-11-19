from dataclasses import dataclass,asdict
import torch
from torch import nn
from torch.nn import functional as F
from ..phase1_v11.transforms import decode,inverse,PRICES,ACTIVITY,LEVELS
from ..phase2_v1.config import FRAME_IDS,POSITIONS
from ..v5.encoder_v3 import AttentionBlock


@dataclass(frozen=True)
class Config:
    width:int=256
    layers:int=2
    heads:int=4
    feedforward:int=1024
    dropout:float=0.
    price_factor:float=1000.


class ContextEncoder(nn.Module):
    def __init__(self,c):
        super().__init__();self.config=c
        self.frame=nn.Embedding(5,c.width);self.position=nn.Embedding(8,c.width)
        self.age=nn.Linear(2,c.width)
        self.blocks=nn.Sequential(*[AttentionBlock(c.width,c.heads,c.feedforward,c.dropout,'sdpa',c.dropout) for _ in range(c.layers)])
        self.norm=nn.LayerNorm(c.width);self.readout=nn.Linear(c.width,c.width)
        self.register_buffer('frames',torch.tensor(FRAME_IDS),persistent=False)
        self.register_buffer('positions',torch.tensor(POSITIONS),persistent=False)
        nn.init.zeros_(self.readout.weight);nn.init.zeros_(self.readout.bias)

    def forward(self,tokens,ages):
        if tokens.shape[1:]!=(16,256) or ages.shape!=(*tokens.shape[:2],2):raise ValueError('Expected16x256 embeddings and16x2 causal ages')
        h=tokens+self.frame(self.frames)+self.position(self.positions)+self.age(ages)
        h=self.blocks(h)
        # Preserve the latest-window content at initialization. This is part of
        # the learned export; the decoder receives only this final vector.
        return tokens[:,7].float()+self.readout(self.norm(h[:,7])).float()


class ForecastDecoder(nn.Module):
    def __init__(self):
        super().__init__();self.anchor=nn.Linear(256,18)
        self.residual=nn.Sequential(nn.Linear(256,256),nn.GELU(),nn.Linear(256,64*18))
        self.register_buffer('factor',torch.tensor([100. if j%6<4 else 1. for j in range(18)]))
        nn.init.normal_(self.residual[-1].weight,std=1e-5);nn.init.zeros_(self.residual[-1].bias)

    def forward(self,z):
        # FP32 head is mandatory: absolute cheap-stock scale must not be rounded
        # by BF16 readout. All predicted quantities, including levels, come from z.
        with torch.autocast(z.device.type,enabled=False):
            code=self.anchor(z.float())[:,None]+self.residual(z.float()).reshape(-1,64,18)/self.factor
            return decode(code,code.new_zeros((len(code),9)))

    @torch.no_grad()
    def initialize_scale(self,reference_weight,reference_bias):
        self.anchor.weight.zero_();self.anchor.bias.zero_()
        for i,j in enumerate(LEVELS):
            self.anchor.weight[j,-32:]=reference_weight[i];self.anchor.bias[j]=reference_bias[i]
        for j in (2,3,8,9,14,15):self.anchor.bias[j]=.001


class Forecaster(nn.Module):
    def __init__(self,config=Config()):
        super().__init__();self.config=config;self.encoder=ContextEncoder(config);self.decoder=ForecastDecoder()
    def forward(self,tokens,ages):
        z=self.encoder(tokens,ages);return z,self.decoder(z)
    def training_prediction(self,tokens,ages):return self(tokens,ages)[1],None
    def training_loss(self,predicted,target,ids=None):return loss(predicted,target,self.config.price_factor)
    @torch.no_grad()
    def encode_context(self,tokens,ages):
        if self.training:raise ValueError('Export requires eval mode')
        return self.encoder(tokens,ages)
    @torch.no_grad()
    def predict(self,tokens,ages):
        z,logs=self(tokens,ages);return dict(embedding=z,bars=inverse(logs))


def loss(predicted,target,price_factor=1000.):
    residual=(predicted-target['logs']).reshape(-1,64,3,6);mask=target['valid'].reshape(-1,64,3,6)
    def family(error,valid):
        element=F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none')
        return (torch.where(valid,element,0).sum((0,1))/valid.sum((0,1)).clamp_min(1)).mean()
    return .5*(family(residual[...,:4]*price_factor,mask[...,:4])+family(residual[...,4:],mask[...,4:]))
