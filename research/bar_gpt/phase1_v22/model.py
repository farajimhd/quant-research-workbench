"""Position-preserving encoder exports; only learned embeddings enter decoder."""
import copy
from dataclasses import replace
import torch
from torch import nn
from torch.nn import functional as F
from ..phase0_v2.model import FeatureAttention
from ..phase1_v20.encoders import restore as restore_parent


def widen_features(bar, factor=4):
    """Net2Wider replication: preserve each old attention head exactly in FP32."""
    old=bar.encoder; c=old.config
    new=FeatureAttention(replace(c,width=c.width*factor,heads=c.heads*factor))
    s=old.state_dict(); target=new.state_dict(); d=c.width
    for key,value in s.items():
        if key in ('value_weight','feature_identity'): result=value.repeat(1,factor)
        elif key=='metadata.weight': result=value.repeat(factor,1)
        elif key=='metadata.bias': result=value.repeat(factor)
        elif key=='readout.weight': result=value.reshape(c.embedding,18,d).repeat(1,1,factor).reshape(c.embedding,-1)/factor
        elif key=='readout.bias': result=value
        elif key.endswith('qkv.weight'): result=value.reshape(3,d,d).repeat(1,factor,factor).reshape(3*d*factor,d*factor)/factor
        elif key.endswith('qkv.bias'): result=value.reshape(3,d).repeat(1,factor).flatten()
        elif value.ndim==2: result=value.repeat(factor,factor)/factor
        else: result=value.repeat(factor)
        if result.shape!=target[key].shape: raise ValueError(key)
        target[key]=result
    new.load_state_dict(target)
    # Break replicated-head gradient symmetry without changing initial outputs:
    # identical replicated activations multiply perturbations summing to zero.
    with torch.no_grad():
        noise=torch.randn(c.embedding,18,factor,d)*.001
        noise=noise-noise.mean(2,keepdim=True)
        new.readout.weight.add_(noise.reshape_as(new.readout.weight))
    bar.encoder=new;bar.config=new.config


class PositionEncoder(nn.Module):
    def __init__(self,parent,n):
        super().__init__();self.config=replace(parent.config,embedding=n)
        self.register_buffer('mean',parent.mean.clone());self.register_buffer('scale',parent.scale.clone())
        self.stem=copy.deepcopy(parent.stem);self.position=nn.Parameter(parent.position.detach().clone())
        self.cls=nn.Parameter(parent.cls.detach().clone());self.blocks=copy.deepcopy(parent.blocks)
        d=self.config.width
        self.queries=nn.Parameter(parent.position.detach().clone())
        self.query_norm=nn.LayerNorm(d);self.context_norm=nn.LayerNorm(d)
        self.query=nn.Linear(d,d);self.kv=nn.Linear(d,2*d);self.output=nn.Linear(d,d)
        self.output_norm=nn.LayerNorm(d);self.readout=nn.Linear(d,n)

    def forward(self,latents,metadata,pos):
        x=torch.cat(((latents-self.mean)/self.scale,metadata),-1);b=len(x);d=self.config.width;heads=self.config.heads
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(b,-1,-1),h),1))[:,1:]
            # Position-aligned visible encoded states are an ENCODER residual.
            # Unknown positions contain only visible-context mean, never targets.
            aligned=h.mean(1,keepdim=True).expand(-1,64,-1).scatter(1,pos[...,None].expand_as(h),h)
            queries=self.queries.expand(b,-1,-1)
            q=self.query(self.query_norm(queries)).reshape(b,64,heads,d//heads).transpose(1,2)
            k,v=self.kv(self.context_norm(h)).reshape(b,-1,2,heads,d//heads).permute(2,0,3,1,4).unbind(0)
            attended=F.scaled_dot_product_attention(q,k,v).transpose(1,2).reshape(b,64,d)
            state=self.output_norm(aligned+queries+self.output(attended))
        return self.readout(state.float())


def build(saved,kind='global512',wide=False):
    m=restore_parent(saved)
    if wide:widen_features(m.bar)
    if kind.startswith('slots'):
        n=int(kind[5:]);m.temporal=PositionEncoder(m.temporal,n);m.decoder=nn.Linear(n,18)
    else:
        n=int(kind[6:]);m.temporal.config=replace(m.temporal.config,embedding=n)
        m.temporal.readout=nn.Linear(64*m.temporal.config.width,n)
        m.temporal.mask_adapter=nn.Linear(n,n)
        nn.init.zeros_(m.temporal.mask_adapter.weight);nn.init.zeros_(m.temporal.mask_adapter.bias)
        m.decoder=nn.Linear(n,64*18)
    # Every arm starts at the training-derived coordinate mean, with nonzero
    # small decoder weights so encoder gradients are available immediately.
    nn.init.normal_(m.decoder.weight,std=.001);nn.init.zeros_(m.decoder.bias)
    m.repair_arm='joint_repaired';m.activity_change_prefloor=True;m.requires_grad_(True)
    return m


def restore(saved):
    m=build(saved['parent_template'],**saved['architecture']);m.load_state_dict(saved['model']);return m


def optimizer(model,lr=3e-4):
    inherited=[];fresh=[]
    for name,p in model.temporal.named_parameters():
        (inherited if name.startswith(('stem.','blocks.')) or name in ('position','cls') else fresh).append(p)
    return torch.optim.AdamW([dict(params=model.bar.parameters(),lr=3e-7),
        dict(params=inherited,lr=1e-5),dict(params=fresh+list(model.decoder.parameters()),lr=lr)],
        weight_decay=0.,fused=next(model.parameters()).is_cuda)
