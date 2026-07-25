from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v15.config import ModelConfig
from research.news_reaction_model.v15.opportunity import OPPORTUNITY_CLASSES


@dataclass(slots=True)
class NewsReactionOpportunityOutput:
    logits: dict[str, torch.Tensor]
    article_embedding: torch.Tensor
    profile: dict[str, float]

    def probabilities(self) -> dict[str, torch.Tensor]:
        return {horizon: torch.softmax(logits.float(), dim=-1) for horizon, logits in self.logits.items()}

    def classes(self) -> dict[str, torch.Tensor]:
        return {horizon: logits.argmax(dim=-1) for horizon, logits in self.logits.items()}


class ResidualMLP(nn.Module):
    def __init__(self, width: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.net = nn.Sequential(
            nn.Linear(width, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, width),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(self.norm(x))


class NewsReactionModelV15(nn.Module):
    """V12 opportunity model augmented with strictly prior-news context."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.openai_text_projection = nn.Sequential(
            nn.LayerNorm(config.openai_embedding_dim),
            nn.Linear(config.openai_embedding_dim, d),
            nn.GELU(),
        )
        self.stock_state_projection = nn.Sequential(
            nn.LayerNorm(config.stock_state_dim),
            nn.Linear(config.stock_state_dim, d),
            nn.GELU(),
        )
        self.time_projection = nn.Sequential(
            nn.Linear(config.time_feature_dim, d),
            nn.GELU(),
        )
        self.chunk_projection = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU())
        self.chunk_position = nn.Embedding(3, d)
        self.chunk_gate = nn.Linear(d, 1)
        self.prior_context_projection = nn.Sequential(
            nn.LayerNorm(config.context_feature_dim),
            nn.Linear(config.context_feature_dim, d),
            nn.GELU(),
        )
        self.prior_item_fusion = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
        )
        self.prior_position = nn.Embedding(config.context_size, d)
        self.context_attention = nn.MultiheadAttention(
            d,
            config.attention_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.context_update = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(d, d),
            nn.Dropout(config.dropout),
        )
        self.horizon_embedding = nn.Embedding(len(config.horizons), config.horizon_dim)
        joint = d + config.horizon_dim
        self.input_fusion = nn.Sequential(nn.LayerNorm(joint), nn.Linear(joint, d), nn.GELU())
        self.blocks = nn.ModuleList(
            ResidualMLP(d, config.hidden_dim, config.dropout) for _ in range(config.layers)
        )
        self.output_norm = nn.LayerNorm(d)
        self.opportunity_heads = nn.ModuleDict(
            {horizon: nn.Linear(d, OPPORTUNITY_CLASSES) for horizon in config.horizons}
        )

    def forward(self, x: dict[str, Any]) -> NewsReactionOpportunityOutput:
        openai_text = self.openai_text_projection(x["openai_embedding"])
        stock_state = self.stock_state_projection(x["stock_state"])
        time_features = self.time_projection(x["time_features"])
        mask = x["channel_mask"].bool()
        if (~mask.any(dim=1)).any():
            mask = mask.clone()
            mask[~mask.any(dim=1), 0] = True
        hidden = self.chunk_projection(
            torch.stack((openai_text, stock_state, time_features), dim=1)
        )
        positions = torch.arange(hidden.shape[1], device=hidden.device)
        hidden = hidden + self.chunk_position(positions).unsqueeze(0)
        scores = self.chunk_gate(hidden).squeeze(-1).masked_fill(~mask, float("-inf"))
        article = torch.sum(hidden * torch.softmax(scores, dim=1).unsqueeze(-1), dim=1)
        prior_embeddings = x["prior_openai_embeddings"]
        prior_features = x["prior_context_features"]
        prior_mask = x["prior_context_mask"].bool()
        if prior_embeddings.ndim != 3 or prior_embeddings.shape[1] != self.config.context_size:
            raise ValueError(
                "prior_openai_embeddings must have shape "
                f"[B, {self.config.context_size}, {self.config.openai_embedding_dim}]."
            )
        if prior_features.shape[:2] != prior_embeddings.shape[:2]:
            raise ValueError("Prior embedding and context-feature axes do not match.")
        prior_text = self.openai_text_projection(prior_embeddings)
        prior_metadata = self.prior_context_projection(prior_features)
        prior_items = self.prior_item_fusion(
            torch.cat((prior_text, prior_metadata), dim=-1)
        )
        positions = torch.arange(self.config.context_size, device=article.device)
        prior_items = prior_items + self.prior_position(positions).unsqueeze(0)
        has_context = prior_mask.any(dim=1)
        safe_mask = prior_mask.clone()
        safe_mask[~has_context, 0] = True
        attended, _ = self.context_attention(
            article.unsqueeze(1),
            prior_items,
            prior_items,
            key_padding_mask=~safe_mask,
            need_weights=False,
        )
        contextual_article = article + self.context_update(
            torch.cat((article, attended.squeeze(1)), dim=-1)
        )
        article = torch.where(has_context.unsqueeze(-1), contextual_article, article)
        horizon_ids = torch.arange(len(self.config.horizons), device=hidden.device)
        horizon_embedding = self.horizon_embedding(horizon_ids).unsqueeze(0).expand(
            article.shape[0], -1, -1
        )
        fused = self.input_fusion(
            torch.cat(
                (
                    article.unsqueeze(1).expand(-1, len(self.config.horizons), -1),
                    horizon_embedding,
                ),
                dim=-1,
            )
        )
        for block in self.blocks:
            fused = block(fused)
        fused = self.output_norm(fused)
        logits = {
            horizon: self.opportunity_heads[horizon](fused[:, index])
            for index, horizon in enumerate(self.config.horizons)
        }
        return NewsReactionOpportunityOutput(logits=logits, article_embedding=article, profile={})

    def forward_with_timings(
        self,
        x: dict[str, Any],
        *,
        sync_cuda: bool = False,
    ) -> NewsReactionOpportunityOutput:
        started = time.perf_counter()
        output = self.forward(x)
        if sync_cuda and output.article_embedding.is_cuda:
            torch.cuda.synchronize()
        output.profile["model_forward_seconds"] = time.perf_counter() - started
        return output


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  text["Current OpenAI text embedding"] --> textproj["Shared text projection"]',
            '  state["V8 point-in-time stock state"] --> stateproj["V8 state projection"]',
            '  time["Causal exchange session and time features"] --> timeproj["Time projection"]',
            '  textproj --> pooling["Gated three-channel pooling"]',
            "  stateproj --> pooling",
            "  timeproj --> pooling",
            '  prior["Up to four strictly prior OpenAI embeddings"] --> priorproj["Shared text projection"]',
            '  reaction["Causally completed prior reactions + masks + time distance"] --> priorfeatures["Context projection"]',
            "  priorproj --> contextitems[\"Ordered prior-news tokens\"]",
            "  priorfeatures --> contextitems",
            "  contextitems --> attention[\"Six-head current-to-prior attention\"]",
            "  pooling --> attention",
            '  horizons["V8 horizon embedding"] --> encoder["V8 residual horizon encoder"]',
            "  attention --> encoder",
            '  encoder --> opportunity["One three-class opportunity head per horizon"]',
            '  opportunity --> classes["none | upside | downside"]',
        ]
    )
