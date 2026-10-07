"""Bounded actual-candle encoders shared by teacher and streaming policy.

The input remains 37 scalars and ten V7 slots, including elapsed clock gaps.
No episode identity, label or future outcome enters this module.
"""
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.checkpoint import checkpoint
from research.rl_trading.v6.model import ActualCandleEncoder,INPUT_WIDTH

VERSION='rl-v6-structured-temporal-encoder-v1'
ARCHITECTURES=('lag','mlp','tcn','gru','transformer')


class StructuredProjection(nn.Module):
    def __init__(self,width):
        super().__init__();self.in_features=INPUT_WIDTH
        self.scalar=nn.Sequential(nn.Linear(37,width),nn.GELU(),nn.LayerNorm(width))
        self.level=nn.Sequential(nn.Linear(11,32),nn.GELU(),nn.Linear(32,32))
        self.fuse=nn.Sequential(nn.Linear(width+64,width),nn.GELU(),nn.LayerNorm(width))

    def forward(self,x):
        levels=x[...,37:].reshape(*x.shape[:-1],2,5,11)
        mask=levels[...,10:11]
        sides=(self.level(levels)*mask).sum(-2)/mask.sum(-2).clamp_min(1)
        return self.fuse(torch.cat((self.scalar(x[...,:37]),sides.flatten(-2)),dim=-1))


class CausalResidual(nn.Module):
    def __init__(self,width,dilation):
        super().__init__();self.dilation=dilation
        self.first=nn.Conv1d(width,width,3,dilation=dilation)
        self.second=nn.Conv1d(width,width,3,dilation=dilation)
        self.norm1=nn.LayerNorm(width);self.norm2=nn.LayerNorm(width)

    def forward(self,x):
        y=self.first(F.pad(x,(2*self.dilation,0)))
        y=F.gelu(self.norm1(y.transpose(1,2))).transpose(1,2)
        y=self.second(F.pad(y,(2*self.dilation,0)))
        return F.gelu(x+self.norm2(y.transpose(1,2)).transpose(1,2))


class TemporalCandleEncoder(ActualCandleEncoder):
    def __init__(self,width=128,*,architecture='tcn',structured=True):
        super().__init__(width)
        if architecture not in ARCHITECTURES:raise ValueError('Unknown temporal architecture')
        self.architecture=architecture;self.encoder_version=VERSION
        self.activation_checkpointing=False
        self.reproject_history_for_gradient=architecture!='lag'
        if structured:self.project=StructuredProjection(width)
        if architecture=='tcn':self.temporal=nn.Sequential(*(CausalResidual(width,d) for d in (1,2,4,8,16,32)))
        elif architecture=='gru':self.temporal=nn.GRU(width,width,num_layers=2,batch_first=True)
        elif architecture=='transformer':
            self.position=nn.Parameter(torch.randn(120,width)*.02)
            self.temporal=nn.TransformerEncoder(nn.TransformerEncoderLayer(width,4,4*width,
                dropout=0.,activation='gelu',batch_first=True,norm_first=True),3,enable_nested_tensor=False)
        elif architecture=='mlp':self.temporal=nn.Sequential(nn.Linear(width*3,width*2),nn.GELU(),nn.Linear(width*2,width))

    def encode_history(self,history,present=None):
        if history.ndim!=3 or history.shape[1:]!=(120,self.width):raise ValueError('Expected bounded actual-candle history')
        if present is None:present=torch.ones(history.shape[:2],dtype=torch.bool,device=history.device)
        if present.shape!=history.shape[:2] or present.dtype!=torch.bool:raise ValueError('Invalid history presence mask')
        x=history*present[...,None]
        if self.architecture=='lag':return super().encode_history(x,present)
        if self.architecture=='tcn':
            temporal_input=x.transpose(1,2)
            output=(checkpoint(self.temporal,temporal_input,use_reentrant=False)
                    if self.activation_checkpointing and self.training and torch.is_grad_enabled()
                    else self.temporal(temporal_input))[:,:,-1]
        elif self.architecture=='gru':output=self.temporal(x)[0][:,-1]
        elif self.architecture=='transformer':
            safe=present.clone();safe[:,0]=True
            mask=torch.triu(torch.ones(120,120,dtype=torch.bool,device=x.device),1)
            output=self.temporal(x+self.position[None],mask=mask,src_key_padding_mask=~safe)[:,-1]
        else:
            denominator=present.sum(1).clamp_min(1)[:,None];mean=x.sum(1)/denominator
            variance=((x-mean[:,None])**2*present[...,None]).sum(1)/denominator
            output=self.temporal(torch.cat((x[:,-1],mean,torch.sqrt(variance+1e-6)),1))
        output=self.norm(output)
        return torch.where(present.any(1)[:,None],output,torch.zeros_like(output))

    def encode_listing(self,scalar,levels):
        projected=self.project(self._input(scalar,levels));outputs=[]
        if not len(projected):return projected
        padded=F.pad(projected,(0,0,119,0));mask=F.pad(torch.ones(len(projected),dtype=torch.bool,device=projected.device),(119,0))
        for start in range(0,len(projected),128):
            ends=torch.arange(start,min(start+128,len(projected)),device=projected.device)
            indices=ends[:,None]+torch.arange(120,device=projected.device)[None]
            outputs.append(self.encode_history(padded[indices],mask[indices]))
        return torch.cat(outputs)


def replace_encoder(policy,architecture,*,structured=True):
    old=policy.encoder;device=next(old.parameters()).device
    policy.encoder=TemporalCandleEncoder(old.width,architecture=architecture,structured=structured).to(device)
    return policy
