from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v16.config import ModelConfig
from research.news_reaction_model.v16.opportunity import OPPORTUNITY_CLASSES


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


class NewsReactionModelV16(nn.Module):
    """V15 plus strictly causal cross-market news and leader attention."""

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
        self.current_market_projection = nn.Sequential(
            nn.LayerNorm(config.current_market_feature_dim),
            nn.Linear(config.current_market_feature_dim, d),
            nn.GELU(),
        )
        self.chunk_projection = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU())
        self.chunk_position = nn.Embedding(4, d)
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
        self.market_news_projection = nn.Sequential(
            nn.LayerNorm(config.market_news_feature_dim),
            nn.Linear(config.market_news_feature_dim, d),
            nn.GELU(),
        )
        self.market_news_fusion = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
        )
        self.market_leader_projection = nn.Sequential(
            nn.LayerNorm(config.market_leader_feature_dim),
            nn.Linear(config.market_leader_feature_dim, d),
            nn.GELU(),
        )
        self.market_position = nn.Embedding(
            config.market_context_size + config.market_leader_size,
            d,
        )
        self.market_token_type = nn.Embedding(2, d)
        self.market_attention = nn.MultiheadAttention(
            d,
            config.attention_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.market_update = nn.Sequential(
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
        current_market = self.current_market_projection(x["current_market_features"])
        mask = x["channel_mask"].bool()
        if (~mask.any(dim=1)).any():
            mask = mask.clone()
            mask[~mask.any(dim=1), 0] = True
        hidden = self.chunk_projection(
            torch.stack((openai_text, stock_state, time_features, current_market), dim=1)
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
        market_embeddings = x["market_context_openai_embeddings"]
        market_features = x["market_context_features"]
        market_mask = x["market_context_mask"].bool()
        leader_features = x["market_leader_features"]
        leader_mask = x["market_leader_mask"].bool()
        expected_market = (
            article.shape[0],
            self.config.market_context_size,
            self.config.openai_embedding_dim,
        )
        if tuple(market_embeddings.shape) != expected_market:
            raise ValueError(
                f"market_context_openai_embeddings must have shape {expected_market}, "
                f"got {tuple(market_embeddings.shape)}."
            )
        if tuple(market_features.shape[:2]) != tuple(market_embeddings.shape[:2]):
            raise ValueError("Market embedding and feature axes do not match.")
        if tuple(leader_features.shape[:2]) != (
            article.shape[0],
            self.config.market_leader_size,
        ):
            raise ValueError("Market leader axes do not match the configured contract.")
        market_news_tokens = self.market_news_fusion(
            torch.cat(
                (
                    self.openai_text_projection(market_embeddings),
                    self.market_news_projection(market_features),
                ),
                dim=-1,
            )
        )
        leader_tokens = self.market_leader_projection(leader_features)
        market_tokens = torch.cat((market_news_tokens, leader_tokens), dim=1)
        token_mask = torch.cat((market_mask, leader_mask), dim=1)
        token_count = market_tokens.shape[1]
        positions = torch.arange(token_count, device=article.device)
        types = torch.cat(
            (
                torch.zeros(self.config.market_context_size, dtype=torch.long, device=article.device),
                torch.ones(self.config.market_leader_size, dtype=torch.long, device=article.device),
            )
        )
        market_tokens = (
            market_tokens
            + self.market_position(positions).unsqueeze(0)
            + self.market_token_type(types).unsqueeze(0)
        )
        has_market = token_mask.any(dim=1)
        safe_market_mask = token_mask.clone()
        safe_market_mask[~has_market, 0] = True
        market_attended, _ = self.market_attention(
            article.unsqueeze(1),
            market_tokens,
            market_tokens,
            key_padding_mask=~safe_market_mask,
            need_weights=False,
        )
        market_article = article + self.market_update(
            torch.cat((article, market_attended.squeeze(1)), dim=-1)
        )
        article = torch.where(has_market.unsqueeze(-1), market_article, article)
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
            '  currentmarket["Current ticker pre-news action + causal ranks"] --> marketproj["Market-state projection"]',
            '  textproj --> pooling["Gated four-channel pooling"]',
            "  stateproj --> pooling",
            "  timeproj --> pooling",
            "  marketproj --> pooling",
            '  prior["Up to four strictly prior OpenAI embeddings"] --> priorproj["Shared text projection"]',
            '  reaction["Causally completed prior reactions + masks + time distance"] --> priorfeatures["Context projection"]',
            "  priorproj --> contextitems[\"Ordered prior-news tokens\"]",
            "  priorfeatures --> contextitems",
            "  contextitems --> attention[\"Six-head current-to-prior attention\"]",
            "  pooling --> attention",
            '  horizons["V8 horizon embedding"] --> encoder["V8 residual horizon encoder"]',
            '  recentmarket["Latest 100 single-ticker news + observed reactions"] --> crossmarket["Cross-market attention"]',
            '  leaders["Up to 20 causal market leaders"] --> crossmarket',
            "  attention --> crossmarket",
            "  crossmarket --> encoder",
            '  encoder --> opportunity["One three-class opportunity head per horizon"]',
            '  opportunity --> classes["none | upside | downside"]',
        ]
    )
