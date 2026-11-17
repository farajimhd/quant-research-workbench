"""Train-only fixed transforms, learned bottleneck, single-affine decoder."""
from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from ..phase1_v7.model import geometry,quote_geometry,PRICES,ACTIVITY


@dataclass
class Config:
    architecture:str='mlp'
    width:int=1024
    layers:int=3
    embedding:int=512
    visible:int=16
    output:str='direct'
    precision:str='fp32'


def coordinates(logs,valid,kind):
    if kind=='direct':
        result=logs.clone()
        for base in (0,6,12):result[...,base+4:base+6]=logs[...,base+4:base+6].sqrt()
        return result,valid
    result=geometry(logs,valid);mask=valid.clone()
    for base in (0,6,12):mask[...,base:base+4]=valid[...,base:base+4].all(-1,keepdim=True)
    if kind=='joint':
        pair,pm=quote_geometry(logs,valid);result[...,12:16]=pair;mask[...,12:16]=pm
    return result,mask


def inverse(logs):
    raw=logs.double().clone()
    for base in (0,6,12):
        raw[...,base:base+4]=logs[...,base:base+4].double().exp()
        raw[...,base+4:base+6]=logs[...,base+4:base+6].double().expm1()
    return raw


class Residual(nn.Module):
    def __init__(self,width):
        super().__init__();self.block=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,width),nn.GELU(),nn.Linear(width,width))
    def forward(self,x):return x+.25*self.block(x)


class Encoder(nn.Module):
    def __init__(self,config,input_mean,input_scale):
        super().__init__();self.config=config
        self.register_buffer('input_mean',input_mean.float().clone());self.register_buffer('input_scale',input_scale.float().clone())
        if config.architecture=='mlp':
            self.backbone=nn.Sequential(nn.Linear(64*54,config.width),nn.GELU(),
                *[Residual(config.width) for _ in range(config.layers)],nn.LayerNorm(config.width))
        else:
            self.input=nn.Linear(54,config.width);self.position=nn.Parameter(torch.randn(64,config.width)*.02)
            self.cls=nn.Parameter(torch.randn(1,1,config.width)*.02)
            layer=nn.TransformerEncoderLayer(config.width,config.width//64,config.width*4,dropout=0.,activation='gelu',batch_first=True,norm_first=True)
            self.backbone=nn.TransformerEncoder(layer,config.layers,norm=nn.LayerNorm(config.width),enable_nested_tensor=False)
            # TransformerEncoder clones start identically otherwise.
            for block in self.backbone.layers:
                for module in block.modules():
                    if isinstance(module,nn.Linear):module.reset_parameters()
        self.export=nn.Linear(config.width,config.embedding)

    def encode(self,values,valid,positions):
        normalized=torch.where(valid,(values-self.input_mean)/self.input_scale,0)
        tokens=torch.cat((normalized,valid.float()),-1)
        with torch.autocast(values.device.type,dtype=torch.bfloat16,enabled=self.config.precision=='bf16'):
            if self.config.architecture=='mlp':
                grid=tokens.new_zeros(len(tokens),64,54)
                grid.scatter_(1,positions[...,None].expand(-1,-1,54),tokens)
                hidden=self.backbone(grid.flatten(1))
            else:
                tokens=self.input(tokens)+self.position[positions]
                hidden=self.backbone(torch.cat((self.cls.expand(len(tokens),-1,-1),tokens),1))[:,0]
        # EVERY exported coordinate is produced by learned weights. No copied slots.
        return self.export(hidden.float())

    def forward(self,values,valid,positions):return self.encode(values,valid,positions)


class Autoencoder(Encoder):
    def __init__(self,config,input_mean,input_scale,output_mean,output_scale):
        super().__init__(config,input_mean,input_scale)
        self.register_buffer('output_mean',output_mean.float().clone());self.register_buffer('output_scale',output_scale.float().clone())
        # 18 market coordinates, 9 time coordinates, 27 validity logits per bar.
        self.decoder=nn.Linear(config.embedding,64*54)
        nn.init.normal_(self.decoder.weight,std=.0005);nn.init.zeros_(self.decoder.bias)

    def decode(self,z):
        decoded=self.decoder(z.float()).reshape(-1,64,54)
        code=decoded[...,:18]*self.output_scale+self.output_mean
        columns=[None]*18
        for base in (0,6,12):
            if self.config.output=='direct':
                for j in range(base,base+4):columns[j]=code[...,j]
            else:
                o=code[...,base];c=o+code[...,base+3]
                columns[base]=o;columns[base+3]=c
                columns[base+1]=torch.maximum(o,c)+code[...,base+1].square()
                columns[base+2]=torch.minimum(o,c)-code[...,base+2].square()
            for j in (base+4,base+5):columns[j]=code[...,j].square()
        if self.config.output=='joint':
            ao=columns[6]+code[...,12].square();ac=columns[9]+code[...,15].square()
            ah=torch.maximum(torch.maximum(ao,ac),columns[7])+code[...,13].square()
            al=torch.minimum(torch.minimum(ao,ac),columns[8]+code[...,14].square())
            columns[12:16]=[ao,ah,al,ac]
        return torch.stack(columns,-1),decoded[...,18:27],decoded[...,27:]

    def forward(self,values,valid,positions):
        z=self.encode(values,valid,positions)
        return z,self.decode(z)


def objective(outputs,target,target_valid,selected,input_mean,input_scale):
    prediction,time,validity=outputs
    residual=prediction-target[...,:18]
    factor=residual.new_tensor([100 if j in PRICES else 1 for j in range(18)])
    losses=F.smooth_l1_loss(residual*factor,torch.zeros_like(residual),reduction='none',beta=1.)
    mask=target_valid[...,:18]&selected[...,None]
    channel=torch.where(mask,losses,0).sum((0,1))/mask.sum((0,1)).clamp_min(1)
    market=.5*(channel[list(PRICES)].mean()+channel[list(ACTIVITY)].mean())
    time_target=(target[...,18:]-input_mean[18:])/input_scale[18:]
    time_mask=target_valid[...,18:]&selected[...,None]
    tl=F.smooth_l1_loss(time,time_target,reduction='none')
    time_loss=torch.where(time_mask,tl,0).sum()/time_mask.sum().clamp_min(1)
    vl=F.binary_cross_entropy_with_logits(validity,target_valid.float(),reduction='none')
    validity_loss=torch.where(selected[...,None],vl,0).sum()/(selected.sum()*27).clamp_min(1)
    return market+.05*(time_loss+validity_loss),dict(market=market,price=channel[list(PRICES)].mean(),
        activity=channel[list(ACTIVITY)].mean(),time=time_loss,validity=validity_loss)
