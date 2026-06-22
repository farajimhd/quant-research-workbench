from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import nn

from research.temporal_event_model.v1.config import EncoderConfig, ModelConfig


HEADER_BYTES = 14
EVENT_BYTES = 16
BITS_PER_BYTE = 8


@dataclass(slots=True)
class TemporalEventOutput:
    header_bit_logits: torch.Tensor
    event_bit_logits: torch.Tensor
    chunk_embeddings: torch.Tensor


@dataclass(slots=True)
class FutureChunkLabelOutput:
    price_target_logits: torch.Tensor
    chunk_embedding: torch.Tensor


class FuturePriceExtremaMLPDecoder(nn.Module):
    """Fast nonlinear head for cache-v2 future price-extrema bit prediction.

    The cache-v2 downstream experiment has only one current chunk as input.
    The frozen event encoder converts that chunk into `[B, embedding_dim]`.
    This decoder maps the embedding directly to the target bits for the stored
    future chunks. Each future chunk target contains the price-relevant header
    bits plus the low/high price-delta int16 bits for that chunk.
    """

    def __init__(self, *, embedding_dim: int, hidden_dim: int, target_chunks: int, target_bits: int, dropout: float) -> None:
        super().__init__()
        self.feature_mlp = nn.Sequential(
            nn.LayerNorm(embedding_dim),
            nn.Linear(embedding_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
        )
        self.label_head = nn.Linear(hidden_dim, int(target_chunks) * int(target_bits))
        self.target_chunks = int(target_chunks)
        self.target_bits = int(target_bits)

    def forward(self, chunk_embedding: torch.Tensor) -> torch.Tensor:
        features = self.feature_mlp(chunk_embedding)
        return self.label_head(features).view(features.shape[0], self.target_chunks, self.target_bits)


class SingleChunkFutureLabelPredictor(nn.Module):
    """Frozen event encoder plus simple MLP decoder for cache-v2 labels.

    Input is exactly one compact event chunk:

    - `header_uint8`: `[B, 14]`
    - `events_uint8`: `[B, 128, 16]`

    Output is `price_target_logits`: `[B, target_chunks, target_bits]`. The
    default cache-v2 probe uses two target chunks and predicts the price-header
    plus low/high extrema bits for each future chunk.
    """

    def __init__(
        self,
        *,
        event_encoder: nn.Module,
        embedding_dim: int,
        hidden_dim: int,
        target_chunks: int,
        target_bits: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.event_encoder = event_encoder
        self.decoder = FuturePriceExtremaMLPDecoder(
            embedding_dim=embedding_dim,
            hidden_dim=hidden_dim,
            target_chunks=target_chunks,
            target_bits=target_bits,
            dropout=dropout,
        )

    def encode_chunk(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        return self.event_encoder(header_uint8, events_uint8)

    def decode_embedding(self, chunk_embedding: torch.Tensor) -> torch.Tensor:
        return self.decoder(chunk_embedding)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> FutureChunkLabelOutput:
        chunk_embedding = self.encode_chunk(header_uint8, events_uint8)
        price_target_logits = self.decode_embedding(chunk_embedding.float())
        return FutureChunkLabelOutput(price_target_logits=price_target_logits, chunk_embedding=chunk_embedding)


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


class FutureChunkQueryEmbedding(nn.Embedding):
    """Learned horizon query tokens; each token asks for one future event chunk."""


class TemporalPositionEmbedding(nn.Embedding):
    """Position embedding over the sequence of past chunk embeddings."""


class TemporalEventPredictor(nn.Module):
    """Predict future compact event chunks from a sequence of past chunk embeddings.

    The event encoder is intentionally isolated behind `encode_context_chunks`.
    That makes the downstream model compatible with either a frozen pretrained
    encoder or a later end-to-end fine-tuning experiment.
    """

    def __init__(self, *, event_encoder: nn.Module, config: ModelConfig, context_chunks: int, target_chunks: int) -> None:
        super().__init__()
        self.event_encoder = event_encoder
        self.config = config
        self.context_chunks = int(context_chunks)
        self.target_chunks = int(target_chunks)
        self.chunk_embedding_to_temporal_width = nn.Linear(config.embedding_dim, config.temporal_d_model)
        self.context_position_embedding = TemporalPositionEmbedding(self.context_chunks, config.temporal_d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.temporal_d_model,
            nhead=config.temporal_heads,
            dim_feedforward=config.temporal_ff_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.temporal_encoder = transformer_encoder(encoder_layer, num_layers=config.temporal_layers)
        self.future_chunk_query_embedding = FutureChunkQueryEmbedding(self.target_chunks, config.temporal_d_model)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=config.temporal_d_model,
            nhead=config.temporal_heads,
            dim_feedforward=config.temporal_ff_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.future_chunk_decoder = nn.TransformerDecoder(decoder_layer, num_layers=config.decoder_layers)
        self.future_header_bit_head = nn.Sequential(
            nn.LayerNorm(config.temporal_d_model),
            nn.Linear(config.temporal_d_model, HEADER_BYTES * BITS_PER_BYTE),
        )
        self.future_event_bit_head = nn.Sequential(
            nn.LayerNorm(config.temporal_d_model),
            nn.Linear(config.temporal_d_model, 128 * EVENT_BYTES * BITS_PER_BYTE),
        )

    def forward(self, context_header_uint8: torch.Tensor, context_events_uint8: torch.Tensor) -> TemporalEventOutput:
        chunk_embeddings = self.encode_context_chunks(context_header_uint8, context_events_uint8)
        temporal_tokens = self.chunk_embedding_to_temporal_width(chunk_embeddings)
        positions = torch.arange(self.context_chunks, device=temporal_tokens.device).view(1, -1)
        temporal_tokens = temporal_tokens + self.context_position_embedding(positions)
        temporal_memory = self.temporal_encoder(temporal_tokens)
        query_ids = torch.arange(self.target_chunks, device=temporal_tokens.device).view(1, -1).expand(temporal_tokens.shape[0], -1)
        future_queries = self.future_chunk_query_embedding(query_ids)
        decoded_future = self.future_chunk_decoder(future_queries, temporal_memory)
        header_logits = self.future_header_bit_head(decoded_future).view(
            decoded_future.shape[0], self.target_chunks, HEADER_BYTES, BITS_PER_BYTE
        )
        event_logits = self.future_event_bit_head(decoded_future).view(
            decoded_future.shape[0], self.target_chunks, 128, EVENT_BYTES, BITS_PER_BYTE
        )
        return TemporalEventOutput(header_bit_logits=header_logits, event_bit_logits=event_logits, chunk_embeddings=chunk_embeddings)

    def encode_context_chunks(self, context_header_uint8: torch.Tensor, context_events_uint8: torch.Tensor) -> torch.Tensor:
        batch_size, context_chunks = context_header_uint8.shape[:2]
        flat_header = context_header_uint8.reshape(batch_size * context_chunks, HEADER_BYTES)
        flat_events = context_events_uint8.reshape(batch_size * context_chunks, 128, EVENT_BYTES)
        flat_embedding = self.event_encoder(flat_header, flat_events)
        return flat_embedding.reshape(batch_size, context_chunks, -1)


def build_event_encoder(config: EncoderConfig, *, events_per_chunk: int, device: torch.device) -> nn.Module:
    """Build and optionally initialize a standalone masked-event encoder."""

    version = config.version.lower().strip()
    if version not in {"v6", "v7", "v8"}:
        raise ValueError(f"Unsupported encoder version {config.version!r}; expected v6, v7, or v8.")
    model_module = importlib.import_module(f"research.masked_event_model.{version}.model")
    config_module = importlib.import_module(f"research.masked_event_model.{version}.config")
    encoder_model_config = config_module.ModelConfig(
        d_byte=config.d_byte,
        d_model=config.d_model,
        embedding_dim=config.embedding_dim,
        n_heads=config.n_heads,
        encoder_layers=config.encoder_layers,
        decoder_layers=config.decoder_layers,
        ffn_mult=config.ffn_mult,
        dropout=config.dropout,
    )
    autoencoder = model_module.EventTokenMaskedAutoencoder(events_per_chunk=events_per_chunk, config=encoder_model_config)
    if config.checkpoint:
        load_pretrained_autoencoder(autoencoder, config.checkpoint)
    encoder = autoencoder.build_encoder_model().to(device)
    if config.freeze:
        encoder.eval()
        for parameter in encoder.parameters():
            parameter.requires_grad_(False)
    return encoder


def load_pretrained_autoencoder(model: nn.Module, checkpoint: Path) -> None:
    payload: Any = torch.load(checkpoint, map_location="cpu")
    state = payload.get("model_state_dict") or payload.get("model") or payload.get("state_dict") if isinstance(payload, dict) else payload
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint {checkpoint} does not contain a model state dict.")
    model.load_state_dict(state, strict=False)
