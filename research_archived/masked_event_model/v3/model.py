from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v3.config import ModelConfig
from research.masked_event_model.v3.masking import MaskBatch


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


@dataclass(slots=True)
class ModelOutput:
    quote_reconstruction: torch.Tensor
    quote_reconstruction_indices: torch.Tensor
    trade_reconstruction: torch.Tensor
    trade_reconstruction_indices: torch.Tensor
    summary_reconstruction: torch.Tensor
    summary_reconstruction_indices: torch.Tensor
    event_kind_logits: torch.Tensor
    event_kind_indices: torch.Tensor
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
        self.embedding_dim = config.embedding_dim
        self.quote_encoder_visible_events = max(1, min(max_quote_events, int(round(max_quote_events * config.encoder_visible_ratio))))
        self.trade_encoder_visible_events = max(1, min(max_trade_events, int(round(max_trade_events * config.encoder_visible_ratio))))

        self.quote_value_projection = MLPProjection(quote_feature_count, config.d_model, config.d_model, config.dropout)
        self.trade_value_projection = MLPProjection(trade_feature_count, config.d_model, config.d_model, config.dropout)
        self.summary_projection = MLPProjection(summary_feature_count, config.d_model, config.d_model, config.dropout)

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
        self.embedding_projection = nn.Linear(config.d_model, config.embedding_dim)
        self.decoder_projection = nn.Linear(config.embedding_dim, config.d_model)

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
            nn.Linear(config.embedding_dim, config.ff_dim),
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
        encoded_chunks, embedding_chunks, embedding = self._encode_inputs(
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
        decoder_input_chunks = self.decoder_projection(embedding_chunks)
        decoded_chunks = self.decoder_norm(self.decoder(decoder_input_chunks))
        quote_indices = (masks.quote_value_mask & (quote_values.abs().sum(dim=-1, keepdim=True) > 0.0)).any(dim=-1).nonzero(as_tuple=False)
        trade_indices = (masks.trade_value_mask & (trade_values.abs().sum(dim=-1, keepdim=True) > 0.0)).any(dim=-1).nonzero(as_tuple=False)
        summary_indices = masks.summary_value_mask.any(dim=-1).nonzero(as_tuple=False)
        event_kind_indices = (masks.event_kind_mask & (event_kinds != 2)).nonzero(as_tuple=False)
        event_pos = torch.arange(self.max_total_events, device=quote_values.device)
        kind_inputs = event_kinds.clamp(0, 2)

        quote_dec_tokens = self._sparse_event_tokens(
            decoded_chunks,
            quote_indices,
            self.quote_position_embedding(quote_pos),
        )
        trade_dec_tokens = self._sparse_event_tokens(
            decoded_chunks,
            trade_indices,
            self.trade_position_embedding(trade_pos),
        )
        summary_dec_tokens = decoded_chunks[summary_indices[:, 0], summary_indices[:, 1]] if summary_indices.numel() else decoded_chunks.new_empty((0, self.d_model))
        event_dec_tokens = self._sparse_event_kind_tokens(
            decoded_chunks,
            event_kind_indices,
            self.event_position_embedding(event_pos),
            self.event_kind_embedding(kind_inputs),
        )
        forecast = self.forecast_head(embedding).reshape(batch, self.horizon_count, 1, self.target_bit_count)
        return ModelOutput(
            quote_reconstruction=self.quote_decoder_head(quote_dec_tokens),
            quote_reconstruction_indices=quote_indices,
            trade_reconstruction=self.trade_decoder_head(trade_dec_tokens),
            trade_reconstruction_indices=trade_indices,
            summary_reconstruction=self.summary_decoder_head(summary_dec_tokens),
            summary_reconstruction_indices=summary_indices,
            event_kind_logits=self.event_kind_head(event_dec_tokens),
            event_kind_indices=event_kind_indices,
            forecast_logits=forecast,
            embeddings=embedding,
            encoded_chunks=encoded_chunks,
        )

    def _sparse_event_tokens(
        self,
        decoded_chunks: torch.Tensor,
        indices: torch.Tensor,
        position_embeddings: torch.Tensor,
    ) -> torch.Tensor:
        if indices.numel() == 0:
            return decoded_chunks.new_empty((0, self.d_model))
        return decoded_chunks[indices[:, 0], indices[:, 1]] + position_embeddings[indices[:, 2]]

    def _sparse_event_kind_tokens(
        self,
        decoded_chunks: torch.Tensor,
        indices: torch.Tensor,
        event_position_embeddings: torch.Tensor,
        event_kind_embeddings: torch.Tensor,
    ) -> torch.Tensor:
        if indices.numel() == 0:
            return decoded_chunks.new_empty((0, self.d_model))
        return (
            decoded_chunks[indices[:, 0], indices[:, 1]]
            + event_position_embeddings[indices[:, 2]]
            + event_kind_embeddings[indices[:, 0], indices[:, 1], indices[:, 2]]
        )

    def _encode_inputs(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
        masks: MaskBatch | None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        batch, chunks, quote_events, _ = quote_values.shape
        trade_events = trade_values.shape[2]
        quote_valid = quote_values.abs().sum(dim=-1) > 0.0
        trade_valid = trade_values.abs().sum(dim=-1) > 0.0

        if masks is None:
            masked_quote_values = quote_values
            masked_trade_values = trade_values
            masked_summary = chunk_summary
        else:
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
        quote_padding = (~quote_valid).reshape(batch * chunks, quote_events)
        trade_padding = (~trade_valid).reshape(batch * chunks, trade_events)
        if masks is not None:
            quote_tokens = torch.where(masks.quote_token_mask.unsqueeze(-1), self.quote_mask_token, quote_tokens)
            trade_tokens = torch.where(masks.trade_token_mask.unsqueeze(-1), self.trade_mask_token, trade_tokens)
            quote_visible = quote_valid & ~masks.quote_token_mask
            trade_visible = trade_valid & ~masks.trade_token_mask
            quote_tokens, quote_padding = compact_visible_tokens(
                quote_tokens.reshape(batch * chunks, quote_events, self.d_model),
                quote_visible.reshape(batch * chunks, quote_events),
                max_tokens=self.quote_encoder_visible_events,
            )
            trade_tokens, trade_padding = compact_visible_tokens(
                trade_tokens.reshape(batch * chunks, trade_events, self.d_model),
                trade_visible.reshape(batch * chunks, trade_events),
                max_tokens=self.trade_encoder_visible_events,
            )
        else:
            quote_tokens = quote_tokens.reshape(batch * chunks, quote_events, self.d_model)
            trade_tokens = trade_tokens.reshape(batch * chunks, trade_events, self.d_model)

        quote_padding = ensure_not_all_padding(quote_padding)
        trade_padding = ensure_not_all_padding(trade_padding)

        quote_encoded = self.quote_event_encoder(quote_tokens, src_key_padding_mask=quote_padding)
        trade_encoded = self.trade_event_encoder(trade_tokens, src_key_padding_mask=trade_padding)
        quote_pooled = masked_mean(quote_encoded, ~quote_padding).reshape(batch, chunks, self.d_model)
        trade_pooled = masked_mean(trade_encoded, ~trade_padding).reshape(batch, chunks, self.d_model)
        if masks is not None:
            summary_tokens = torch.where(masks.chunk_mask.unsqueeze(-1), self.summary_mask_token, summary_tokens)

        chunk_tokens = self.fusion(torch.cat([quote_pooled, trade_pooled, summary_tokens], dim=-1))
        chunk_pos = torch.arange(chunks, device=quote_values.device)
        chunk_tokens = chunk_tokens + self.chunk_position_embedding(chunk_pos).view(1, chunks, -1)
        encoded_chunks = self.encoder_norm(self.temporal_encoder(chunk_tokens))
        embedding_chunks = self.embedding_projection(encoded_chunks)
        embedding = embedding_chunks[:, -1, :]
        return encoded_chunks, embedding_chunks, embedding

    def encode(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
    ) -> torch.Tensor:
        return self._encode_inputs(quote_values, trade_values, event_kinds, event_indices, chunk_summary, None)[2]

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
    if mask.shape[1] == 0:
        return mask
    all_padding = mask.all(dim=1, keepdim=True)
    first = mask[:, :1] & ~all_padding
    if mask.shape[1] == 1:
        return first
    return torch.cat([first, mask[:, 1:]], dim=1)


def masked_mean(values: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    weights = valid.float().unsqueeze(-1)
    return (values * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)


def compact_visible_tokens(tokens: torch.Tensor, visible_mask: torch.Tensor, *, max_tokens: int) -> tuple[torch.Tensor, torch.Tensor]:
    keep_count = max(1, min(int(max_tokens), tokens.shape[1]))
    scores = torch.rand(visible_mask.shape, device=tokens.device).masked_fill(~visible_mask, -1.0)
    indices = scores.topk(keep_count, dim=1).indices.sort(dim=1).values
    compact_tokens = tokens.gather(1, indices.unsqueeze(-1).expand(-1, -1, tokens.shape[-1]))
    compact_padding = ~visible_mask.gather(1, indices)
    return compact_tokens, compact_padding
