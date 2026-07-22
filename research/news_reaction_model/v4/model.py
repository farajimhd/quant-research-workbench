from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v4.config import ModelConfig
from research.news_reaction_model.v4.ranges import RANGE_SPECS, TARGET_NAMES


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


class NewsReactionModelV4(nn.Module):
    """Frozen-news-embedding encoder with horizon-specific percentage-range heads."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.chunk_projection = nn.Sequential(
            nn.LayerNorm(config.embedding_dim), nn.Linear(config.embedding_dim, d), nn.GELU(),
        )
        self.chunk_position = nn.Embedding(config.max_chunks, d)
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
        chunks = x["embeddings"]
        mask = x["chunk_mask"].bool()
        hidden = self.chunk_projection(chunks)
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
        '  chunks["Qwen chunks [B,2,1024]"] --> projection["LayerNorm + projection"]',
        '  projection --> pooling["Masked gated chunk pooling"]',
        '  horizons["Horizon embedding"] --> fusion["Publication-time fusion"]',
        '  pooling --> fusion',
        '  fusion --> mlp["Residual MLP encoder"]',
        '  mlp --> ending["Per-horizon ending range"]',
        '  mlp --> high["Per-horizon high range"]',
        '  mlp --> low["Per-horizon low range"]',
    ])
