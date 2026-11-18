"""Shared temporal encoders, ordered multiscale attention, lightweight task heads."""
from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from .data import anchor
from ..phase1_v8.model import PRICES,ACTIVITY


@dataclass
class Config:
    architecture:str='patch'
    width:int=128
    embedding:int=256
    layers:int=2
    global_layers:int=2
    patch:int=8
    dropout:float=.05
    precision:str='bf16'
    summary_weight:float=0.
    price_huber_beta:float=1.


def structured(code):
    columns=[None]*18
    for base in (0,6,12):
        o=code[...,base];c=o+code[...,base+3]
        columns[base]=o;columns[base+3]=c
        columns[base+1]=torch.maximum(o,c)+code[...,base+1].square()
        columns[base+2]=torch.minimum(o,c)-code[...,base+2].square()
        for j in (base+4,base+5):columns[j]=code[...,j].square()
    ao=columns[6]+code[...,12].square();ac=columns[9]+code[...,15].square()
    ah=torch.maximum(torch.maximum(ao,ac),columns[7])+code[...,13].square()
    al=torch.minimum(torch.minimum(ao,ac),columns[8]+code[...,14].square())
    columns[12:16]=[ao,ah,al,ac]
    return torch.stack(columns,-1)


def attention(width,layers,dropout):
    layer=nn.TransformerEncoderLayer(width,width//32,width*4,dropout=dropout,activation='gelu',batch_first=True,norm_first=True)
    result=nn.TransformerEncoder(layer,layers,norm=nn.LayerNorm(width),enable_nested_tensor=False)
    for block in result.layers:
        for child in block.modules():
            if isinstance(child,nn.Linear):child.reset_parameters()
    return result


class ConvBlock(nn.Module):
    def __init__(self,width,dilation):
        super().__init__();self.net=nn.Sequential(nn.GroupNorm(1,width),nn.Conv1d(width,width,3,padding=dilation,dilation=dilation),
            nn.GELU(),nn.Conv1d(width,width,1))
    def forward(self,x):return x+.25*self.net(x)


class WindowEncoder(nn.Module):
    def __init__(self,c,mean,scale):
        super().__init__();self.config=c;self.register_buffer('input_mean',mean.clone());self.register_buffer('input_scale',scale.clone())
        w=c.width
        if c.architecture=='mlp':
            from ..phase1_v8.model import Residual
            self.backbone=nn.Sequential(nn.Linear(64*54,w*4),nn.GELU(),*[Residual(w*4) for _ in range(c.layers)],nn.LayerNorm(w*4),nn.Linear(w*4,w))
        elif c.architecture=='conv':
            self.stem=nn.Conv1d(54,w,1);self.backbone=nn.Sequential(*[ConvBlock(w,2**i) for i in range(c.layers+2)],nn.AdaptiveAvgPool1d(8),nn.Flatten(),nn.Linear(w*8,w))
        else:
            n=27 if c.architecture=='variable' else 64//(1 if c.architecture=='bar' else c.patch)
            dim=128 if c.architecture=='variable' else 54*(1 if c.architecture=='bar' else c.patch)
            self.stem=nn.Linear(dim,w);self.position=nn.Parameter(torch.randn(1,n+1,w)*.01)
            self.cls=nn.Parameter(torch.randn(1,1,w)*.01);self.backbone=attention(w,c.layers,c.dropout)
        self.context=nn.Sequential(nn.Linear(54,w),nn.GELU())
        self.export=nn.Linear(w*2,c.embedding)

    def forward(self,x,v):
        mean=torch.where(v,x,0).sum(1)/v.sum(1).clamp_min(1)
        mean=torch.where(v.any(1),mean,self.input_mean)
        summary=torch.cat(((mean-self.input_mean)/self.input_scale,v.float().mean(1)),-1)
        factor=x.new_tensor([100 if i in PRICES else 1 for i in range(18)])
        market=torch.asinh((x[...,:18]-mean[:,None,:18])*factor)
        normalized=torch.cat((market,(x[...,18:]-self.input_mean[18:])/self.input_scale[18:]),-1)
        tokens=torch.cat((torch.where(v,normalized,0),v.float()),-1)
        c=self.config
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=c.precision=='bf16' and x.device.type=='cuda'):
            if c.architecture=='mlp':hidden=self.backbone(tokens.flatten(1))
            elif c.architecture=='conv':hidden=self.backbone(self.stem(tokens.transpose(1,2)))
            else:
                if c.architecture=='variable':tokens=torch.cat((torch.where(v,normalized,0).transpose(1,2),v.float().transpose(1,2)),-1)
                else:tokens=tokens.reshape(len(x),-1,54*(1 if c.architecture=='bar' else c.patch))
                tokens=self.stem(tokens);hidden=self.backbone(torch.cat((self.cls.expand(len(x),-1,-1),tokens),1)+self.position)[:,0]
        # Absolute-scale path/readout remain FP32; BF16 is confined to shape attention.
        context=self.context(summary)
        return self.export(torch.cat((hidden.float(),context.float()),-1))


class JointModel(nn.Module):
    def __init__(self,c,**parameters):
        super().__init__();self.config=c
        for k,v in parameters.items():self.register_buffer(k,v.clone())
        self.encoder=WindowEncoder(c,self.input_mean,self.input_scale)
        e=c.embedding
        self.frame=nn.Embedding(5,e);self.slot=nn.Embedding(16,e);self.age=nn.Sequential(nn.Linear(2,e),nn.GELU(),nn.Linear(e,e))
        self.register_buffer('frame_ids',torch.tensor([0]*8+[1]*4+[2]*2+[3,4]));self.register_buffer('slot_ids',torch.arange(16))
        self.query=nn.Parameter(torch.randn(1,1,e)*.01);self.temporal=attention(e,c.global_layers,c.dropout)
        self.forecast=nn.Linear(e,64*18);nn.init.normal_(self.forecast.weight,std=.0001);nn.init.zeros_(self.forecast.bias)
        self.reconstruction=nn.Linear(e,64*54);nn.init.normal_(self.reconstruction.weight,std=.0005);nn.init.zeros_(self.reconstruction.bias)
        if c.summary_weight>0:self.future_summary=nn.Linear(e,8)

    def encode_windows(self,x,valid):return self.encoder(x.reshape(-1,64,27),valid.reshape(-1,64,27)).reshape(len(x),16,-1)

    def reconstruct(self,z):
        out=self.reconstruction(z.float()).reshape(*z.shape[:-1],64,54)
        code=out[...,:18]*self.reconstruction_scale+self.reconstruction_mean
        return structured(code),out[...,18:27],out[...,27:]

    def forward(self,x,valid,ages,reconstruct=True):
        # This signature accepts only completed history. Targets/clocks are not arguments.
        z=self.encode_windows(x,valid);c=self.config
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=c.precision=='bf16' and x.device.type=='cuda'):
            token=z+self.frame(self.frame_ids)[None]+self.slot(self.slot_ids)[None]+self.age(ages)
            joint=self.temporal(torch.cat((self.query.expand(len(x),-1,-1),token),1))[:,0]
        delta=self.forecast(joint.float()).reshape(-1,64,18)
        # The only inverse anchor is the known historical bar, never a target reference.
        prediction=structured(anchor(x,valid,check=False)[:,None]+delta*self.forecast_scale)
        return dict(forecast=prediction,windows=z,joint=joint.float(),reconstruction=self.reconstruct(z) if reconstruct else None,
            future_summary=self.future_summary(joint.float()) if c.summary_weight>0 else None)


def objective(model,output,data,reconstruction_weight=.1):
    error=(output['forecast']-data['target'])/model.loss_scale
    valid=data['target_valid'];loss=F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none')
    if model.config.price_huber_beta!=1:
        price=error.new_tensor([i in PRICES for i in range(18)],dtype=torch.bool)
        robust=F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none',beta=model.config.price_huber_beta)
        loss=torch.where(price,robust,loss)
    channel=torch.where(valid,loss,0).sum((0,1))/valid.sum((0,1)).clamp_min(1)
    forecast=.5*(channel[list(PRICES)].mean()+channel[list(ACTIVITY)].mean())
    reconstruction=forecast*0
    if output['reconstruction'] is not None and reconstruction_weight:
        p,t,vl=output['reconstruction'];x=data['x'];v=data['valid']
        residual=p-x[...,:18];factor=residual.new_tensor([100 if i in PRICES else 1 for i in range(18)])
        losses=F.smooth_l1_loss(residual*factor,torch.zeros_like(residual),reduction='none')
        channels=torch.where(v[...,:18],losses,0).sum((0,1,2))/v[...,:18].sum((0,1,2)).clamp_min(1)
        time=F.smooth_l1_loss(t,(x[...,18:]-model.input_mean[18:])/model.input_scale[18:],reduction='none')
        time=torch.where(v[...,18:],time,0).sum()/v[...,18:].sum().clamp_min(1)
        binary=F.binary_cross_entropy_with_logits(vl,v.float(),reduction='none')
        validity=.5*(torch.where(v,binary,0).sum()/v.sum().clamp_min(1)+torch.where(~v,binary,0).sum()/(~v).sum().clamp_min(1))
        reconstruction=.5*(.02*channels[list(PRICES)].mean()+channels[list(ACTIVITY)].mean())+.05*(time+validity)
    auxiliary=forecast*0
    if model.config.summary_weight>0:
        y=data['target'];v=data['target_valid'];past=structured(anchor(data['x'],data['valid'],check=False))
        values=[torch.diff(y[...,3],dim=1).square().mean(1).sqrt()/model.loss_scale[3],
            (y[...,1].amax(1)-y[...,2].amin(1))/model.loss_scale[3]]
        masks=[v[...,3].all(1),v[...,[1,2]].all(2).all(1)]
        for j in ACTIVITY:
            values.append((y[...,j].mean(1)-past[:,j])/model.loss_scale[j]);masks.append(v[...,j].all(1))
        target=torch.stack(values,-1);mask=torch.stack(masks,-1)
        error=F.smooth_l1_loss(output['future_summary'],target,reduction='none')
        auxiliary=(torch.where(mask,error,0).sum(0)/mask.sum(0).clamp_min(1)).mean()
    return forecast+reconstruction_weight*reconstruction+model.config.summary_weight*auxiliary,dict(forecast=forecast,reconstruction=reconstruction,future_summary=auxiliary)
