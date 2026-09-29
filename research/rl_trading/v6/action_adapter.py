"""Translate a causal V6 policy proposal into one bracket OMS intent.

The caller supplies an explicit, versioned tick size for each held listing.
No historical tick-size authority is inferred from bar price precision.
Translation never changes cash, holdings, or the policy's action memory;
those change only after the quote-bound OMS reports a later outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import torch
from torch.nn import functional as F


@dataclass(frozen=True)
class ProposedOrder:
    action: str  # hold, enter_long, exit_long, set_stop, set_target.
    listing_index: int | None
    cash_fraction: float | None = None
    price: float | None = None


def decode_proposal(logits: torch.Tensor, sizes: torch.Tensor,
                    stop_distances: torch.Tensor,
                    target_distances: torch.Tensor, *,
                    held_index: torch.Tensor,
                    entry_prices: tuple[float, ...],
                    held_tick_sizes: tuple[float, ...],
                    ) -> ProposedOrder:
    """Decode [1+N+3H] masked logits without accessing future execution.

    Stop and target heads predict positive log distances from the confirmed
    entry price. Both sell levels round down to the supplied order increment.
    Invalid order geometry fails closed instead of clipping a prediction.
    """
    listings = sizes.numel()
    holdings = len(entry_prices)
    if (logits.ndim != 1 or logits.numel() != 1+listings+3*holdings or
            stop_distances.shape != (holdings,) or
            target_distances.shape != (holdings,) or
            held_index.shape != (holdings,) or
            held_index.dtype != torch.long or
            len(held_tick_sizes) != holdings or
            not torch.isfinite(logits.max())):
        raise ValueError('Malformed V6 action proposal axes')
    token = int(logits.argmax().item())
    if token == 0:
        return ProposedOrder('hold', None)
    if token <= listings:
        fraction = float(sizes[token-1].item())
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError('Invalid predicted buy cash fraction')
        return ProposedOrder('enter_long', token-1,
                             cash_fraction=fraction)
    base = 1+listings
    action, slot = divmod(token-base, holdings)
    if not 0 <= action < 3:
        raise ValueError('Invalid held action token')
    listing = int(held_index[slot].item())
    if action == 0:
        return ProposedOrder('exit_long', listing)
    entry, tick = entry_prices[slot], held_tick_sizes[slot]
    if (not math.isfinite(entry) or entry <= 0 or
            not math.isfinite(tick) or tick <= 0):
        raise ValueError('Held bracket lacks positive entry and tick authority')
    raw_distance = (stop_distances[slot] if action == 1 else
                    target_distances[slot])
    if not torch.isfinite(raw_distance):
        raise ValueError('Nonfinite bracket distance')
    distance = float(F.softplus(raw_distance).item())
    try:
        raw_price = entry*math.exp(-distance if action == 1 else distance)
    except OverflowError as error:
        raise ValueError('Nonfinite proposed bracket price') from error
    if not math.isfinite(raw_price):
        raise ValueError('Nonfinite proposed bracket price')
    price = math.floor(raw_price/tick + 1e-9)*tick
    if price <= 0 or (action == 1 and price >= entry) or (
            action == 2 and price <= entry):
        raise ValueError('Proposed bracket is invalid after tick rounding')
    return ProposedOrder('set_stop' if action == 1 else 'set_target',
                         listing, price=price)
