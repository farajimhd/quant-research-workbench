"""Masked order and fractional-size imitation objective for V5 supervision."""
from __future__ import annotations

import torch
from torch.nn import functional as F


def teacher_loss(action_logits: torch.Tensor, buy_size_logits: torch.Tensor,
                 tokens: torch.Tensor, remaining_cash_weights: torch.Tensor,
                 order_valid: torch.Tensor, *, size_weight: float = 1.) -> tuple[torch.Tensor, dict]:
    """Score [B,O] teacher steps, including one STOP and excluding padding.

    Tokens use 0=STOP, 1..N=BUY listing, N+1..N+H=SELL holding.
    BUY targets are fractions of cash remaining after preceding orders.
    """
    batch, orders, actions = action_logits.shape
    candidates = buy_size_logits.shape[-1]
    if (buy_size_logits.shape[:2] != (batch, orders) or
            tokens.shape != (batch, orders) or
            remaining_cash_weights.shape != (batch, orders) or
            order_valid.shape != (batch, orders) or
            actions < candidates + 1):
        raise ValueError('Inconsistent V5 action or size shapes')
    active = order_valid.to(action_logits.dtype)
    per_order = F.cross_entropy(action_logits.transpose(1, 2),
                                tokens.clamp(0, actions - 1), reduction='none')
    action_loss = (per_order * active).sum() / active.sum()
    buy = order_valid & (tokens > 0) & (tokens <= candidates)
    selected = torch.gather(buy_size_logits, 2,
                            (tokens - 1).clamp(0, candidates - 1).unsqueeze(-1)).squeeze(-1)
    # The certified label adapter validates token and weight ranges before GPU
    # transfer. Tensor-only reductions avoid a device synchronization per step.
    buy_float = buy.to(selected.dtype)
    target = remaining_cash_weights.clamp(0, 1)
    size_loss = (F.binary_cross_entropy_with_logits(selected, target,
                reduction='none') * buy_float).sum() / buy_float.sum().clamp_min(1)
    size_mae = (((torch.sigmoid(selected) - target).abs() * buy_float).sum() /
                buy_float.sum().clamp_min(1))
    loss = action_loss + size_weight * size_loss
    metrics = dict(action_loss=action_loss.detach(), size_loss=size_loss.detach(),
                   buy_size_mae=size_mae.detach(),
                   order_accuracy=((action_logits.argmax(-1) == tokens) & order_valid).sum()
                   .float().div(active.sum()).detach(),
                   active_orders=order_valid.sum().detach(), buy_orders=buy.sum().detach())
    return loss, metrics
