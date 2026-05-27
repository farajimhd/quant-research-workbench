from __future__ import annotations

import torch
from torch import nn

from research.inhouse_transformer.v21.config import ModelConfig


class MicrostructureBranchEncoder(nn.Module):
    def __init__(
        self,
        *,
        feature_count: int,
        context_length: int,
        temporal_layers: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.feature_count = feature_count
        self.context_length = context_length
        self.value_projection = nn.Linear(1, config.d_model)
        self.feature_embedding = nn.Embedding(feature_count, config.d_model)
        self.position_embedding = nn.Embedding(context_length, config.d_model)

        feature_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.num_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.feature_encoder = nn.TransformerEncoder(feature_layer, num_layers=config.feature_attention_layers)
        self.feature_pool = nn.Linear(config.d_model, 1)

        temporal_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.num_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.temporal_encoder = nn.TransformerEncoder(temporal_layer, num_layers=temporal_layers)
        self.norm = nn.LayerNorm(config.d_model)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        batch_size, seq_len, feature_count = values.shape
        if seq_len != self.context_length:
            raise ValueError(f"Expected context length {self.context_length}, got {seq_len}.")
        if feature_count != self.feature_count:
            raise ValueError(f"Expected {self.feature_count} features, got {feature_count}.")

        token_values = self.value_projection(values.unsqueeze(-1))
        feature_ids = torch.arange(feature_count, device=values.device)
        position_ids = torch.arange(seq_len, device=values.device)
        feature_embed = self.feature_embedding(feature_ids).view(1, 1, feature_count, -1)
        position_embed = self.position_embedding(position_ids).view(1, seq_len, 1, -1)
        tokens = token_values + feature_embed + position_embed

        flat_tokens = tokens.reshape(batch_size * seq_len, feature_count, -1)
        encoded_features = self.feature_encoder(flat_tokens)
        feature_weights = torch.softmax(self.feature_pool(encoded_features), dim=1)
        time_tokens = (encoded_features * feature_weights).sum(dim=1).reshape(batch_size, seq_len, -1)

        encoded_time = self.temporal_encoder(time_tokens)
        return self.norm(encoded_time[:, -1, :])


class HybridMicrostructureTransformer(nn.Module):
    """Hybrid 1s/10s microstructure transformer with v14-style binary targets."""

    def __init__(
        self,
        *,
        one_second_feature_count: int,
        ten_second_feature_count: int,
        one_second_context: int,
        ten_second_context: int,
        horizon_steps: int,
        target_count: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.one_second_feature_count = one_second_feature_count
        self.ten_second_feature_count = ten_second_feature_count
        self.one_second_context = one_second_context
        self.ten_second_context = ten_second_context
        self.horizon = horizon_steps
        self.target_count = target_count
        self.target_bit_count = config.target_bit_count

        self.one_second_encoder = MicrostructureBranchEncoder(
            feature_count=one_second_feature_count,
            context_length=one_second_context,
            temporal_layers=config.one_second_layers,
            config=config,
        )
        self.ten_second_encoder = MicrostructureBranchEncoder(
            feature_count=ten_second_feature_count,
            context_length=ten_second_context,
            temporal_layers=config.ten_second_layers,
            config=config,
        )
        self.fusion = nn.Sequential(
            nn.Linear(config.d_model * 2, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, config.d_model),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.head = nn.Linear(config.d_model, horizon_steps * target_count * config.target_bit_count)

    def forward(self, one_second_values: torch.Tensor, ten_second_values: torch.Tensor) -> torch.Tensor:
        one_repr = self.one_second_encoder(one_second_values)
        ten_repr = self.ten_second_encoder(ten_second_values)
        fused = self.fusion(torch.cat([one_repr, ten_repr], dim=-1))
        return self.head(fused).reshape(
            one_second_values.shape[0],
            self.horizon,
            self.target_count,
            self.target_bit_count,
        )


def forecast_loss(prediction: torch.Tensor, target: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
    total = nn.functional.binary_cross_entropy_with_logits(prediction, target)
    bit_accuracy = ((prediction.detach().sigmoid() >= 0.5) == (target >= 0.5)).float().mean()
    return total, {
        "loss": float(total.detach().cpu()),
        "regression_loss": float(total.detach().cpu()),
        "bit_accuracy_pct": float(bit_accuracy.detach().cpu() * 100.0),
    }

