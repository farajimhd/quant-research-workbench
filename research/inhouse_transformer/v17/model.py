from __future__ import annotations

import torch
from torch import nn

from research.inhouse_transformer.v17.config import ModelConfig


class TokenBranchEncoder(nn.Module):
    """v16-style market/time token encoder for one temporal scale."""

    def __init__(
        self,
        *,
        feature_count: int,
        time_feature_count: int,
        context_length: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.feature_count = feature_count
        self.time_feature_count = time_feature_count
        self.input_token_count = feature_count + time_feature_count
        self.context_length = context_length
        self.feature_attention_chunk_size = max(1, int(config.feature_attention_chunk_size))

        self.value_projection = nn.Linear(1, config.d_model)
        self.feature_embedding = nn.Embedding(feature_count, config.d_model)
        self.time_value_projection = nn.Linear(1, config.d_model)
        self.time_feature_embedding = nn.Embedding(time_feature_count, config.d_model)
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
        self.temporal_encoder = nn.TransformerEncoder(temporal_layer, num_layers=config.temporal_layers)
        self.temporal_norm = nn.LayerNorm(config.d_model)

    def forward(self, values: torch.Tensor, time_features: torch.Tensor) -> torch.Tensor:
        batch_size, seq_len, feature_count = values.shape
        time_batch_size, time_seq_len, time_feature_count = time_features.shape
        if time_batch_size != batch_size:
            raise ValueError(f"Expected time feature batch size {batch_size}, got {time_batch_size}.")
        if time_seq_len != seq_len:
            raise ValueError(f"Expected time feature context length {seq_len}, got {time_seq_len}.")
        if seq_len != self.context_length:
            raise ValueError(f"Expected context length {self.context_length}, got {seq_len}.")
        if feature_count != self.feature_count:
            raise ValueError(f"Expected {self.feature_count} features, got {feature_count}.")
        if time_feature_count != self.time_feature_count:
            raise ValueError(f"Expected {self.time_feature_count} time features, got {time_feature_count}.")

        token_values = self.value_projection(values.unsqueeze(-1))
        time_values = self.time_value_projection(time_features.unsqueeze(-1))
        feature_ids = torch.arange(feature_count, device=values.device)
        time_feature_ids = torch.arange(time_feature_count, device=values.device)
        position_ids = torch.arange(seq_len, device=values.device)
        feature_embed = self.feature_embedding(feature_ids).view(1, 1, feature_count, -1)
        time_feature_embed = self.time_feature_embedding(time_feature_ids).view(1, 1, time_feature_count, -1)
        position_embed = self.position_embedding(position_ids).view(1, seq_len, 1, -1)

        market_tokens = token_values + feature_embed + position_embed
        time_tokens = time_values + time_feature_embed + position_embed
        tokens = torch.cat([market_tokens, time_tokens], dim=2)
        flat_tokens = tokens.reshape(batch_size * seq_len, self.input_token_count, -1)
        encoded_features = self.encode_features(flat_tokens)
        feature_weights = torch.softmax(self.feature_pool(encoded_features), dim=1)
        bar_tokens = (encoded_features * feature_weights).sum(dim=1).reshape(batch_size, seq_len, -1)

        temporal_tokens = self.temporal_encoder(bar_tokens)
        return self.temporal_norm(temporal_tokens[:, -1, :])

    def encode_features(self, flat_tokens: torch.Tensor) -> torch.Tensor:
        if flat_tokens.shape[0] <= self.feature_attention_chunk_size:
            return self.feature_encoder(flat_tokens)
        encoded_chunks = [
            self.feature_encoder(chunk)
            for chunk in flat_tokens.split(self.feature_attention_chunk_size, dim=0)
        ]
        return torch.cat(encoded_chunks, dim=0)


class FeatureTemporalTransformer(nn.Module):
    """v17 binary target model with v16 base encoder plus gated macro context branches."""

    def __init__(
        self,
        *,
        feature_count: int,
        time_feature_count: int,
        context_length: int,
        horizon: int,
        target_count: int,
        config: ModelConfig,
        macro_feature_count: int,
        macro_context_length: int,
        anchor_feature_count: int,
        anchor_context_length: int,
    ) -> None:
        super().__init__()
        self.horizon = horizon
        self.target_count = target_count
        self.config = config
        self.target_bit_count = config.target_bit_count
        self.macro_branch_count = 4

        self.one_min_encoder = TokenBranchEncoder(
            feature_count=feature_count,
            time_feature_count=time_feature_count,
            context_length=context_length,
            config=config,
        )
        self.macro_15m_encoder = TokenBranchEncoder(
            feature_count=macro_feature_count,
            time_feature_count=time_feature_count,
            context_length=macro_context_length,
            config=config,
        )
        self.macro_1h_encoder = TokenBranchEncoder(
            feature_count=macro_feature_count,
            time_feature_count=time_feature_count,
            context_length=macro_context_length,
            config=config,
        )
        self.macro_1d_encoder = TokenBranchEncoder(
            feature_count=macro_feature_count,
            time_feature_count=time_feature_count,
            context_length=macro_context_length,
            config=config,
        )
        self.anchor_encoder = TokenBranchEncoder(
            feature_count=anchor_feature_count,
            time_feature_count=time_feature_count,
            context_length=anchor_context_length,
            config=config,
        )

        self.macro_scale_embedding = nn.Embedding(self.macro_branch_count, config.d_model)
        fusion_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.num_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.macro_fusion_encoder = nn.TransformerEncoder(fusion_layer, num_layers=1)
        self.macro_gate = nn.Linear(config.d_model * 2, 1)
        nn.init.constant_(self.macro_gate.bias, config.macro_gate_init_bias)
        self.macro_dropout = nn.Dropout(config.macro_dropout)
        self.fusion_norm = nn.LayerNorm(config.d_model)

        self.regression_head = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, horizon * target_count * config.target_bit_count),
        )

    def forward(
        self,
        values: torch.Tensor,
        time_features: torch.Tensor,
        macro_15m_values: torch.Tensor,
        macro_15m_time_features: torch.Tensor,
        macro_1h_values: torch.Tensor,
        macro_1h_time_features: torch.Tensor,
        macro_1d_values: torch.Tensor,
        macro_1d_time_features: torch.Tensor,
        anchor_values: torch.Tensor,
        anchor_time_features: torch.Tensor,
    ) -> torch.Tensor:
        main_summary = self.one_min_encoder(values, time_features)
        macro_summaries = torch.stack(
            [
                self.macro_15m_encoder(macro_15m_values, macro_15m_time_features),
                self.macro_1h_encoder(macro_1h_values, macro_1h_time_features),
                self.macro_1d_encoder(macro_1d_values, macro_1d_time_features),
                self.anchor_encoder(anchor_values, anchor_time_features),
            ],
            dim=1,
        )
        scale_ids = torch.arange(self.macro_branch_count, device=values.device)
        macro_tokens = macro_summaries + self.macro_scale_embedding(scale_ids).view(1, self.macro_branch_count, -1)
        macro_tokens = self.macro_fusion_encoder(macro_tokens)

        repeated_main = main_summary.unsqueeze(1).expand_as(macro_tokens)
        gates = torch.sigmoid(self.macro_gate(torch.cat([repeated_main, macro_tokens], dim=-1)))
        if self.training and self.config.macro_dropout > 0.0:
            gates = self.macro_dropout(gates)
        macro_update = (macro_tokens * gates).sum(dim=1) / max(1, self.macro_branch_count)
        fused_token = self.fusion_norm(main_summary + macro_update)
        prediction = self.regression_head(fused_token).reshape(
            values.shape[0],
            self.horizon,
            self.target_count,
            self.target_bit_count,
        )
        return prediction


def forecast_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
) -> tuple[torch.Tensor, dict[str, float]]:
    total = nn.functional.binary_cross_entropy_with_logits(prediction, target)
    bit_accuracy = ((prediction.detach().sigmoid() >= 0.5) == (target >= 0.5)).float().mean()
    return total, {
        "loss": float(total.detach().cpu()),
        "regression_loss": float(total.detach().cpu()),
        "bit_accuracy_pct": float(bit_accuracy.detach().cpu() * 100.0),
    }
