"""Replicate trained heads and add identity blocks, preserving FP32 function."""
import copy
from dataclasses import replace
import torch
from torch import nn
from ..phase1_v15.model import CountAttention
from ..phase1_v22.model import restore as restore_v22,PositionEncoder,optimizer as base_optimizer


def expand_temporal(old,width,depth):
    d=old.config.width;layers=old.config.layers
    if width%d or width<d or depth<layers:raise ValueError('Only integer width expansion and added depth')
    if width==d and depth==layers:return copy.deepcopy(old)
    factor=width//d
    # The constructor consumes only config and cloned common encoder modules.
    shell=copy.deepcopy(old);shell.config=replace(old.config,width=width,heads=old.config.heads*factor,layers=layers)
    shell.stem=nn.Linear(52,width);shell.position=nn.Parameter(torch.empty(1,64,width));shell.cls=nn.Parameter(torch.empty(1,1,width))
    attention=lambda:CountAttention(width,shell.config.heads,stable=old.blocks[0].stable,balanced=old.blocks[0].balanced)
    shell.blocks=nn.Sequential(*[attention() for _ in range(layers)],nn.LayerNorm(width))
    new=PositionEncoder(shell,32);state=new.state_dict()
    for key,value in old.state_dict().items():
        if key in ('mean','scale'):result=value
        elif key in ('position','cls','queries'):result=value.repeat(1,1,factor)
        elif key=='stem.weight':result=value.repeat(factor,1)
        elif key=='readout.weight':result=value.repeat(1,factor)/factor
        elif key=='readout.bias':result=value
        elif key.endswith('qkv.weight'):result=value.reshape(3,d,d).repeat(1,factor,factor).reshape(3*width,width)/factor
        elif key.endswith('qkv.bias'):result=value.reshape(3,d).repeat(1,factor).flatten()
        elif key=='kv.weight':result=value.reshape(2,d,d).repeat(1,factor,factor).reshape(2*width,width)/factor
        elif key=='kv.bias':result=value.reshape(2,d).repeat(1,factor).flatten()
        elif value.ndim==2:result=value.repeat(factor,factor)/factor
        else:result=value.repeat(factor)
        if result.shape!=state[key].shape:raise ValueError(key)
        state[key]=result
    new.load_state_dict(state)
    if factor>1:
        # Zero-sum perturbation leaves identical replicated head outputs unchanged
        # initially, while allowing the replicated heads to learn differently.
        with torch.no_grad():
            noise=torch.randn(32,factor,d)*.0001;noise-=noise.mean(1,keepdim=True)
            new.readout.weight.add_(noise.flatten(1))
    blocks=list(new.blocks.children())[:-1]
    for _ in range(depth-layers):
        block=attention()
        nn.init.zeros_(block.projection.weight);nn.init.zeros_(block.projection.bias)
        nn.init.zeros_(block.ff[2].weight);nn.init.zeros_(block.ff[2].bias)
        blocks.append(block)
    new.blocks=nn.Sequential(*blocks,list(new.blocks.children())[-1])
    new.config=replace(new.config,layers=depth)
    return new


def build(parent,width=128,depth=2):
    m=restore_v22(parent)
    m.temporal=expand_temporal(m.temporal,width,depth)
    return m


def restore(saved):
    m=build(saved['parent_template'],**saved['architecture']);m.load_state_dict(saved['model']);return m


def optimizer(model,lr=3e-4,width_scaled=False):
    if not width_scaled:return base_optimizer(model,lr)
    factor=model.temporal.config.width/128
    inherited=[];fresh=[]
    for name,p in model.temporal.named_parameters():
        (inherited if name.startswith(('stem.','blocks.')) or name in ('position','cls') else fresh).append(p)
    return torch.optim.AdamW([dict(params=model.bar.parameters(),lr=3e-7),
        dict(params=inherited,lr=1e-5/factor),dict(params=fresh,lr=lr/factor),
        dict(params=model.decoder.parameters(),lr=lr)],weight_decay=0.,fused=next(model.parameters()).is_cuda)
