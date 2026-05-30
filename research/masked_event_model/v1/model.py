from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v1.config import ModelConfig
from research.masked_event_model.v1.masking import MaskBatch


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


@dataclass(slots=True)
class ModelOutput:
    quote_reconstruction: torch.Tensor
    trade_reconstruction: torch.Tensor
    summary_reconstruction: torch.Tensor
    event_kind_logits: torch.Tensor
    forecast_logits: torch.Tensor
    embeddings: torch.Tensor
    encoded_chunks: torch.Tensor


class MLPProjection(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.net(values)


class MaskedEventAutoencoder(nn.Module):
    def __init__(
        self,
        *,
        quote_feature_count: int,
        trade_feature_count: int,
        summary_feature_count: int,
        context_chunks: int,
        max_quote_events: int,
        max_trade_events: int,
        max_total_events: int,
        horizon_count: int,
        target_bit_count: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.quote_feature_count = quote_feature_count
        self.trade_feature_count = trade_feature_count
        self.summary_feature_count = summary_feature_count
        self.context_chunks = context_chunks
        self.max_quote_events = max_quote_events
        self.max_trade_events = max_trade_events
        self.max_total_events = max_total_events
        self.horizon_count = horizon_count
        self.target_bit_count = target_bit_count
        self.d_model = config.d_model

        self.quote_value_projection = MLPProjection(quote_feature_count, config.d_model, config.ff_dim, config.dropout)
        self.trade_value_projection = MLPProjection(trade_feature_count, config.d_model, config.ff_dim, config.dropout)
        self.summary_projection = MLPProjection(summary_feature_count, config.d_model, config.ff_dim, config.dropout)

        self.quote_position_embedding = nn.Embedding(max_quote_events, config.d_model)
        self.trade_position_embedding = nn.Embedding(max_trade_events, config.d_model)
        self.event_position_embedding = nn.Embedding(max_total_events, config.d_model)
        self.chunk_position_embedding = nn.Embedding(context_chunks, config.d_model)
        self.event_kind_embedding = nn.Embedding(3, config.d_model)

        self.quote_mask_token = nn.Parameter(torch.zeros(1, 1, 1, config.d_model))
        self.trade_mask_token = nn.Parameter(torch.zeros(1, 1, 1, config.d_model))
        self.summary_mask_token = nn.Parameter(torch.zeros(1, 1, config.d_model))

        quote_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        trade_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.quote_event_encoder = transformer_encoder(quote_layer, num_layers=config.quote_event_layers)
        self.trade_event_encoder = transformer_encoder(trade_layer, num_layers=config.trade_event_layers)

        self.fusion = nn.Sequential(
            nn.Linear(config.d_model * 3, config.d_model),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.LayerNorm(config.d_model),
        )
        temporal_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.temporal_encoder = transformer_encoder(temporal_layer, num_layers=config.temporal_layers)
        self.encoder_norm = nn.LayerNorm(config.d_model)

        decoder_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.decoder = transformer_encoder(decoder_layer, num_layers=config.decoder_layers)
        self.decoder_norm = nn.LayerNorm(config.d_model)

        self.quote_decoder_head = nn.Linear(config.d_model, quote_feature_count)
        self.trade_decoder_head = nn.Linear(config.d_model, trade_feature_count)
        self.summary_decoder_head = nn.Linear(config.d_model, summary_feature_count)
        self.event_kind_head = nn.Linear(config.d_model, 3)
        self.forecast_head = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, horizon_count * target_bit_count),
        )
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.normal_(self.quote_mask_token, std=0.02)
        nn.init.normal_(self.trade_mask_token, std=0.02)
        nn.init.normal_(self.summary_mask_token, std=0.02)

    def forward(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
        masks: MaskBatch,
    ) -> ModelOutput:
        encoded_chunks, embedding = self._encode_inputs(
            quote_values,
            trade_values,
            event_kinds,
            event_indices,
            chunk_summary,
            masks,
        )
        batch, chunks, quote_events, _ = quote_values.shape
        trade_events = trade_values.shape[2]
        quote_pos = torch.arange(quote_events, device=quote_values.device)
        trade_pos = torch.arange(trade_events, device=trade_values.device)
        decoded_chunks = self.decoder_norm(self.decoder(encoded_chunks))
        quote_dec_tokens = decoded_chunks.unsqueeze(2) + self.quote_position_embedding(quote_pos).view(1, 1, quote_events, -1)
        trade_dec_tokens = decoded_chunks.unsqueeze(2) + self.trade_position_embedding(trade_pos).view(1, 1, trade_events, -1)
        event_pos = torch.arange(self.max_total_events, device=quote_values.device)
        kind_inputs = event_kinds.clamp(0, 2)
        event_dec_tokens = (
            decoded_chunks.unsqueeze(2)
            + self.event_position_embedding(event_pos).view(1, 1, self.max_total_events, -1)
            + self.event_kind_embedding(kind_inputs)
        )
        forecast = self.forecast_head(embedding).reshape(batch, self.horizon_count, 1, self.target_bit_count)
        return ModelOutput(
            quote_reconstruction=self.quote_decoder_head(quote_dec_tokens),
            trade_reconstruction=self.trade_decoder_head(trade_dec_tokens),
            summary_reconstruction=self.summary_decoder_head(decoded_chunks),
            event_kind_logits=self.event_kind_head(event_dec_tokens),
            forecast_logits=forecast,
            embeddings=embedding,
            encoded_chunks=encoded_chunks,
        )

    def _encode_inputs(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
        masks: MaskBatch,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        batch, chunks, quote_events, _ = quote_values.shape
        trade_events = trade_values.shape[2]
        quote_valid = quote_values.abs().sum(dim=-1) > 0.0
        trade_valid = trade_values.abs().sum(dim=-1) > 0.0

        masked_quote_values = quote_values.masked_fill(masks.quote_value_mask, 0.0)
        masked_trade_values = trade_values.masked_fill(masks.trade_value_mask, 0.0)
        masked_summary = chunk_summary.masked_fill(masks.summary_value_mask, 0.0)

        quote_tokens = self.quote_value_projection(masked_quote_values)
        trade_tokens = self.trade_value_projection(masked_trade_values)
        summary_tokens = self.summary_projection(masked_summary)

        quote_pos = torch.arange(quote_events, device=quote_values.device)
        trade_pos = torch.arange(trade_events, device=trade_values.device)
        quote_tokens = quote_tokens + self.quote_position_embedding(quote_pos).view(1, 1, quote_events, -1)
        trade_tokens = trade_tokens + self.trade_position_embedding(trade_pos).view(1, 1, trade_events, -1)
        quote_tokens = torch.where(masks.quote_token_mask.unsqueeze(-1), self.quote_mask_token, quote_tokens)
        trade_tokens = torch.where(masks.trade_token_mask.unsqueeze(-1), self.trade_mask_token, trade_tokens)

        flat_quote = quote_tokens.reshape(batch * chunks, quote_events, self.d_model)
        flat_trade = trade_tokens.reshape(batch * chunks, trade_events, self.d_model)
        quote_padding = (~quote_valid).reshape(batch * chunks, quote_events)
        trade_padding = (~trade_valid).reshape(batch * chunks, trade_events)
        quote_padding = ensure_not_all_padding(quote_padding)
        trade_padding = ensure_not_all_padding(trade_padding)

        quote_encoded = self.quote_event_encoder(flat_quote, src_key_padding_mask=quote_padding)
        trade_encoded = self.trade_event_encoder(flat_trade, src_key_padding_mask=trade_padding)
        quote_pooled = masked_mean(quote_encoded, ~quote_padding).reshape(batch, chunks, self.d_model)
        trade_pooled = masked_mean(trade_encoded, ~trade_padding).reshape(batch, chunks, self.d_model)
        summary_tokens = torch.where(masks.chunk_mask.unsqueeze(-1), self.summary_mask_token, summary_tokens)

        chunk_tokens = self.fusion(torch.cat([quote_pooled, trade_pooled, summary_tokens], dim=-1))
        chunk_pos = torch.arange(chunks, device=quote_values.device)
        chunk_tokens = chunk_tokens + self.chunk_position_embedding(chunk_pos).view(1, chunks, -1)
        encoded_chunks = self.encoder_norm(self.temporal_encoder(chunk_tokens))
        embedding = encoded_chunks[:, -1, :]
        return encoded_chunks, embedding

    def encode(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
    ) -> torch.Tensor:
        empty_masks = MaskBatch(
            quote_value_mask=torch.zeros_like(quote_values, dtype=torch.bool),
            trade_value_mask=torch.zeros_like(trade_values, dtype=torch.bool),
            summary_value_mask=torch.zeros_like(chunk_summary, dtype=torch.bool),
            event_kind_mask=torch.zeros_like(event_kinds, dtype=torch.bool),
            quote_token_mask=torch.zeros_like(quote_values[..., 0], dtype=torch.bool),
            trade_token_mask=torch.zeros_like(trade_values[..., 0], dtype=torch.bool),
            chunk_mask=torch.zeros_like(chunk_summary[..., 0], dtype=torch.bool),
        )
        return self._encode_inputs(quote_values, trade_values, event_kinds, event_indices, chunk_summary, empty_masks)[1]

    def forecast_only(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
    ) -> torch.Tensor:
        embedding = self.encode(quote_values, trade_values, event_kinds, event_indices, chunk_summary)
        batch = quote_values.shape[0]
        return self.forecast_head(embedding).reshape(batch, self.horizon_count, 1, self.target_bit_count)


def ensure_not_all_padding(mask: torch.Tensor) -> torch.Tensor:
    if not mask.any():
        return mask
    all_padding = mask.all(dim=1)
    if all_padding.any():
        mask = mask.clone()
        mask[all_padding, 0] = False
    return mask


def masked_mean(values: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    weights = valid.float().unsqueeze(-1)
    return (values * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)
