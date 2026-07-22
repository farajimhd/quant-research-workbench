from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v5.config import ModelConfig
from research.news_reaction_model.v5.ranges import RANGE_SPECS, TARGET_NAMES


@dataclass(slots=True)
class NewsReactionRangeOutput:
    logits: dict[str, dict[str, torch.Tensor]]
    article_embedding: torch.Tensor
    profile: dict[str, float]


class ResidualMLP(nn.Module):
    def __init__(self, width: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.net = nn.Sequential(
            nn.Linear(width, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, width), nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(self.norm(x))


class NewsReactionModelV5(nn.Module):
    """V4 prediction stack driven by supervised sparse lexical input adapters."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.word_embedding = nn.EmbeddingBag(
            config.word_vocab_size, d, mode="sum", include_last_offset=True,
        )
        self.char_embedding = nn.EmbeddingBag(
            config.char_vocab_size, d, mode="sum", include_last_offset=True,
        )
        self.chunk_projection = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU())
        self.chunk_position = nn.Embedding(2, d)
        self.chunk_gate = nn.Linear(d, 1)
        self.horizon_embedding = nn.Embedding(len(config.horizons), config.horizon_dim)
        joint = d + config.horizon_dim
        self.input_fusion = nn.Sequential(nn.LayerNorm(joint), nn.Linear(joint, d), nn.GELU())
        self.blocks = nn.ModuleList(ResidualMLP(d, config.hidden_dim, config.dropout) for _ in range(config.layers))
        self.output_norm = nn.LayerNorm(d)
        self.range_heads = nn.ModuleDict({
            horizon: nn.ModuleDict({
                target: nn.Linear(d, RANGE_SPECS[horizon].classes) for target in TARGET_NAMES
            })
            for horizon in config.horizons
        })

    def forward(self, x: dict[str, Any]) -> NewsReactionRangeOutput:
        word = self.word_embedding(
            x["word_indices"], x["word_offsets"], per_sample_weights=x["word_weights"]
        )
        char = self.char_embedding(
            x["char_indices"], x["char_offsets"], per_sample_weights=x["char_weights"]
        )
        mask = x["channel_mask"].bool()
        if (~mask.any(dim=1)).any():
            mask = mask.clone()
            mask[~mask.any(dim=1), 0] = True
        hidden = self.chunk_projection(torch.stack((word, char), dim=1))
        positions = torch.arange(hidden.shape[1], device=hidden.device)
        hidden = hidden + self.chunk_position(positions).unsqueeze(0)
        scores = self.chunk_gate(hidden).squeeze(-1).masked_fill(~mask, float("-inf"))
        article = torch.sum(hidden * torch.softmax(scores, dim=1).unsqueeze(-1), dim=1)
        horizon_ids = torch.arange(len(self.config.horizons), device=hidden.device)
        horizon_embedding = self.horizon_embedding(horizon_ids).unsqueeze(0).expand(article.shape[0], -1, -1)
        fused = self.input_fusion(torch.cat((article.unsqueeze(1).expand(-1, len(self.config.horizons), -1), horizon_embedding), dim=-1))
        for block in self.blocks:
            fused = block(fused)
        fused = self.output_norm(fused)
        logits = {
            horizon: {target: self.range_heads[horizon][target](fused[:, index]) for target in TARGET_NAMES}
            for index, horizon in enumerate(self.config.horizons)
        }
        return NewsReactionRangeOutput(logits=logits, article_embedding=article, profile={})

    def forward_with_timings(self, x: dict[str, Any], *, sync_cuda: bool = False) -> NewsReactionRangeOutput:
        started = time.perf_counter()
        output = self.forward(x)
        if sync_cuda and output.article_embedding.is_cuda:
            torch.cuda.synchronize()
        output.profile["model_forward_seconds"] = time.perf_counter() - started
        return output


def build_model_mermaid() -> str:
    return "\n".join([
        "flowchart LR",
        '  words["sparse word IDs + TF-IDF weights"] --> wordbag["weighted word EmbeddingBag"]',
        '  chars["sparse character IDs + TF-IDF weights"] --> charbag["weighted character EmbeddingBag"]',
        '  wordbag --> projection["V4-like channel projection"]',
        '  charbag --> projection',
        '  projection --> pooling["V4 gated channel pooling"]',
        '  horizons["Horizon embedding"] --> fusion["Publication-time fusion"]',
        '  pooling --> fusion',
        '  fusion --> mlp["Residual MLP encoder"]',
        '  mlp --> ending["Per-horizon ending range"]',
        '  mlp --> high["Per-horizon high range"]',
        '  mlp --> low["Per-horizon low range"]',
    ])
