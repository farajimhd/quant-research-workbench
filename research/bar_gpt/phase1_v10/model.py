"""Physically selected bar tokens; learned fixed latent; one affine decoder."""
from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from ..joint_v9.model import attention,structured
from ..phase1_v8.model import PRICES,ACTIVITY,inverse


@dataclass
class Config:
    width:int=384
    layers:int=6
    embedding:int=768
    context:int=192
    latent_tokens:int=4
    visible:int=16
    dropout:float=.05
    precision:str='bf16'
    price_scale:float=.02


class Encoder(nn.Module):
    def __init__(self,c,mean,scale):
        super().__init__();self.config=c
        self.register_buffer('mean',mean.clone());self.register_buffer('scale',scale.clone())
        self.stem=nn.Linear(54,c.width);self.position=nn.Parameter(torch.randn(1,64,c.width)*.01)
        self.latents=nn.Parameter(torch.randn(1,c.latent_tokens,c.width)*.01)
        self.backbone=attention(c.width,c.layers,c.dropout)
        # TransformerEncoder clones its prototype; independently initialize QKV.
        for block in self.backbone.layers:
            nn.init.xavier_uniform_(block.self_attn.in_proj_weight)
            if block.self_attn.in_proj_bias is not None:nn.init.zeros_(block.self_attn.in_proj_bias)
        self.shape=nn.Linear(c.latent_tokens*c.width,c.embedding-c.context)
        self.context=nn.Sequential(nn.Linear(138,c.context),nn.Linear(c.context,c.context))

    def forward(self,x,valid,positions):
        # x contains ONLY the selected bars. Hidden targets cannot enter this API.
        v=valid;count=v.sum(1);mean=torch.where(v,x,0).sum(1)/count.clamp_min(1)
        mean=torch.where(count>0,mean,self.mean)
        observed=v[:,-1,:18];last=torch.where(observed,x[:,-1,:18],mean[:,:18])
        first=torch.where(v[:,0,:18],x[:,0,:18],mean[:,:18])
        av=v[...,list(ACTIVITY)];activity=torch.where(av,x[...,list(ACTIVITY)],0).sqrt()
        am=activity.sum(1)/av.sum(1).clamp_min(1)
        am=torch.where(av.any(1),am,self.mean[list(ACTIVITY)].sqrt())
        factor=x.new_tensor([100 if i in PRICES else 1 for i in range(18)])
        variance=torch.where(v[...,:18],(x[...,:18]-mean[:,None,:18]).square(),0).sum(1)/count[:,:18].clamp_min(1)
        summary=torch.cat(((mean-self.mean)/self.scale,v.float().mean(1),
            (last-self.mean[:18])/self.scale[:18],observed.float(),am/4,last[:,list(ACTIVITY)].sqrt()/4,
            variance.sqrt()*factor,(last-first)*factor),-1)
        market=torch.asinh((x[...,:18]-mean[:,None,:18])*factor)
        market[...,list(ACTIVITY)]=torch.asinh(activity-am[:,None])
        normalized=torch.cat((market,(x[...,18:]-self.mean[18:])/self.scale[18:]),-1)
        tokens=torch.cat((torch.where(v,normalized,0),v.float()),-1)
        c=self.config
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda and c.precision=='bf16'):
            h=self.stem(tokens)+self.position[0,positions]
            h=self.backbone(torch.cat((self.latents.expand(len(x),-1,-1),h),1))[:,:c.latent_tokens]
        return torch.cat((self.shape(h.float().flatten(1)),self.context(summary.float())),-1)


class Autoencoder(nn.Module):
    def __init__(self,c,parameters):
        super().__init__();self.config=c
        for k,v in parameters.items():self.register_buffer(k,torch.as_tensor(v,dtype=torch.float32).clone())
        self.encoder=Encoder(c,self.input_mean,self.input_scale)
        self.decoder=nn.Linear(c.embedding,64*54)
        nn.init.normal_(self.decoder.weight,std=.0005);nn.init.zeros_(self.decoder.bias)

    def decode(self,z):
        y=self.decoder(z.float()).reshape(-1,64,54)
        return structured(y[...,:18]*self.output_scale+self.output_mean),y[...,18:27],y[...,27:]

    def forward(self,x,v,positions):return self.decode(self.encoder(x,v,positions))


def select(values,valid,visible=16,generator=None,positions=None):
    n=len(values)
    if positions is None:
        positions=(torch.arange(64,device=values.device).expand(n,-1) if visible==64 else
            torch.rand(n,64,device=values.device,generator=generator).argsort(1)[:,:visible].sort(1).values)
    take=positions[...,None].expand(-1,-1,27)
    hidden=torch.ones(n,64,dtype=torch.bool,device=values.device)
    if visible!=64:hidden.scatter_(1,positions,False)
    return values.gather(1,take),valid.gather(1,take),positions,hidden


def objective(model,output,target,valid,hidden):
    p,t,vl=output;mask=valid&hidden[...,None]
    factor=p.new_tensor([100 if i in PRICES else 1 for i in range(18)])
    loss=F.smooth_l1_loss((p-target[...,:18])*factor,torch.zeros_like(p),reduction='none')
    channel=torch.where(mask[...,:18],loss,0).sum((0,1))/mask[...,:18].sum((0,1)).clamp_min(1)
    tm=F.smooth_l1_loss(t,(target[...,18:]-model.input_mean[18:])/model.input_scale[18:],reduction='none')
    tm=torch.where(mask[...,18:],tm,0).sum()/mask[...,18:].sum().clamp_min(1)
    binary=F.binary_cross_entropy_with_logits(vl,valid.float(),reduction='none')
    positive=valid&hidden[...,None];negative=(~valid)&hidden[...,None]
    vm=.5*(torch.where(positive,binary,0).sum()/positive.sum().clamp_min(1)+torch.where(negative,binary,0).sum()/negative.sum().clamp_min(1))
    value=.5*(model.config.price_scale*channel[list(PRICES)].mean()+channel[list(ACTIVITY)].mean())+.05*(tm+vm)
    return value,channel


def learning_rate(step,total,peak=1e-3,floor=1e-5):
    import math
    return floor+(peak-floor)*.5*(1+math.cos(math.pi*min(1,step/max(1,total-1))))
