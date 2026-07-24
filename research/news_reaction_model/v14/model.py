from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v14.config import ModelConfig
from research.news_reaction_model.v14.opportunity import OPPORTUNITY_CLASSES


WORD_TYPE = 0
CHAR_TYPE = 1
NUMERIC_TYPE = 2
NUMERIC_DENSE_TYPE = 3
STOCK_STATE_TYPE = 4
TIME_TYPE = 5
TOKEN_TYPES = 6


@dataclass(slots=True)
class NewsReactionOpportunityOutput:
    logits: dict[str, torch.Tensor]
    article_embedding: torch.Tensor
    profile: dict[str, float]

    def probabilities(self) -> dict[str, torch.Tensor]:
        return {
            horizon: torch.softmax(logits.float(), dim=-1)
            for horizon, logits in self.logits.items()
        }

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


class HorizonCrossAttentionBlock(nn.Module):
    """One transformer-style cross-attention block over an unordered token set."""

    def __init__(self, width: int, heads: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.query_norm = nn.LayerNorm(width)
        self.memory_norm = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(
            width,
            heads,
            dropout=dropout,
            batch_first=True,
        )
        self.attention_dropout = nn.Dropout(dropout)
        self.feed_forward = ResidualMLP(width, hidden, dropout)

    def forward(
        self,
        queries: torch.Tensor,
        memory: torch.Tensor,
        memory_mask: torch.Tensor,
    ) -> torch.Tensor:
        attended, _ = self.attention(
            self.query_norm(queries),
            self.memory_norm(memory),
            self.memory_norm(memory),
            key_padding_mask=~memory_mask,
            need_weights=False,
        )
        return self.feed_forward(queries + self.attention_dropout(attended))


class NewsReactionModelV14(nn.Module):
    """V10 opportunity model with token-level sparse TF-IDF cross-attention."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.word_embedding = nn.Embedding(
            config.word_vocab_size + 1, d, padding_idx=config.word_vocab_size
        )
        self.char_embedding = nn.Embedding(
            config.char_vocab_size + 1, d, padding_idx=config.char_vocab_size
        )
        self.numeric_embedding = nn.Embedding(
            config.numeric_vocab_size + 1, d, padding_idx=config.numeric_vocab_size
        )
        self.weight_projection = nn.Sequential(nn.Linear(1, d), nn.Tanh())
        self.token_type = nn.Embedding(TOKEN_TYPES, d)
        self.numeric_dense_projection = nn.Sequential(
            nn.LayerNorm(config.numeric_dense_dim),
            nn.Linear(config.numeric_dense_dim, d),
            nn.GELU(),
        )
        self.stock_state_projection = nn.Sequential(
            nn.LayerNorm(config.stock_state_dim),
            nn.Linear(config.stock_state_dim, d),
            nn.GELU(),
        )
        self.time_projection = nn.Sequential(
            nn.LayerNorm(config.time_feature_dim),
            nn.Linear(config.time_feature_dim, d),
            nn.GELU(),
        )
        self.horizon_queries = nn.Embedding(len(config.horizons), d)
        self.token_attention = HorizonCrossAttentionBlock(
            d, config.attention_heads, config.hidden_dim, config.dropout
        )
        # Preserve V10's horizon conditioning and residual prediction stack.
        self.horizon_embedding = nn.Embedding(len(config.horizons), config.horizon_dim)
        joint = d + config.horizon_dim
        self.input_fusion = nn.Sequential(
            nn.LayerNorm(joint), nn.Linear(joint, d), nn.GELU()
        )
        self.blocks = nn.ModuleList(
            ResidualMLP(d, config.hidden_dim, config.dropout)
            for _ in range(config.layers)
        )
        self.output_norm = nn.LayerNorm(d)
        self.opportunity_heads = nn.ModuleDict(
            {horizon: nn.Linear(d, OPPORTUNITY_CLASSES) for horizon in config.horizons}
        )

    def _sparse_tokens(
        self,
        ids: torch.Tensor,
        weights: torch.Tensor,
        embedding: nn.Embedding,
        token_type: int,
    ) -> torch.Tensor:
        signed_log_weight = torch.sign(weights) * torch.log1p(torch.abs(weights))
        type_ids = torch.full_like(ids, token_type)
        return (
            embedding(ids)
            + self.weight_projection(signed_log_weight.unsqueeze(-1))
            + self.token_type(type_ids)
        )

    def _dense_token(
        self,
        values: torch.Tensor,
        projection: nn.Module,
        token_type: int,
    ) -> torch.Tensor:
        type_ids = torch.full(
            (values.shape[0],),
            token_type,
            dtype=torch.long,
            device=values.device,
        )
        return projection(values) + self.token_type(type_ids)

    def forward(self, x: dict[str, Any]) -> NewsReactionOpportunityOutput:
        word = self._sparse_tokens(
            x["word_ids"], x["word_weights"], self.word_embedding, WORD_TYPE
        )
        char = self._sparse_tokens(
            x["char_ids"], x["char_weights"], self.char_embedding, CHAR_TYPE
        )
        numeric = self._sparse_tokens(
            x["numeric_ids"],
            x["numeric_weights"],
            self.numeric_embedding,
            NUMERIC_TYPE,
        )
        numeric_dense = self._dense_token(
            x["numeric_dense"], self.numeric_dense_projection, NUMERIC_DENSE_TYPE
        )
        stock_state = self._dense_token(
            x["stock_state"], self.stock_state_projection, STOCK_STATE_TYPE
        )
        time_features = self._dense_token(
            x["time_features"], self.time_projection, TIME_TYPE
        )
        memory = torch.cat(
            (
                word,
                char,
                numeric,
                numeric_dense.unsqueeze(1),
                stock_state.unsqueeze(1),
                time_features.unsqueeze(1),
            ),
            dim=1,
        )
        batch_size = memory.shape[0]
        dense_mask = torch.ones((batch_size, 3), dtype=torch.bool, device=memory.device)
        memory_mask = torch.cat(
            (
                x["word_mask"].bool(),
                x["char_mask"].bool(),
                x["numeric_mask"].bool(),
                dense_mask,
            ),
            dim=1,
        )
        horizon_ids = torch.arange(len(self.config.horizons), device=memory.device)
        queries = self.horizon_queries(horizon_ids).unsqueeze(0).expand(
            batch_size, -1, -1
        )
        attended = self.token_attention(queries, memory, memory_mask)
        horizon_context = self.horizon_embedding(horizon_ids).unsqueeze(0).expand(
            batch_size, -1, -1
        )
        fused = self.input_fusion(torch.cat((attended, horizon_context), dim=-1))
        for block in self.blocks:
            fused = block(fused)
        fused = self.output_norm(fused)
        logits = {
            horizon: self.opportunity_heads[horizon](fused[:, index])
            for index, horizon in enumerate(self.config.horizons)
        }
        return NewsReactionOpportunityOutput(
            logits=logits,
            article_embedding=attended.mean(dim=1),
            profile={},
        )

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
            '  word["Top-K word n-gram IDs + TF-IDF weights"] --> tokens["Learned feature + weight + type tokens"]',
            '  char["Top-K character n-gram IDs + TF-IDF weights"] --> tokens',
            '  numeric["Top-K financial-number context IDs + weights"] --> tokens',
            '  dense["Numeric summary + causal stock state + publication time"] --> tokens',
            '  horizons["Ten learned horizon queries"] --> attention["Six-head cross-attention over unordered tokens"]',
            "  tokens --> attention",
            '  attention --> encoder["V10 horizon conditioning + residual encoder"]',
            '  encoder --> opportunity["One three-class opportunity head per horizon"]',
            '  opportunity --> classes["none | upside | downside"]',
        ]
    )
