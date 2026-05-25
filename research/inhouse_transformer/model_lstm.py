from __future__ import annotations

import torch
from torch import nn


class SimpleLSTMForecaster(nn.Module):
    """Small LSTM baseline for actual-value sequence forecasting."""

    def __init__(
        self,
        *,
        input_size: int,
        hidden_size: int,
        layers: int,
        dropout: float,
        horizon: int,
        target_count: int,
    ) -> None:
        super().__init__()
        self.horizon = horizon
        self.target_count = target_count
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=layers,
            dropout=dropout if layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Linear(hidden_size, horizon * target_count)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        _, (hidden, _) = self.lstm(inputs)
        prediction = self.head(hidden[-1])
        return prediction.reshape(inputs.shape[0], self.horizon, self.target_count)
