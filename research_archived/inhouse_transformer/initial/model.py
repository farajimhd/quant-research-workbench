from __future__ import annotations

import torch
from torch import nn

from research.inhouse_transformer.initial.config import ModelConfig


class FeatureTemporalTransformer(nn.Module):
    """Factorized feature/time transformer for multi-horizon OHLC return forecasts."""

    def __init__(
        self,
        *,
        feature_count: int,
        time_feature_count: int,
        context_length: int,
        horizon: int,
        target_count: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.feature_count = feature_count
        self.context_length = context_length
        self.horizon = horizon
        self.target_count = target_count
        self.config = config
        self.feature_attention_chunk_size = max(1, int(config.feature_attention_chunk_size))

        self.value_projection = nn.Linear(1, config.d_model)
        self.feature_embedding = nn.Embedding(feature_count, config.d_model)
        self.position_embedding = nn.Embedding(context_length, config.d_model)
        self.time_projection = nn.Sequential(
            nn.Linear(time_feature_count, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, config.d_model),
        )
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
        self.temporal_encoder = nn.TransformerEncoder(temporal_layer, num_layers=config.temporal_layers)
        self.temporal_norm = nn.LayerNorm(config.d_model)

        self.regression_head = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, horizon * target_count),
        )
        self.direction_head = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim // 2),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim // 2, horizon),
        )

    def forward(self, values: torch.Tensor, time_features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
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
        time_embed = self.time_projection(time_features).unsqueeze(2)

        tokens = token_values + feature_embed + position_embed + time_embed
        flat_tokens = tokens.reshape(batch_size * seq_len, feature_count, -1)
        encoded_features = self.encode_features(flat_tokens)
        feature_weights = torch.softmax(self.feature_pool(encoded_features), dim=1)
        bar_tokens = (encoded_features * feature_weights).sum(dim=1).reshape(batch_size, seq_len, -1)

        temporal_tokens = self.temporal_encoder(bar_tokens)
        last_token = self.temporal_norm(temporal_tokens[:, -1, :])
        prediction = self.regression_head(last_token).reshape(batch_size, self.horizon, self.target_count)
        direction_logits = self.direction_head(last_token)
        return prediction, direction_logits

    def encode_features(self, flat_tokens: torch.Tensor) -> torch.Tensor:
        if flat_tokens.shape[0] <= self.feature_attention_chunk_size:
            return self.feature_encoder(flat_tokens)
        encoded_chunks = [
            self.feature_encoder(chunk)
            for chunk in flat_tokens.split(self.feature_attention_chunk_size, dim=0)
        ]
        return torch.cat(encoded_chunks, dim=0)


def forecast_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    direction_logits: torch.Tensor,
    direction_target: torch.Tensor,
    direction_loss_weight: float,
) -> tuple[torch.Tensor, dict[str, float]]:
    regression = nn.functional.smooth_l1_loss(prediction, target)
    if direction_loss_weight > 0.0:
        direction = nn.functional.binary_cross_entropy_with_logits(direction_logits, direction_target)
        total = regression + direction_loss_weight * direction
    else:
        direction = regression.new_zeros(())
        total = regression
    return total, {
        "loss": float(total.detach().cpu()),
        "regression_loss": float(regression.detach().cpu()),
        "direction_loss": float(direction.detach().cpu()),
    }
