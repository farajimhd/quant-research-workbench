"""Conditional imitation loss for certified five-action bracket supervision.

Only the selected action contributes a size or price-distance regression.
Oracle stop/target extrema are labels; they are never policy observations.
"""
from __future__ import annotations

import math

import torch
from torch.nn import functional as F


def bracket_loss(logits: torch.Tensor, sizes: torch.Tensor,
                 stop_distances: torch.Tensor,
                 target_distances: torch.Tensor, *, token: int,
                 size_fraction: float | None = None,
                 oracle_log_distance: float | None = None,
                 ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """Score one ordered teacher action with shape [1+N+3H].

    Token 0 is HOLD; 1..N are ENTER_LONG; the next H tokens each are
    EXIT_LONG, SET_STOP, SET_TARGET. Stop distance is log(entry/stop), target
    distance is log(target/entry); both are positive and scale-free.
    """
    listings, holdings = sizes.numel(), stop_distances.numel()
    if (logits.ndim != 1 or target_distances.shape != (holdings,) or
            logits.shape != (1 + listings + 3 * holdings,) or
            not 0 <= token < logits.numel() or
            not torch.isfinite(logits[token]) or
            (token == 0 and (size_fraction is not None or
                             oracle_log_distance is not None))):
        raise ValueError('Invalid bracket action target or prediction shape')
    entered = 1 <= token <= listings
    stop_base = 1 + listings + holdings
    target_base = stop_base + holdings
    stop_set = stop_base <= token < target_base
    target_set = target_base <= token < target_base + holdings
    if (entered != (size_fraction is not None) or
            (stop_set or target_set) !=
            (oracle_log_distance is not None)):
        raise ValueError('Conditional bracket label is missing or misplaced')
    if (size_fraction is not None and
            (not math.isfinite(size_fraction) or not 0 <= size_fraction <= 1)):
        raise ValueError('Invalid realized cash fraction')
    if (oracle_log_distance is not None and
            (not math.isfinite(oracle_log_distance) or
             oracle_log_distance <= 0)):
        raise ValueError('Oracle bracket distance must be positive')
    action = F.cross_entropy(logits[None],
        torch.tensor([token], dtype=torch.long, device=logits.device))
    size = logits.new_zeros(())
    bracket = logits.new_zeros(())
    if entered:
        size = F.smooth_l1_loss(sizes[token - 1],
            sizes.new_tensor(size_fraction))
    elif stop_set or target_set:
        raw = (stop_distances[token - stop_base] if stop_set else
               target_distances[token - target_base])
        bracket = F.smooth_l1_loss(F.softplus(raw),
            raw.new_tensor(oracle_log_distance))
    return action + size + bracket, {
        'action_loss': action.detach(), 'size_loss': size.detach(),
        'bracket_loss': bracket.detach(),
        'action_correct': (logits.argmax() == token).to(logits.dtype).detach(),
    }
