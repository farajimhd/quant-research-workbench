"""Bounded-gradient chronological state for packed actual-candle training.

Only listings with a new persisted candle update their 120-candle state.
Each optimization chunk retains a graph for changed listings; `detach`
commits their states without retaining the graph across the full session.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

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
    seen: torch.Tensor  # [N], actual observed count; not clock seconds.
    _updates: dict[int, tuple[torch.Tensor, int]] = field(default_factory=dict, repr=False)

    def history_for(self, indices: torch.Tensor) -> torch.Tensor:
        """Gather [K,120,D] only, including this BPTT chunk's sparse graph.

        Detached base storage is never copied into an [N,120,D] graph.
        Python handles identity pointers only; projection/attention remain
        batched tensor operations over the requested rows.
        """
        keys = indices.detach().cpu().tolist()
        if not any(key in self._updates for key in keys):
            return self.history.index_select(0, indices)
        # Group identity pointers by event tensor. One batched gather/scatter
        # per event replaces one slice and autograd node per listing.
        groups = {}
        for destination, key in enumerate(keys):
            if key in self._updates:
                block, source = self._updates[key]
                group = groups.setdefault(id(block), (block, [], []))
                group[1].append(destination)
                group[2].append(source)
        result = self.history.index_select(0, indices)
        for block, destinations, sources in groups.values():
            destination = torch.tensor(destinations, device=indices.device)
            source = torch.tensor(sources, device=indices.device)
            result = result.index_copy(0, destination, block.index_select(0, source))
        return result

    @classmethod
    def empty(cls, encoder: ActualCandleEncoder, listings: int, *,
              device: torch.device, dtype: torch.dtype) -> 'SparseCandleState':
        if listings < 1:
            raise ValueError('Session has no listings')
        return cls(torch.zeros(listings, encoder.history_candles, encoder.width,
                               device=device, dtype=dtype),
                   torch.zeros(listings, encoder.width, device=device,
                               dtype=dtype),
                   torch.zeros(listings, device=device, dtype=torch.long))

    def advance(self, encoder: ActualCandleEncoder,
                listing_index: torch.Tensor, scalar: torch.Tensor,
                levels: torch.Tensor) -> None:
        """Apply one close-clock event with K distinct observed listings.

        A single indexed tensor update retains the bounded chunk's graph.
        This avoids one Python operation and autograd node per changed
        listing. The full state is detached at the chunk boundary.
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
        previous = self.history_for(listing_index)
        projected = encoder.project(encoder._input(scalar, levels))
        updated = torch.cat((previous[:, 1:], projected[:, None]), dim=1)
        weighted = (updated * encoder.lag[:, 0, :].T[None]).sum(dim=1)
        encoded = encoder.norm(F.gelu(weighted))
        if torch.is_grad_enabled():
            for offset, key in enumerate(listing_index.detach().cpu().tolist()):
                self._updates[key] = (updated, offset)
        else:
            self.history[listing_index] = updated
        self.encoded = self.encoded.index_copy(0, listing_index, encoded)
        self.seen[listing_index] += 1

    def embeddings(self) -> torch.Tensor:
        """Return [N,D] newest causal embeddings at the current close."""
        return self.encoded

    def detach(self) -> None:
        """Commit changed listings and sever all prior-chunk gradients."""
        if self._updates:
            with torch.no_grad():
                keys = list(self._updates)
                indices = torch.tensor(keys, device=self.history.device, dtype=torch.long)
                self.history[indices] = self.history_for(indices).detach()
            self._updates.clear()
        self.history = self.history.detach()
        self.encoded = self.encoded.detach()


@torch.no_grad()
def seed_previous_session(state: SparseCandleState,
                          encoder: ActualCandleEncoder,
                          listings: tuple[str, ...],
                          previous: SessionBank | None, *,
                          batch_size: int = 256) -> None:
    """Load at most 120 actual prior candles per listing, once per session.

    The current candle replaces the oldest input at the first current-day
    observation. Missing prior listings remain zero-padded with no fabricated
    bars. This function only initializes history; it never supplies labels.
    """
    if (len(listings) != state.history.shape[0] or batch_size < 1 or
            state.history.grad_fn is not None or
            state.encoded.grad_fn is not None):
        raise ValueError('Invalid previous-session warm-up state')
    if previous is None:
        return
    length = encoder.history_candles
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
            item = previous.listing_tail(identity, length=length)
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
        state.seen[start:start+len(identities)] = torch.from_numpy(
            present.sum(1).astype(np.int64)).to(state.history.device)
        state.history[start:start+len(identities)] = projected
        weighted = (state.history[start:start+len(identities)] *
                    encoder.lag[:, 0, :].T[None]).sum(dim=1)
        state.encoded[start:start+len(identities)] = encoder.norm(
            F.gelu(weighted))
