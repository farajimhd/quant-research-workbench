from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from research.temporal_event_model.v2.config import ModelConfig


@dataclass(slots=True)
class TemporalReturnOutput:
    # Shape: [B, H]. Predicted future returns in normalized units.
    return_prediction_norm: torch.Tensor


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


class MarketTemporalReturnPredictor(nn.Module):
    """Predict future single-ticker returns from a sequence of market-structure embeddings.

    The market encoder is intentionally outside this module. That keeps the
    temporal predictor reusable with cached embeddings in production, while the
    trainer can still optionally fine-tune the encoder by including it in the
    optimizer.
    """

    def __init__(self, *, context_chunks: int, horizons: tuple[int, ...], config: ModelConfig) -> None:
        super().__init__()
        self.context_chunks = int(context_chunks)
        self.horizons = tuple(int(value) for value in horizons)
        self.config = config

        self.context_embedding_projection = nn.Sequential(
            nn.Linear(config.embedding_dim, config.temporal_d_model),
            nn.GELU(),
            nn.LayerNorm(config.temporal_d_model),
        )
        self.context_position_embedding = nn.Embedding(self.context_chunks, config.temporal_d_model)

        layer = nn.TransformerEncoderLayer(
            d_model=config.temporal_d_model,
            nhead=config.temporal_heads,
            dim_feedforward=config.temporal_ff_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.temporal_context_encoder = transformer_encoder(layer, num_layers=config.temporal_layers)
        self.temporal_context_norm = nn.LayerNorm(config.temporal_d_model)

        self.global_context_projection = self._optional_projection(config.global_context_dim)
        self.ticker_context_projection = self._optional_projection(config.ticker_context_dim)
        summary_dim = config.temporal_d_model * 2
        if config.global_context_dim > 0:
            summary_dim += config.temporal_d_model
        if config.ticker_context_dim > 0:
            summary_dim += config.temporal_d_model

        self.temporal_summary_mlp = nn.Sequential(
            nn.Linear(summary_dim, config.temporal_d_model),
            nn.GELU(),
            nn.LayerNorm(config.temporal_d_model),
            nn.Dropout(config.dropout),
            nn.Linear(config.temporal_d_model, config.temporal_d_model),
            nn.GELU(),
        )
        self.return_horizon_head = nn.Linear(config.temporal_d_model, len(self.horizons))

    def _optional_projection(self, dim: int) -> nn.Module | None:
        if int(dim) <= 0:
            return None
        return nn.Sequential(
            nn.Linear(int(dim), self.config.temporal_d_model),
            nn.GELU(),
            nn.LayerNorm(self.config.temporal_d_model),
        )

    def forward(
        self,
        context_embeddings: torch.Tensor,
        *,
        global_context: torch.Tensor | None = None,
        ticker_context: torch.Tensor | None = None,
    ) -> TemporalReturnOutput:
        return TemporalReturnOutput(
            return_prediction_norm=self.predict_return_tensor(
                context_embeddings,
                global_context=global_context,
                ticker_context=ticker_context,
            )
        )

    def predict_return_tensor(
        self,
        context_embeddings: torch.Tensor,
        *,
        global_context: torch.Tensor | None = None,
        ticker_context: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Tensor-only path for inference, summaries, and graph tools.

        `forward()` returns `TemporalReturnOutput` for training readability, but
        `torchinfo` and `torchview` expect called modules to return tensors or
        tensor containers. This method uses the same layers and weights while
        returning only the `[B, H]` return prediction tensor.
        """

        # Input shape: [B, K, E]. Output shape: [B, K, D].
        tokens = self.context_embedding_projection(context_embeddings)
        # Input shape: [K]. Output shape after unsqueeze: [1, K, D].
        positions = torch.arange(tokens.shape[1], device=tokens.device, dtype=torch.long)
        tokens = tokens + self.context_position_embedding(positions).unsqueeze(0)
        # Input shape: [B, K, D]. Output shape: [B, K, D].
        encoded = self.temporal_context_norm(self.temporal_context_encoder(tokens))
        # Shape: [B, D]. Newest context token is always the final token.
        newest_summary = encoded[:, -1, :]
        # Shape: [B, D]. Mean gives a cheap global view over the longer context.
        mean_summary = encoded.mean(dim=1)
        summaries = [newest_summary, mean_summary]
        if self.global_context_projection is not None and global_context is not None:
            summaries.append(self.global_context_projection(global_context))
        if self.ticker_context_projection is not None and ticker_context is not None:
            summaries.append(self.ticker_context_projection(ticker_context))
        # Shape: [B, 2D (+ optional context projections)] -> [B, D].
        latent = self.temporal_summary_mlp(torch.cat(summaries, dim=-1))
        # Shape: [B, H].
        return self.return_horizon_head(latent)
