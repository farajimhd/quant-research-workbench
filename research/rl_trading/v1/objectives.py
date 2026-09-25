"""Teacher-action and return supervision; no hindsight values enter observations."""
import torch
from torch.nn import functional as F


def teacher_loss(logits, value, batch, *, trade_weight: float = 4., value_weight: float = .1):
    if trade_weight < 1 or value_weight < 0:
        raise ValueError('Loss weights are invalid')
    target = batch['actions']
    mask = batch['action_mask']
    if logits.shape != mask.shape or target.shape != logits.shape[:2]:
        raise ValueError('Teacher action and feasible-mask shapes differ')
    if not torch.gather(mask,2,target.unsqueeze(-1)).all():
        raise ValueError('Teacher target is outside the causal action mask')
    protected = logits.float().masked_fill(~mask,-1e9)
    per_order = F.cross_entropy(protected.flatten(0,1),target.flatten(),reduction='none').reshape_as(target)
    weights = torch.ones_like(per_order).masked_fill(target != 0,trade_weight)
    action = (per_order*weights).sum()/weights.sum()
    returns = F.smooth_l1_loss(value.float(),batch['return_to_go'].float())
    total = action+value_weight*returns
    return total,dict(action_loss=action.detach(),value_loss=returns.detach(),
        action_accuracy=(protected.argmax(dim=-1) == target).float().mean().detach(),
        trade_recall=((protected.argmax(dim=-1) == target) & (target != 0)).sum().float().detach()
            /(target != 0).sum().clamp_min(1))
