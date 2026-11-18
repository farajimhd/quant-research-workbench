"""One reference token, learned attention pooling, no direct scale/readout bypass."""
from dataclasses import asdict
import torch
from torch import nn
from torch.nn import functional as F
from .config import Config
from .transforms import coordinates,references,center,decode,PRICES,ACTIVITY,REF_PRICES,REF_ACTIVITY
from ..phase1_v10.model import NormalizedAttentionBlock,select


class Encoder(nn.Module):
    def __init__(self,c,parameters):
        super().__init__();self.config=c
        for k in ('time_mean','time_scale','reference_mean','reference_scale'):
            self.register_buffer(k,torch.as_tensor(parameters[k],dtype=torch.float32).clone())
        self.register_buffer('factor',torch.tensor([100. if j in PRICES else 1. for j in range(18)]))
        self.register_buffer('input_factor',torch.tensor([c.input_price_scale if j in PRICES else 1. for j in range(18)]),persistent=False)
        self.register_buffer('reference_factor',torch.tensor([100.,1.,1.]*3))
        self.stem=nn.Linear(54,c.width);self.reference_stem=nn.Linear(18,c.width)
        self.position=nn.Parameter(torch.randn(1,64,c.width)*.01)
        self.reference_type=nn.Parameter(torch.randn(1,1,c.width)*.01)
        self.latents=nn.Parameter(torch.randn(1,c.latent_tokens,c.width)*.01)
        self.blocks=nn.Sequential(*[NormalizedAttentionBlock(c.width,c.dropout) for _ in range(c.layers)],nn.LayerNorm(c.width))
        self.readout=nn.Linear(c.latent_tokens*c.width,c.embedding-(32 if c.readout=='split_scale' else 0))
        if c.readout=='split_scale':self.scale_projection=nn.Linear(18,32)

    def prepare(self,x,v):
        code,cm=coordinates(x[...,:18],v[...,:18]);ref,rv=references(x[...,:18],v[...,:18],self.reference_mean)
        if not self.config.center:ref=torch.zeros_like(ref)
        z=center(code,ref)*self.input_factor
        if self.config.input_asinh:z=torch.asinh(z)
        t=(x[...,18:]-self.time_mean)/self.time_scale
        values=torch.cat((torch.where(cm,z,0),torch.where(v[...,18:],t,0)),dim=-1)
        bars=torch.cat((values,v.float()),-1)
        token=torch.cat(((ref-self.reference_mean)/self.reference_scale,rv.float()),-1)
        return bars,token,ref,rv

    def forward(self,x,v,positions):
        bars,ref,_,_=self.prepare(x,v);c=self.config
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda and c.precision=='bf16'):
            h=self.stem(bars)+self.position[0,positions]
            r=self.reference_stem(ref).unsqueeze(1)+self.reference_type
            h=self.blocks(torch.cat((self.latents.expand(len(x),-1,-1),r,h),1))
            if c.readout in ('latents','split_scale'):h=h[:,:c.latent_tokens]
            elif c.readout=='include_reference':h=torch.cat((h[:,:c.latent_tokens-1],h[:,c.latent_tokens:c.latent_tokens+1]),1)
            else:raise ValueError('Unknown fixed embedding readout')
        z=self.readout(h.float().flatten(1))
        if c.readout=='split_scale':z=torch.cat((z,self.scale_projection(ref.float())),-1)
        return z


class Autoencoder(nn.Module):
    def __init__(self,c,parameters):
        super().__init__();self.config=c;self.encoder=Encoder(c,parameters)
        self.decoder=nn.Linear(c.embedding,64*54);self.reference_head=nn.Linear(32 if c.readout=='split_scale' else c.embedding,9)
        nn.init.normal_(self.decoder.weight,std=.0005);nn.init.zeros_(self.decoder.bias)
        nn.init.normal_(self.reference_head.weight,std=.0005)
        with torch.no_grad():self.reference_head.bias.copy_(self.encoder.reference_mean)

    def decode(self,z):
        y=self.decoder(z.float()).reshape(-1,64,54);ref=self.reference_head(z[:,-32:].float() if self.config.readout=='split_scale' else z.float())
        if not self.config.center:ref=ref*0
        code=y[...,:18]/self.encoder.factor
        return dict(logs=decode(code,ref),code=code,references=ref,time=y[...,18:27],validity=y[...,27:])

    def forward(self,x,v,positions):return self.decode(self.encoder(x,v,positions))


def masked_mean(x,mask):return torch.where(mask,x,0).sum()/mask.sum().clamp_min(1)


def objective(model,out,target,valid,hidden,visible_values,visible_valid):
    c=model.config;market=valid[...,:18]&hidden[...,None]
    err=(out['logs']-target[...,:18])*model.encoder.factor
    element=F.smooth_l1_loss(err,torch.zeros_like(err),reduction='none')
    channels=torch.where(market,element,0).sum((0,1))/market.sum((0,1)).clamp_min(1)
    price=channels.reshape(3,6)[:,:4].mean();activity=channels.reshape(3,6)[:,4:].mean()
    refs,rm=references(visible_values[...,:18],visible_valid[...,:18],model.encoder.reference_mean)
    rf=model.encoder.reference_factor;re=F.smooth_l1_loss((out['references']-refs)*rf,torch.zeros_like(refs),reduction='none')
    re=re.reshape(-1,3,3);rm=rm.reshape(-1,3,3)
    rp=masked_mean(re[:,:,0],rm[:,:,0]);ra=masked_mean(re[:,:,1:],rm[:,:,1:])
    if not c.center:rp=rp*0;ra=ra*0
    tc,cm=coordinates(target[...,:18],valid[...,:18]);tc=center(tc,refs if c.center else refs*0)
    co=torch.stack([out['code'][...,j].abs() if j%6 in (2,3) else out['code'][...,j] for j in range(18)],-1)
    ce=F.smooth_l1_loss((co-tc)*model.encoder.factor,torch.zeros_like(tc),reduction='none')
    coordinate=masked_mean(ce,cm&hidden[...,None])
    te=F.smooth_l1_loss(out['time'],(target[...,18:]-model.encoder.time_mean)/model.encoder.time_scale,reduction='none')
    timing=masked_mean(te,valid[...,18:]&hidden[...,None])
    ve=F.binary_cross_entropy_with_logits(out['validity'],valid.float(),reduction='none')
    validity=.5*(masked_mean(ve,valid&hidden[...,None])+masked_mean(ve,(~valid)&hidden[...,None]))
    total=.5*(price+activity)+c.reference_weight*(rp+.25*ra)+.05*(coordinate+timing+validity)
    visible=total*0
    if c.visible_weight:
        known=~hidden;vm=valid[...,:18]&known[...,None]
        vc=torch.where(vm,element,0).sum((0,1))/vm.sum((0,1)).clamp_min(1)
        vp=vc.reshape(3,6)[:,:4].mean();va=vc.reshape(3,6)[:,4:].mean()
        vcoord=masked_mean(ce,cm&known[...,None]);vt=masked_mean(te,valid[...,18:]&known[...,None])
        vv=.5*(masked_mean(ve,valid&known[...,None])+masked_mean(ve,(~valid)&known[...,None]))
        visible=.5*(vp+va)+.05*(vcoord+vt+vv);total=total+c.visible_weight*visible
    return total,dict(price=price,activity=activity,reference_price=rp,reference_activity=ra,coordinate=coordinate,time=timing,validity=validity,visible=visible)
