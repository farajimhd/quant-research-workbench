from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from research.news_reaction_model.v20.model import (
    CrossAttentionBlock,
    GatedProjection,
    SparseTopKExperts,
)
from research.news_reaction_model.v21.config import ModelConfig
from research.news_reaction_model.v21.targets import (
    MAGNITUDE_BUCKET_COUNT,
    SIDE_COUNT,
    TrainingStatistics,
)


@dataclass(slots=True)
class HierarchicalReturnOutput:
    direction_logits: torch.Tensor
    direction_probabilities: torch.Tensor
    magnitude_logits: torch.Tensor
    magnitude_probabilities: torch.Tensor
    joint_return_probabilities: torch.Tensor
    expected_return: torch.Tensor
    expected_up_return: torch.Tensor
    expected_down_return: torch.Tensor
    router_probabilities: torch.Tensor
    article_embedding: torch.Tensor


class NewsReactionModelV21(nn.Module):
    """V20 encoder with a coherent direction × conditional-magnitude decoder."""

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
        self.direction_head = nn.Sequential(
            nn.LayerNorm(d),
            nn.Linear(d, config.expert_hidden_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.expert_hidden_dim, 3),
        )
        self.magnitude_head = nn.Sequential(
            nn.LayerNorm(d),
            nn.Linear(d, config.expert_hidden_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(
                config.expert_hidden_dim,
                SIDE_COUNT * MAGNITUDE_BUCKET_COUNT,
            ),
        )
        self.register_buffer(
            "magnitude_centers",
            torch.tensor(
                training_statistics.magnitude_centers, dtype=torch.float32
            ),
            persistent=True,
        )
        self.register_buffer(
            "active_magnitude_mask",
            torch.tensor(
                [
                    [count > 0 for count in side]
                    for side in training_statistics.magnitude_counts
                ],
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
            fused = torch.where(empty.unsqueeze(-1), current[:, 0], fused)
        return self.experts(fused)

    def forward(self, x: dict[str, Any]) -> HierarchicalReturnOutput:
        article, router_probabilities = self.encode_article(x)
        direction_logits = self.direction_head(article)
        direction_probabilities = torch.softmax(direction_logits.float(), dim=-1)
        magnitude_logits = self.magnitude_head(article).reshape(
            -1, SIDE_COUNT, MAGNITUDE_BUCKET_COUNT
        )
        magnitude_logits = magnitude_logits.masked_fill(
            ~self.active_magnitude_mask.unsqueeze(0), -1.0e4
        )
        magnitude_probabilities = torch.softmax(
            magnitude_logits.float(), dim=-1
        )
        centers = self.magnitude_centers.to(magnitude_probabilities.dtype)
        expected_magnitude = (magnitude_probabilities * centers.unsqueeze(0)).sum(
            dim=-1
        )
        expected_up = expected_magnitude[:, 0]
        expected_down = -expected_magnitude[:, 1]
        expected_return = (
            direction_probabilities[:, 1] * expected_up
            + direction_probabilities[:, 2] * expected_down
        )
        down_joint = (
            direction_probabilities[:, 2:3]
            * magnitude_probabilities[:, 1].flip(dims=(-1,))
        )
        neutral_joint = direction_probabilities[:, 0:1]
        up_joint = (
            direction_probabilities[:, 1:2]
            * magnitude_probabilities[:, 0]
        )
        joint = torch.cat((down_joint, neutral_joint, up_joint), dim=-1)
        return HierarchicalReturnOutput(
            direction_logits=direction_logits,
            direction_probabilities=direction_probabilities,
            magnitude_logits=magnitude_logits,
            magnitude_probabilities=magnitude_probabilities,
            joint_return_probabilities=joint,
            expected_return=expected_return,
            expected_up_return=expected_up,
            expected_down_return=expected_down,
            router_probabilities=router_probabilities,
            article_embedding=article,
        )


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  current["V20 current article and state tokens"] --> encoder["Current transformer"]',
            '  prior["Causal prior-news sequence"] --> priorEncoder["Prior transformer"]',
            '  encoder --> cross["Current-to-prior cross attention"]',
            '  priorEncoder --> cross',
            '  cross --> moe["Sparse top-2 regime experts"]',
            '  moe --> direction["P(neutral), P(upside), P(downside)"]',
            '  moe --> magnitude["P(magnitude bucket | upside/downside)"]',
            '  direction --> joint["Normalized signed-return distribution"]',
            '  magnitude --> joint',
            '  joint --> output["Direction confidence and expected return percent"]',
        ]
    )


__all__ = [
    "HierarchicalReturnOutput",
    "NewsReactionModelV21",
    "build_model_mermaid",
]
