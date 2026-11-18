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
    attention:str='standard'


class NormalizedAttentionBlock(nn.Module):
    """Bounded QK logits; raw inputs/targets and value channels are not clipped."""
    def __init__(self,width,dropout):
        super().__init__();self.heads=width//32;self.dropout=dropout
        self.norm1=nn.LayerNorm(width);self.norm2=nn.LayerNorm(width)
        self.qkv=nn.Linear(width,3*width);self.projection=nn.Linear(width,width)
        self.linear1=nn.Linear(width,4*width);self.linear2=nn.Linear(4*width,width)
        nn.init.xavier_uniform_(self.qkv.weight);nn.init.zeros_(self.qkv.bias)

    def forward(self,h):
        b,t,w=h.shape
        q,k,v=self.qkv(self.norm1(h)).reshape(b,t,3,self.heads,32).permute(2,0,3,1,4).unbind(0)
        q=F.normalize(q.float(),dim=-1,eps=1e-6).to(q.dtype)
        k=F.normalize(k.float(),dim=-1,eps=1e-6).to(k.dtype)
        a=F.scaled_dot_product_attention(q,k,v,dropout_p=self.dropout if self.training else 0.,scale=8.)
        h=h+F.dropout(self.projection(a.transpose(1,2).reshape(b,t,w)),self.dropout,self.training)
        y=self.linear2(F.dropout(F.gelu(self.linear1(self.norm2(h))),self.dropout,self.training))
        return h+F.dropout(y,self.dropout,self.training)


class Encoder(nn.Module):
    def __init__(self,c,mean,scale):
        super().__init__();self.config=c
        self.register_buffer('mean',mean.clone());self.register_buffer('scale',scale.clone())
        self.register_buffer('activity_indices',torch.tensor(ACTIVITY))
        self.register_buffer('price_indices',torch.tensor(PRICES))
        self.register_buffer('relative_factor',torch.tensor([100. if i in PRICES else 1. for i in range(18)]))
        self.stem=nn.Linear(54,c.width);self.position=nn.Parameter(torch.randn(1,64,c.width)*.01)
        self.latents=nn.Parameter(torch.randn(1,c.latent_tokens,c.width)*.01)
        if c.attention=='qknorm':
            self.backbone=nn.Sequential(*[NormalizedAttentionBlock(c.width,c.dropout) for _ in range(c.layers)],nn.LayerNorm(c.width))
        elif c.attention=='standard':
            self.backbone=attention(c.width,c.layers,c.dropout)
            for block in self.backbone.layers:
                nn.init.xavier_uniform_(block.self_attn.in_proj_weight)
                if block.self_attn.in_proj_bias is not None:nn.init.zeros_(block.self_attn.in_proj_bias)
        else:raise ValueError('Unknown attention contract')
        self.shape=nn.Linear(c.latent_tokens*c.width,c.embedding-c.context)
        self.context=nn.Sequential(nn.Linear(138,c.context),nn.Linear(c.context,c.context))

    def forward(self,x,valid,positions):
        # x contains ONLY the selected bars. Hidden targets cannot enter this API.
        v=valid;count=v.sum(1);mean=torch.where(v,x,0).sum(1)/count.clamp_min(1)
        mean=torch.where(count>0,mean,self.mean)
        observed=v[:,-1,:18];last=torch.where(observed,x[:,-1,:18],mean[:,:18])
        first=torch.where(v[:,0,:18],x[:,0,:18],mean[:,:18])
        av=v[...,self.activity_indices];activity=torch.where(av,x[...,self.activity_indices],0).sqrt()
        am=activity.sum(1)/av.sum(1).clamp_min(1)
        am=torch.where(av.any(1),am,self.mean[self.activity_indices].sqrt())
        factor=self.relative_factor
        variance=torch.where(v[...,:18],(x[...,:18]-mean[:,None,:18]).square(),0).sum(1)/count[:,:18].clamp_min(1)
        summary=torch.cat(((mean-self.mean)/self.scale,v.float().mean(1),
            (last-self.mean[:18])/self.scale[:18],observed.float(),am/4,last[:,self.activity_indices].sqrt()/4,
            variance.sqrt()*factor,(last-first)*factor),-1)
        market=torch.asinh((x[...,:18]-mean[:,None,:18])*factor)
        market[...,self.activity_indices]=torch.asinh(activity-am[:,None])
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
    factor=model.encoder.relative_factor
    loss=F.smooth_l1_loss((p-target[...,:18])*factor,torch.zeros_like(p),reduction='none')
    channel=torch.where(mask[...,:18],loss,0).sum((0,1))/mask[...,:18].sum((0,1)).clamp_min(1)
    tm=F.smooth_l1_loss(t,(target[...,18:]-model.input_mean[18:])/model.input_scale[18:],reduction='none')
    tm=torch.where(mask[...,18:],tm,0).sum()/mask[...,18:].sum().clamp_min(1)
    binary=F.binary_cross_entropy_with_logits(vl,valid.float(),reduction='none')
    positive=valid&hidden[...,None];negative=(~valid)&hidden[...,None]
    vm=.5*(torch.where(positive,binary,0).sum()/positive.sum().clamp_min(1)+torch.where(negative,binary,0).sum()/negative.sum().clamp_min(1))
    value=.5*(model.config.price_scale*channel[model.encoder.price_indices].mean()+channel[model.encoder.activity_indices].mean())+.05*(tm+vm)
    return value,channel


def learning_rate(step,total,peak=1e-3,floor=1e-5):
    import math
    return floor+(peak-floor)*.5*(1+math.cos(math.pi*min(1,step/max(1,total-1))))
