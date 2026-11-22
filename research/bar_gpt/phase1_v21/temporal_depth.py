"""Identity-initialized extra temporal attention block; decoder unchanged."""
import torch
from ..phase0_v2.model import AttentionBlock


def attach(model):
    if 'extra' in model.temporal.blocks._modules:raise ValueError('Already attached')
    c=model.temporal.config
    block=AttentionBlock(c.width,c.heads).to(next(model.parameters()).device)
    with torch.no_grad():
        block.projection.weight.zero_();block.projection.bias.zero_()
        block.ff[-1].weight.zero_();block.ff[-1].bias.zero_()
    model.temporal.blocks.add_module('extra',block)
