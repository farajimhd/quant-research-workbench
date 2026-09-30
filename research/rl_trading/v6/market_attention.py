"""Identity-preserving causal 15-clock-second ranking and bounded attention.

Uses existing V6 log_volume; no new shard columns or market reads. Ranking
does not reset candle/action histories and never depends on teacher labels.
"""
from dataclasses import dataclass
import numpy as np
import torch
from torch import nn
from research.rl_trading.v6.features import SCALAR_NAMES


@dataclass(frozen=True)
class MarketAttentionConfig:
    top_r: int = 100
    sort_secs: int = 1
    market_tokens: int = 8
    heads: int = 4

    def __post_init__(self):
        if min(self.top_r, self.sort_secs, self.market_tokens, self.heads) < 1:
            raise ValueError('Ranking and attention limits must be positive')


class VolumeRanker:
    """Bounded [15,N] ring, stable ties by certified listing-axis index."""
    def __init__(self, listings: int, config: MarketAttentionConfig):
        if listings < 1:
            raise ValueError('Empty market')
        self.config = config
        self.ring = np.zeros((15, listings), dtype=np.float64)
        self.clocks = np.full(15, -1, dtype=np.int64)
        self.seen = np.zeros(listings, dtype=bool)
        self.last_us = -1
        self.last_selection_us = -1
        self.refresh_second = None
        self.ranked = np.empty(0, dtype=np.int64)

    def observe(self, close_us: int, indices, scalar):
        indices = np.asarray(indices, dtype=np.int64)
        scalar = np.asarray(scalar)
        second = close_us // 1_000_000
        if (close_us % 1_000_000 or close_us <= self.last_us or
                close_us < self.last_selection_us or
                indices.ndim != 1 or len(np.unique(indices)) != len(indices) or
                scalar.shape != (len(indices), len(SCALAR_NAMES)) or
                np.any(indices < 0) or np.any(indices >= len(self.seen))):
            raise ValueError('Invalid completed V6 candle event')
        volume = np.expm1(scalar[:, SCALAR_NAMES.index('log_volume')].astype(np.float64))
        if not np.isfinite(volume).all() or np.any(volume < 0):
            raise ValueError('Invalid V6 candle volume')
        slot = second % 15
        if self.clocks[slot] != second:
            self.ring[slot].fill(0)
            self.clocks[slot] = second
        self.ring[slot, indices] = volume
        self.seen[indices] = True
        self.last_us = close_us

    def select(self, close_us: int, *, held=(), pending=()):
        if close_us < max(self.last_us, self.last_selection_us) or close_us % 1_000_000:
            raise ValueError('Ranking clock precedes observed evidence')
        second = close_us // 1_000_000
        self.last_selection_us = close_us
        if self.refresh_second is None or second-self.refresh_second >= self.config.sort_secs:
            live = (self.clocks > second-15) & (self.clocks <= second)
            total = self.ring[live].sum(0)
            candidates = np.flatnonzero(self.seen)
            order = np.lexsort((candidates, -total[candidates]))
            self.ranked = candidates[order[:self.config.top_r]]
            self.refresh_second = second
        mandatory = np.asarray(tuple(held)+tuple(pending), dtype=np.int64)
        if np.any(mandatory < 0) or np.any(mandatory >= len(self.seen)):
            raise ValueError('Held/pending identity outside certified market')
        return np.union1d(self.ranked, mandatory)


class RankedMarketAttention(nn.Module):
    """Latest query attends to its 120 completed candles, then market latents.

    [R,120,D] temporal keys; [L,D] market latents attend to [R,D], followed
    by listing-to-latent attention. Cost O(R*120*D + R*L*D), no N*N matrix.
    Future masking is enforced by the completed-history input contract.
    """
    def __init__(self, width: int, config: MarketAttentionConfig):
        super().__init__()
        if width % config.heads:
            raise ValueError('Attention heads must divide model width')
        self.position = nn.Parameter(torch.randn(120, width)*.01)
        self.temporal = nn.MultiheadAttention(width, config.heads, dropout=0, batch_first=True)
        self.market = nn.MultiheadAttention(width, config.heads, dropout=0, batch_first=True)
        self.broadcast = nn.MultiheadAttention(width, config.heads, dropout=0, batch_first=True)
        self.latents = nn.Parameter(torch.randn(config.market_tokens, width)*.01)
        self.norm = nn.LayerNorm(width)
        self.summary = nn.Linear(width, width)

    def forward(self, history, seen, embeddings, selected, *, selected_history=None):
        rows = history[selected] if selected_history is None else selected_history
        # [R,120,D], ordered oldest to latest; sparse training graph only.
        counts = seen[selected].clamp(max=120)
        valid = torch.arange(120, device=rows.device)[None] >= (120-counts[:, None])
        if not len(selected) or (counts <= 0).any():
            raise ValueError('Selected attention listing lacks candle context')
        keys = rows + self.position[None]
        temporal, _ = self.temporal(keys[:, -1:], keys, keys,
            key_padding_mask=~valid, need_weights=False)
        listed = self.norm(embeddings[selected] + temporal[:, 0])
        # Lightweight entire observed market summary includes unselected listings.
        summary = embeddings[seen > 0].mean(0)
        queries = self.latents + self.summary(summary)[None]
        market, _ = self.market(queries[None], listed[None], listed[None], need_weights=False)
        cross, _ = self.broadcast(listed[None], market, market, need_weights=False)
        return self.norm(listed + cross[0])
