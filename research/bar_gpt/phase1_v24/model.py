"""Expand the range-leading V23 parent; preserve its decoder and exported slots."""
import torch
from ..phase1_v23.model import expand_temporal,restore as restore_parent

SPECS=[dict(width=2688,depth=8),dict(width=768,depth=10),dict(width=1152,depth=9),dict(width=1536,depth=10),dict(width=384,depth=6)]


def build(parent,width,depth):
    model=restore_parent(parent)
    model.temporal=expand_temporal(model.temporal,width,depth)
    return model


def restore(saved):
    model=build(saved['parent_template'],**saved['architecture'])
    model.load_state_dict(saved['model']);return model


def optimizer(model,multiplier=1.):
    inherited=[];readout=[]
    for name,p in model.temporal.named_parameters():
        (inherited if name.startswith(('stem.','blocks.')) or name in ('position','cls') else readout).append(p)
    factor=384/model.temporal.config.width
    return torch.optim.AdamW([dict(params=model.bar.parameters(),lr=3e-7*multiplier),
        dict(params=inherited,lr=2e-6*factor*multiplier),dict(params=readout,lr=1e-5*factor*multiplier),
        dict(params=model.decoder.parameters(),lr=1e-5*multiplier)],weight_decay=0.,fused=next(model.parameters()).is_cuda)
