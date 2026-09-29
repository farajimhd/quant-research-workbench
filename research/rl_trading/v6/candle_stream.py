"""Bounded-gradient chronological state for packed actual-candle training.

Only listings with a new persisted candle update their 120-candle state.
Each optimization chunk retains a graph for changed listings; `detach`
commits their states without retaining the graph across the full session.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch
from torch.nn import functional as F

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.features import LEVEL_NAMES, SCALAR_NAMES
from research.rl_trading.v6.model import ActualCandleEncoder


@dataclass
class SparseCandleState:
    history: torch.Tensor  # [N,120,D], detached at chunk boundaries.
    encoded: torch.Tensor  # [N,D], most recent completed actual candle.
    changed_history: dict[int, torch.Tensor] = field(default_factory=dict)
    changed_encoded: dict[int, torch.Tensor] = field(default_factory=dict)

    @classmethod
    def empty(cls, encoder: ActualCandleEncoder, listings: int, *,
              device: torch.device, dtype: torch.dtype) -> 'SparseCandleState':
        if listings < 1:
            raise ValueError('Session has no listings')
        return cls(torch.zeros(listings, encoder.history_candles, encoder.width,
                               device=device, dtype=dtype),
                   torch.zeros(listings, encoder.width, device=device,
                               dtype=dtype))

    def advance(self, encoder: ActualCandleEncoder,
                listing_index: torch.Tensor, scalar: torch.Tensor,
                levels: torch.Tensor) -> None:
        """Apply one close-clock event with K distinct observed listings.

        This uses O(K*120*D) temporary history rather than copying the
        O(N*120*D) market state at every second. The pending dictionaries
        are bounded by the number of listings changed in one BPTT chunk.
        """
        if (listing_index.ndim != 1 or listing_index.dtype != torch.long or
                listing_index.numel() != scalar.shape[0] or
                listing_index.unique().numel() != listing_index.numel() or
                (listing_index.numel() and
                 (listing_index.min() < 0 or
                  listing_index.max() >= self.history.shape[0]))):
            raise ValueError('Invalid sparse close event listing axis')
        if not listing_index.numel():
            return
        ids = listing_index.tolist()
        previous = torch.stack([self.changed_history.get(index,
                                 self.history[index]) for index in ids])
        projected = encoder.project(encoder._input(scalar, levels))
        updated = torch.cat((previous[:, 1:], projected[:, None]), dim=1)
        weighted = (updated * encoder.lag[:, 0, :].T[None]).sum(dim=1)
        encoded = encoder.norm(F.gelu(weighted))
        for offset, index in enumerate(ids):
            self.changed_history[index] = updated[offset]
            self.changed_encoded[index] = encoded[offset]

    def embeddings(self) -> torch.Tensor:
        """Return [N,D] newest causal embeddings at the current close."""
        if not self.changed_encoded:
            return self.encoded
        ids = list(self.changed_encoded)
        indexes = torch.tensor(ids, dtype=torch.long,
                               device=self.encoded.device)
        return self.encoded.index_copy(0, indexes,
            torch.stack([self.changed_encoded[index] for index in ids]))

    def detach(self) -> None:
        """Commit changed listings and sever all prior-chunk gradients."""
        if not self.changed_history:
            return
        ids = list(self.changed_history)
        indexes = torch.tensor(ids, dtype=torch.long,
                               device=self.encoded.device)
        with torch.no_grad():
            self.history.index_copy_(0, indexes,
                torch.stack([self.changed_history[index] for index in ids])
                    .detach())
            self.encoded.index_copy_(0, indexes,
                torch.stack([self.changed_encoded[index] for index in ids])
                    .detach())
        self.changed_history.clear()
        self.changed_encoded.clear()


@torch.no_grad()
def seed_previous_session(state: SparseCandleState,
                          encoder: ActualCandleEncoder,
                          listings: tuple[str, ...],
                          previous: SessionBank | None, *,
                          batch_size: int = 256) -> None:
    """Load at most 119 actual prior candles per listing, once per session.

    The current candle becomes the 120th input at the first current-day
    observation. Missing prior listings remain zero-padded with no fabricated
    bars. This function only initializes history; it never supplies labels.
    """
    if (len(listings) != state.history.shape[0] or batch_size < 1 or
            state.changed_history or state.changed_encoded):
        raise ValueError('Invalid previous-session warm-up state')
    if previous is None:
        return
    length = encoder.history_candles - 1
    for start in range(0, len(listings), batch_size):
        identities = listings[start:start+batch_size]
        scalar = np.zeros((len(identities), length, len(SCALAR_NAMES)),
                          dtype=np.float32)
        levels = np.zeros((len(identities), length, 2, 5,
                           len(LEVEL_NAMES)), dtype=np.float32)
        present = np.zeros((len(identities), length), dtype=np.float32)
        for offset, identity in enumerate(identities):
            if identity not in previous.manifest['offsets']:
                continue
            item = previous.listing(identity)
            count = min(length, len(item.close_us))
            if count:
                scalar[offset, -count:] = item.scalar[-count:]
                levels[offset, -count:] = item.levels[-count:]
                present[offset, -count:] = 1
        flat_scalar = torch.from_numpy(scalar.reshape(-1, len(SCALAR_NAMES)))
        flat_levels = torch.from_numpy(levels.reshape(-1, 2, 5,
                                                       len(LEVEL_NAMES)))
        projected = encoder.project(encoder._input(
            flat_scalar.to(state.history.device),
            flat_levels.to(state.history.device))).reshape(
                len(identities), length, encoder.width)
        projected *= torch.from_numpy(present).to(
            state.history.device)[..., None]
        state.history[start:start+len(identities), 1:] = projected
        weighted = (state.history[start:start+len(identities)] *
                    encoder.lag[:, 0, :].T[None]).sum(dim=1)
        state.encoded[start:start+len(identities)] = encoder.norm(
            F.gelu(weighted))
