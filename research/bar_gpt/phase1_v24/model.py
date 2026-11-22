"""Expand the range-leading V23 parent; preserve its decoder and exported slots."""
import torch
from ..phase1_v23.model import expand_temporal,restore as restore_parent

SPECS=[dict(width=2688,depth=8),dict(width=768,depth=10),dict(width=1152,depth=9),dict(width=1536,depth=10),dict(width=384,depth=6)]
EXTREME_SPEC=dict(width=4608,depth=11,diversify=True)


@torch.no_grad()
def break_replica_symmetry(encoder,parent_width,parent_layers):
    """Zero-sum outgoing-weight perturbations preserve replicated activations.

    Perturb every inherited attention/FFN contraction, not only the final
    readout. The latter alone left inherited Q gradients identical in the
    observed native runs. No input, objective or decoder change.
    """
    factor=encoder.config.width//parent_width
    if factor<=1:return
    for block in list(encoder.blocks)[:parent_layers]:
        for layer in (block.projection,block.ff[2]):
            w=layer.weight
            noise=torch.randn(w.shape[0],factor,w.shape[1]//factor,device=w.device,dtype=w.dtype)
            noise*=w.square().mean().sqrt()*.1
            noise-=noise.mean(1,keepdim=True)
            w.add_(noise.reshape_as(w))


def build(parent,width,depth,diversify=False):
    model=restore_parent(parent)
    old_width=model.temporal.config.width;old_layers=model.temporal.config.layers
    model.temporal=expand_temporal(model.temporal,width,depth)
    if diversify:break_replica_symmetry(model.temporal,old_width,old_layers)
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
