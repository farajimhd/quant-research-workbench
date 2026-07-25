from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v16.model import NewsReactionModelV16, ResidualMLP
from research.news_reaction_model.v17.config import ModelConfig
from research.news_reaction_model.v17.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    PERSISTENCE_NAMES,
)


@dataclass(slots=True)
class NewsResponseOutput:
    direction_logits: torch.Tensor
    path_logits: torch.Tensor
    flow_logits: torch.Tensor
    persistence_logits: torch.Tensor
    article_embedding: torch.Tensor


class NewsResponseModelV17(nn.Module):
    """V16's exact encoder with response-archetype heads only."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.encoder = NewsReactionModelV16(config)
        # V17 must not compute, optimize, checkpoint, or allocate V16's old
        # opportunity heads. The shared encoder method ends before these layers.
        del self.encoder.horizon_embedding
        del self.encoder.input_fusion
        del self.encoder.blocks
        del self.encoder.output_norm
        del self.encoder.opportunity_heads
        d = int(config.d_model)
        self.window_embedding = nn.Embedding(
            len(config.response_windows), config.response_window_dim
        )
        self.window_fusion = nn.Sequential(
            nn.LayerNorm(d + config.response_window_dim),
            nn.Linear(d + config.response_window_dim, d),
            nn.GELU(),
        )
        self.response_blocks = nn.ModuleList(
            ResidualMLP(d, config.hidden_dim, config.dropout)
            for _ in range(config.layers)
        )
        self.response_norm = nn.LayerNorm(d)
        self.direction_head = nn.Linear(d, len(DIRECTION_NAMES))
        self.path_head = nn.Linear(d, len(PATH_NAMES))
        self.flow_head = nn.Linear(d, len(FLOW_NAMES))
        self.persistence_head = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(d, len(PERSISTENCE_NAMES)),
        )

    def forward(self, x: dict[str, Any]) -> NewsResponseOutput:
        article = self.encoder.encode_article(x)
        window_ids = torch.arange(
            len(self.config.response_windows), device=article.device
        )
        windows = self.window_embedding(window_ids).unsqueeze(0).expand(
            article.shape[0], -1, -1
        )
        hidden = self.window_fusion(
            torch.cat(
                (
                    article.unsqueeze(1).expand(-1, windows.shape[1], -1),
                    windows,
                ),
                dim=-1,
            )
        )
        for block in self.response_blocks:
            hidden = block(hidden)
        hidden = self.response_norm(hidden)
        persistence_features = torch.cat(
            (article, hidden.mean(dim=1)),
            dim=-1,
        )
        return NewsResponseOutput(
            direction_logits=self.direction_head(hidden),
            path_logits=self.path_head(hidden),
            flow_logits=self.flow_head(hidden),
            persistence_logits=self.persistence_head(persistence_features),
            article_embedding=article,
        )


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  v16["Completed V16 arrays, opened read-only"] --> encoder["V16 shared encoder"]',
            '  encoder --> article["Causal article and market-state representation"]',
            '  windows["Five learned response-window tokens"] --> fusion["Shared response encoder"]',
            "  article --> fusion",
            '  fusion --> direction["Direction: neutral / upside / downside / two-sided"]',
            '  fusion --> path["Path: no move / sustained / spike-fade / flush-recovery / reversal / mixed"]',
            '  fusion --> flow["Flow: balanced / demand / supply"]',
            '  fusion --> persistence["Cross-window persistence head"]',
        ]
    )
