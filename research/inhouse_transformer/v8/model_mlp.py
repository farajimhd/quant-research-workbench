from __future__ import annotations

import torch
from torch import nn


class FlatMLPForecaster(nn.Module):
    """Simple flattened-window MLP baseline for OHLC return forecasts."""

    def __init__(
        self,
        *,
        context_length: int,
        feature_count: int,
        time_feature_count: int,
        horizon: int,
        target_count: int,
        hidden_dim: int,
        layers: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.context_length = context_length
        self.feature_count = feature_count
        self.time_feature_count = time_feature_count
        self.horizon = horizon
        self.target_count = target_count
        input_dim = context_length * (feature_count + time_feature_count)
        output_dim = horizon * target_count
        blocks: list[nn.Module] = []
        current_dim = input_dim
        for _ in range(max(1, layers)):
            blocks.extend(
                [
                    nn.Linear(current_dim, hidden_dim),
                    nn.LayerNorm(hidden_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                ]
            )
            current_dim = hidden_dim
        blocks.append(nn.Linear(current_dim, output_dim))
        self.network = nn.Sequential(*blocks)

    def forward(self, values: torch.Tensor, time_features: torch.Tensor) -> torch.Tensor:
        batch_size, seq_len, feature_count = values.shape
        if seq_len != self.context_length:
            raise ValueError(f"Expected context length {self.context_length}, got {seq_len}.")
        if feature_count != self.feature_count:
            raise ValueError(f"Expected {self.feature_count} input features, got {feature_count}.")
        tokens = torch.cat([values, time_features], dim=-1)
        prediction = self.network(tokens.reshape(batch_size, -1))
        return prediction.reshape(batch_size, self.horizon, self.target_count)
