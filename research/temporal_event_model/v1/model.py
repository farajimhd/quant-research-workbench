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
    low_high_tick_pred: torch.Tensor
    up_class_logits: torch.Tensor
    down_class_logits: torch.Tensor
    path_class_logits: torch.Tensor
    chunk_embedding: torch.Tensor


class FuturePriceExtremaMLPDecoder(nn.Module):
    """Fast nonlinear head for cache-v2 future price-extrema prediction.

    The cache-v2 downstream experiment has only one current chunk as input.
    The frozen event encoder converts that chunk into `[B, embedding_dim]`.
    This decoder maps the embedding to two complementary targets per stored
    future chunk:

    - normalized absolute low/high ticks for regression;
    - categorical up/down/path labels for confusion-matrix diagnostics.

    The future header is used only by the data pipeline to create labels. The
    model no longer predicts future header bits, which avoids letting small
    anchor-bit errors dominate semantic direction metrics.
    """

    def __init__(self, *, embedding_dim: int, hidden_dim: int, target_chunks: int, dropout: float) -> None:
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
        self.low_high_tick_head = nn.Linear(hidden_dim, int(target_chunks) * 2)
        self.up_class_head = nn.Linear(hidden_dim, int(target_chunks) * 3)
        self.down_class_head = nn.Linear(hidden_dim, int(target_chunks) * 3)
        self.path_class_head = nn.Linear(hidden_dim, int(target_chunks) * 4)
        self.target_chunks = int(target_chunks)

    def forward(self, chunk_embedding: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        features = self.feature_mlp(chunk_embedding)
        low_high_tick_pred = self.low_high_tick_head(features).view(features.shape[0], self.target_chunks, 2)
        up_class_logits = self.up_class_head(features).view(features.shape[0], self.target_chunks, 3)
        down_class_logits = self.down_class_head(features).view(features.shape[0], self.target_chunks, 3)
        path_class_logits = self.path_class_head(features).view(features.shape[0], self.target_chunks, 4)
        return low_high_tick_pred, up_class_logits, down_class_logits, path_class_logits


class SingleChunkFutureLabelPredictor(nn.Module):
    """Frozen event encoder plus simple MLP decoder for cache-v2 labels.

    Input is exactly one compact event chunk:

    - `header_uint8`: `[B, 14]`
    - `events_uint8`: `[B, 128, 16]`

    Output contains normalized low/high tick regression values and categorical
    logits for each target chunk. The default cache-v2 probe uses two target
    chunks: future 128 events and future 256 events.
    """

    def __init__(
        self,
        *,
        event_encoder: nn.Module,
        embedding_dim: int,
        hidden_dim: int,
        target_chunks: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.event_encoder = event_encoder
        self.decoder = FuturePriceExtremaMLPDecoder(
            embedding_dim=embedding_dim,
            hidden_dim=hidden_dim,
            target_chunks=target_chunks,
            dropout=dropout,
        )

    def encode_chunk(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        return self.event_encoder(header_uint8, events_uint8)

    def decode_embedding(self, chunk_embedding: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.decoder(chunk_embedding)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> FutureChunkLabelOutput:
        chunk_embedding = self.encode_chunk(header_uint8, events_uint8)
        low_high_tick_pred, up_class_logits, down_class_logits, path_class_logits = self.decode_embedding(chunk_embedding.float())
        return FutureChunkLabelOutput(
            low_high_tick_pred=low_high_tick_pred,
            up_class_logits=up_class_logits,
            down_class_logits=down_class_logits,
            path_class_logits=path_class_logits,
            chunk_embedding=chunk_embedding,
        )


class EmbeddingContextFutureLabelPredictor(nn.Module):
    """Temporal probe over a production-like stream of event-chunk embeddings.

    The event encoder is intentionally outside this module. The training script
    builds a rolling per-ticker embedding stream first, then selects recent and
    older embeddings from that stream exactly like the production ring-buffer
    path is expected to do. This model only learns the temporal head on top of
    frozen chunk embeddings.

    Input:
    - `context_embeddings`: `[B, K, embedding_dim]`, ordered oldest to newest.

    Output:
    - low/high normalized tick predictions and up/down/path logits for each
      future target chunk.
    """

    def __init__(
        self,
        *,
        embedding_dim: int,
        temporal_d_model: int,
        temporal_layers: int,
        temporal_heads: int,
        temporal_ffn_mult: int,
        target_chunks: int,
        hidden_dim: int,
        dropout: float,
        max_context_chunks: int,
    ) -> None:
        super().__init__()
        self.max_context_chunks = int(max_context_chunks)
        self.embedding_to_temporal_width = nn.Sequential(
            nn.LayerNorm(embedding_dim),
            nn.Linear(embedding_dim, temporal_d_model),
            nn.GELU(),
        )
        self.context_position_embedding = TemporalPositionEmbedding(self.max_context_chunks, temporal_d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=temporal_d_model,
            nhead=temporal_heads,
            dim_feedforward=int(temporal_d_model * temporal_ffn_mult),
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.temporal_context_encoder = transformer_encoder(encoder_layer, num_layers=temporal_layers)
        self.newest_context_summary = nn.Sequential(
            nn.LayerNorm(temporal_d_model),
            nn.Linear(temporal_d_model, temporal_d_model),
            nn.GELU(),
        )
        self.decoder = FuturePriceExtremaMLPDecoder(
            embedding_dim=temporal_d_model,
            hidden_dim=hidden_dim,
            target_chunks=target_chunks,
            dropout=dropout,
        )

    def forward(self, context_embeddings: torch.Tensor) -> FutureChunkLabelOutput:
        if context_embeddings.ndim != 3:
            raise ValueError(f"Expected context_embeddings [B,K,E], got {tuple(context_embeddings.shape)}")
        context_chunks = int(context_embeddings.shape[1])
        if context_chunks > self.max_context_chunks:
            raise ValueError(f"context_chunks={context_chunks} exceeds max_context_chunks={self.max_context_chunks}")
        temporal_tokens = self.embedding_to_temporal_width(context_embeddings.float())
        positions = torch.arange(context_chunks, device=temporal_tokens.device).view(1, -1)
        temporal_tokens = temporal_tokens + self.context_position_embedding(positions)
        encoded_context = self.temporal_context_encoder(temporal_tokens)
        summary = self.newest_context_summary(encoded_context[:, -1, :])
        low_high_tick_pred, up_class_logits, down_class_logits, path_class_logits = self.decoder(summary)
        return FutureChunkLabelOutput(
            low_high_tick_pred=low_high_tick_pred,
            up_class_logits=up_class_logits,
            down_class_logits=down_class_logits,
            path_class_logits=path_class_logits,
            chunk_embedding=summary,
        )


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
