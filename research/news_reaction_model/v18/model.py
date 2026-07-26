from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v18.config import ModelConfig
from research.news_reaction_model.v18.targets import DIRECTION_NAMES, FLOW_NAMES, PATH_NAMES


class ResidualMLP(nn.Module):
    def __init__(self, width: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.net = nn.Sequential(
            nn.Linear(width, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, width), nn.Dropout(dropout),
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return value + self.net(self.norm(value))


@dataclass(slots=True)
class EpisodeResponseOutput:
    direction_logits: torch.Tensor
    path_logits: torch.Tensor
    flow_logits: torch.Tensor
    regression: torch.Tensor
    article_embedding: torch.Tensor


class NewsReactionModelV18(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = config.d_model
        self.text_projection = nn.Sequential(
            nn.LayerNorm(config.openai_embedding_dim), nn.Linear(config.openai_embedding_dim, d), nn.GELU()
        )
        self.stock_projection = nn.Sequential(
            nn.LayerNorm(config.stock_state_dim), nn.Linear(config.stock_state_dim, d), nn.GELU()
        )
        self.time_projection = nn.Sequential(nn.Linear(config.time_feature_dim, d), nn.GELU())
        self.episode_projection = nn.Sequential(
            nn.LayerNorm(config.current_episode_feature_dim),
            nn.Linear(config.current_episode_feature_dim, d),
            nn.GELU(),
        )
        self.channel_position = nn.Embedding(4, d)
        self.channel_gate = nn.Linear(d, 1)
        self.context_projection = nn.Sequential(
            nn.LayerNorm(config.context_feature_dim), nn.Linear(config.context_feature_dim, d), nn.GELU()
        )
        self.context_fusion = nn.Sequential(nn.LayerNorm(2 * d), nn.Linear(2 * d, d), nn.GELU())
        self.context_position = nn.Embedding(config.context_size, d)
        self.context_attention = nn.MultiheadAttention(
            d, config.attention_heads, dropout=config.dropout, batch_first=True
        )
        self.context_update = nn.Sequential(
            nn.LayerNorm(2 * d), nn.Linear(2 * d, d), nn.GELU(),
            nn.Dropout(config.dropout), nn.Linear(d, d), nn.Dropout(config.dropout),
        )
        self.blocks = nn.ModuleList(
            ResidualMLP(d, config.hidden_dim, config.dropout) for _ in range(config.layers)
        )
        self.output_norm = nn.LayerNorm(d)
        self.direction_head = nn.Linear(d, len(DIRECTION_NAMES))
        self.path_head = nn.Linear(d, len(PATH_NAMES))
        self.flow_head = nn.Linear(d, len(FLOW_NAMES))
        self.regression_head = nn.Linear(d, 3)

    def encode_article(self, x: dict[str, Any]) -> torch.Tensor:
        channels = torch.stack(
            (
                self.text_projection(x["openai_embedding"]),
                self.stock_projection(x["stock_state"]),
                self.time_projection(x["time_features"]),
                self.episode_projection(x["current_episode_features"]),
            ),
            dim=1,
        )
        positions = torch.arange(4, device=channels.device)
        channels = channels + self.channel_position(positions).unsqueeze(0)
        scores = self.channel_gate(channels).squeeze(-1).masked_fill(
            ~x["channel_mask"].bool(), float("-inf")
        )
        article = torch.sum(
            channels * torch.softmax(scores.float(), dim=1).to(channels.dtype).unsqueeze(-1),
            dim=1,
        )
        context_mask = x["prior_context_mask"].bool()
        prior = self.context_fusion(
            torch.cat(
                (
                    self.text_projection(x["prior_openai_embeddings"]),
                    self.context_projection(x["prior_context_features"]),
                ),
                dim=-1,
            )
        )
        prior = prior + self.context_position(
            torch.arange(self.config.context_size, device=prior.device)
        ).unsqueeze(0)
        has_context = context_mask.any(dim=1)
        safe_mask = context_mask.clone()
        safe_mask[~has_context, 0] = True
        attended, _ = self.context_attention(
            article.unsqueeze(1), prior, prior, key_padding_mask=~safe_mask, need_weights=False
        )
        contextual = article + self.context_update(torch.cat((article, attended.squeeze(1)), dim=-1))
        return torch.where(has_context.unsqueeze(-1), contextual, article)

    def forward(self, x: dict[str, Any]) -> EpisodeResponseOutput:
        article = self.encode_article(x)
        hidden = article
        for block in self.blocks:
            hidden = block(hidden)
        hidden = self.output_norm(hidden)
        return EpisodeResponseOutput(
            self.direction_head(hidden),
            self.path_head(hidden),
            self.flow_head(hidden),
            self.regression_head(hidden),
            article,
        )


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  current["Current OpenAI embedding"] --> pool["Gated current-node pooling"]',
            '  stock["Point-in-time stock state"] --> pool',
            '  time["V15 causal exchange-time vector"] --> pool',
            '  role["Episode role, family, position, age"] --> pool',
            '  history["Up to eight prior episode nodes"] --> attention["Causal episode attention"]',
            '  evidence["Completed prior interval response only"] --> attention',
            "  pool --> attention",
            '  attention --> encoder["Residual response encoder"]',
            '  encoder --> outputs["Direction, path, flow, and actual return percent"]',
        ]
    )
