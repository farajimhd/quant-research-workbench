from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v20.config import ModelConfig
from research.news_reaction_model.v20.targets import (
    DOWN_BUCKET_INDICES,
    FLAT_BUCKET_INDEX,
    RETURN_BUCKET_COUNT,
    TrainingStatistics,
    UP_BUCKET_INDICES,
)


class GatedProjection(nn.Module):
    def __init__(self, input_dim: int, width: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(input_dim)
        self.value = nn.Linear(input_dim, width)
        self.gate = nn.Linear(input_dim, width)
        self.output = nn.Sequential(nn.GELU(), nn.Dropout(dropout), nn.LayerNorm(width))

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        normalized = self.norm(value)
        projected = self.value(normalized) * torch.sigmoid(self.gate(normalized))
        return self.output(projected)


class CrossAttentionBlock(nn.Module):
    def __init__(self, width: int, heads: int, feedforward: int, dropout: float) -> None:
        super().__init__()
        self.query_norm = nn.LayerNorm(width)
        self.context_norm = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(
            width, heads, dropout=dropout, batch_first=True
        )
        self.attention_dropout = nn.Dropout(dropout)
        self.feedforward_norm = nn.LayerNorm(width)
        self.feedforward = nn.Sequential(
            nn.Linear(width, feedforward),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(feedforward, width),
            nn.Dropout(dropout),
        )

    def forward(
        self,
        query: torch.Tensor,
        context: torch.Tensor,
        context_padding: torch.Tensor,
    ) -> torch.Tensor:
        attended, _ = self.attention(
            self.query_norm(query),
            self.context_norm(context),
            self.context_norm(context),
            key_padding_mask=context_padding,
            need_weights=False,
        )
        query = query + self.attention_dropout(attended)
        return query + self.feedforward(self.feedforward_norm(query))


class Expert(nn.Module):
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

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return value + self.net(self.norm(value))


class SparseTopKExperts(nn.Module):
    """Sample-routed top-k experts with bounded compute and auditable routing."""

    def __init__(
        self,
        width: int,
        expert_count: int,
        top_k: int,
        hidden: int,
        dropout: float,
    ) -> None:
        super().__init__()
        if not 1 <= top_k <= expert_count:
            raise ValueError("expert_top_k must be between one and expert_count.")
        self.expert_count = expert_count
        self.top_k = top_k
        self.router = nn.Linear(width, expert_count)
        self.experts = nn.ModuleList(
            Expert(width, hidden, dropout) for _ in range(expert_count)
        )
        self.output_norm = nn.LayerNorm(width)

    def forward(self, value: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        router_probabilities = torch.softmax(self.router(value).float(), dim=-1)
        top_values, top_indices = torch.topk(
            router_probabilities, self.top_k, dim=-1
        )
        top_values = top_values / top_values.sum(dim=-1, keepdim=True).clamp_min(1e-12)
        mixed = torch.zeros_like(value)
        for expert_index, expert in enumerate(self.experts):
            locations = torch.nonzero(top_indices == expert_index, as_tuple=False)
            if not locations.numel():
                continue
            rows = locations[:, 0]
            slots = locations[:, 1]
            transformed = expert(value.index_select(0, rows))
            weights = top_values[rows, slots].to(transformed.dtype).unsqueeze(-1)
            mixed.index_add_(0, rows, transformed * weights)
        return self.output_norm(mixed), router_probabilities


@dataclass(slots=True)
class ReturnDistributionOutput:
    return_logits: torch.Tensor
    return_probabilities: torch.Tensor
    direction_probabilities: torch.Tensor
    expected_return: torch.Tensor
    expected_up_return: torch.Tensor
    expected_down_return: torch.Tensor
    router_probabilities: torch.Tensor
    article_embedding: torch.Tensor


class NewsReactionModelV20(nn.Module):
    TOKEN_QUERY = 0
    TOKEN_TEXT = 1
    TOKEN_STOCK = 2
    TOKEN_TIME = 3
    TOKEN_EPISODE = 4
    TOKEN_REGIME = 5

    def __init__(
        self,
        config: ModelConfig,
        training_statistics: TrainingStatistics,
    ) -> None:
        super().__init__()
        self.config = config
        self.training_statistics = training_statistics
        d = config.d_model
        self.query_token = nn.Parameter(torch.empty(1, 1, d))
        nn.init.normal_(self.query_token, std=0.02)
        self.text_projection = GatedProjection(
            config.openai_embedding_dim, d, config.dropout
        )
        self.stock_projection = GatedProjection(
            config.stock_state_dim, d, config.dropout
        )
        self.time_projection = GatedProjection(
            config.time_feature_dim, d, config.dropout
        )
        self.episode_projection = GatedProjection(
            config.current_episode_feature_dim, d, config.dropout
        )
        self.regime_projection = GatedProjection(2, d, config.dropout)
        self.context_projection = GatedProjection(
            config.context_feature_dim, d, config.dropout
        )
        self.prior_fusion = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.price_embedding = nn.Embedding(config.price_regimes, d)
        self.session_embedding = nn.Embedding(config.publication_sessions, d)
        self.current_token_type = nn.Embedding(6, d)
        self.context_position = nn.Embedding(config.context_size, d)

        def encoder(layers: int) -> nn.TransformerEncoder:
            layer = nn.TransformerEncoderLayer(
                d_model=d,
                nhead=config.attention_heads,
                dim_feedforward=config.feedforward_dim,
                dropout=config.dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            return nn.TransformerEncoder(
                layer,
                num_layers=layers,
                norm=nn.LayerNorm(d),
                enable_nested_tensor=False,
            )

        self.current_encoder = encoder(config.current_layers)
        self.prior_encoder = encoder(config.prior_layers)
        self.cross_attention = nn.ModuleList(
            CrossAttentionBlock(
                d,
                config.attention_heads,
                config.feedforward_dim,
                config.dropout,
            )
            for _ in range(config.cross_attention_layers)
        )
        self.post_cross_norm = nn.LayerNorm(d)
        self.experts = SparseTopKExperts(
            d,
            config.expert_count,
            config.expert_top_k,
            config.expert_hidden_dim,
            config.dropout,
        )
        self.return_head = nn.Sequential(
            nn.LayerNorm(d),
            nn.Linear(d, config.expert_hidden_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.expert_hidden_dim, RETURN_BUCKET_COUNT),
        )
        self.register_buffer(
            "bucket_centers",
            torch.tensor(training_statistics.bucket_centers, dtype=torch.float32),
            persistent=True,
        )
        self.register_buffer(
            "active_bucket_mask",
            torch.tensor(
                [count > 0 for count in training_statistics.bucket_counts],
                dtype=torch.bool,
            ),
            persistent=True,
        )

    def _current_tokens(self, x: dict[str, Any]) -> tuple[torch.Tensor, torch.Tensor]:
        batch = x["openai_embedding"].shape[0]
        device = x["openai_embedding"].device
        regime = self.regime_projection(
            torch.cat((x["anchor_log"], x["context_fraction"]), dim=-1)
        )
        regime = (
            regime
            + self.price_embedding(x["price_regime"].long())
            + self.session_embedding(x["publication_session"].long())
        )
        tokens = torch.stack(
            (
                self.query_token.expand(batch, -1, -1)[:, 0],
                self.text_projection(x["openai_embedding"]),
                self.stock_projection(x["stock_state"]),
                self.time_projection(x["time_features"]),
                self.episode_projection(x["current_episode_features"]),
                regime,
            ),
            dim=1,
        )
        token_types = self.current_token_type(
            torch.arange(6, device=device, dtype=torch.long)
        ).unsqueeze(0)
        padding = torch.zeros((batch, 6), dtype=torch.bool, device=device)
        padding[:, 1] = ~x["channel_mask"][:, 0].bool()
        padding[:, 2] = ~x["channel_mask"][:, 1].bool()
        return tokens + token_types, padding

    def _prior_tokens(
        self, x: dict[str, Any]
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        device = x["openai_embedding"].device
        mask = x["prior_context_mask"].bool()
        prior = self.prior_fusion(
            torch.cat(
                (
                    self.text_projection(x["prior_openai_embeddings"]),
                    self.context_projection(x["prior_context_features"]),
                ),
                dim=-1,
            )
        )
        positions = torch.arange(self.config.context_size, device=device)
        prior = prior + self.context_position(positions).unsqueeze(0)
        padding = ~mask
        empty = ~mask.any(dim=1)
        safe_padding = padding.clone()
        if bool(empty.any()):
            safe_padding[empty, 0] = False
            prior = prior.clone()
            prior[empty, 0] = 0
        encoded = self.prior_encoder(prior, src_key_padding_mask=safe_padding)
        return encoded, safe_padding, empty

    def encode_article(
        self, x: dict[str, Any]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        current, current_padding = self._current_tokens(x)
        current = self.current_encoder(
            current, src_key_padding_mask=current_padding
        )
        prior, prior_padding, empty = self._prior_tokens(x)
        query = current[:, :1]
        for block in self.cross_attention:
            query = block(query, prior, prior_padding)
        fused = self.post_cross_norm(current[:, 0] + query[:, 0])
        if bool(empty.any()):
            # Empty episode context must not acquire signal from the safe dummy.
            fused = torch.where(empty.unsqueeze(-1), current[:, 0], fused)
        return self.experts(fused)

    def forward(self, x: dict[str, Any]) -> ReturnDistributionOutput:
        article, router_probabilities = self.encode_article(x)
        logits = self.return_head(article).masked_fill(
            ~self.active_bucket_mask.unsqueeze(0), -1.0e4
        )
        probabilities = torch.softmax(logits.float(), dim=-1)
        down = probabilities[:, DOWN_BUCKET_INDICES].sum(dim=-1)
        flat = probabilities[:, FLAT_BUCKET_INDEX]
        up = probabilities[:, UP_BUCKET_INDICES].sum(dim=-1)
        direction = torch.stack((flat, up, down), dim=-1)
        centers = self.bucket_centers.to(probabilities.dtype)
        expected = probabilities @ centers
        expected_up = (
            probabilities[:, UP_BUCKET_INDICES]
            @ centers[list(UP_BUCKET_INDICES)]
        ) / up.clamp_min(1e-12)
        expected_down = (
            probabilities[:, DOWN_BUCKET_INDICES]
            @ centers[list(DOWN_BUCKET_INDICES)]
        ) / down.clamp_min(1e-12)
        return ReturnDistributionOutput(
            return_logits=logits,
            return_probabilities=probabilities,
            direction_probabilities=direction,
            expected_return=expected,
            expected_up_return=expected_up,
            expected_down_return=expected_down,
            router_probabilities=router_probabilities,
            article_embedding=article,
        )


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  text["Current OpenAI embedding"] --> current["Gated current-feature encoder"]',
            '  stock["Point-in-time stock state"] --> current',
            '  time["Exchange-time state"] --> current',
            '  episode["Episode and regime state"] --> current',
            '  priorText["Prior-news embeddings"] --> prior["Causal prior-news transformer"]',
            '  priorState["Prior reactions and roles"] --> prior',
            '  current --> cross["Current-to-prior cross attention"]',
            '  prior --> cross',
            '  cross --> moe["Sparse top-2 regime experts"]',
            '  moe --> distribution["19-class signed opportunity distribution"]',
            '  distribution --> direction["P(up), P(down), P(neutral)"]',
            '  distribution --> returns["Expected and conditional return percentages"]',
        ]
    )
