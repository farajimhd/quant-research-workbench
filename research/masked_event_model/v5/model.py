from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v5.config import ModelConfig
from research.masked_event_model.v5.masking import EventMaskBatch, gather_events, maybe_corrupt_header, maybe_corrupt_visible_events


HEADER_BYTES = 14
EVENT_BYTES = 16
BITS_PER_BYTE = 8


@dataclass(slots=True)
class EventMAEOutput:
    event_bit_logits: torch.Tensor
    masked_event_indices: torch.Tensor
    target_events_uint8: torch.Tensor
    chunk_embedding: torch.Tensor
    token_embeddings: torch.Tensor

    @property
    def event_bit_probs(self) -> torch.Tensor:
        return torch.sigmoid(self.event_bit_logits)


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


class CrossAttentionDecoderLayer(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.query_norm = nn.LayerNorm(config.d_model)
        self.memory_norm = nn.LayerNorm(config.d_model)
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=config.d_model,
            num_heads=config.n_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.attention_dropout = nn.Dropout(config.dropout)
        self.ffn_norm = nn.LayerNorm(config.d_model)
        self.ffn = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, config.d_model),
            nn.Dropout(config.dropout),
        )

    def forward(self, queries: torch.Tensor, memory: torch.Tensor) -> torch.Tensor:
        normalized_queries = self.query_norm(queries)
        normalized_memory = self.memory_norm(memory)
        attended, _ = self.cross_attention(
            normalized_queries,
            normalized_memory,
            normalized_memory,
            need_weights=False,
        )
        queries = queries + self.attention_dropout(attended)
        queries = queries + self.ffn(self.ffn_norm(queries))
        return queries


class EventTokenMaskedAutoencoder(nn.Module):
    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.input_representation = str(config.input_representation)
        if self.input_representation != "bit":
            raise ValueError("v5 currently supports input_representation='bit' only")

        self.header_projection = nn.Sequential(
            nn.Linear(HEADER_BYTES * BITS_PER_BYTE, config.d_model),
            nn.GELU(),
            nn.LayerNorm(config.d_model),
        )
        self.event_projection = nn.Sequential(
            nn.Linear(EVENT_BYTES * BITS_PER_BYTE, config.d_model),
            nn.GELU(),
            nn.LayerNorm(config.d_model),
        )
        self.event_position = nn.Embedding(self.events_per_chunk, config.d_model)
        self.token_type = nn.Embedding(3, config.d_model)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, config.d_model))

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.encoder = transformer_encoder(encoder_layer, num_layers=config.encoder_layers)
        self.encoder_norm = nn.LayerNorm(config.d_model)
        self.to_embedding = nn.Linear(config.d_model, config.embedding_dim)
        self.embedding_to_decoder = nn.Sequential(
            nn.LayerNorm(config.embedding_dim),
            nn.Linear(config.embedding_dim, config.d_model),
        )

        self.decoder_mask_token = nn.Parameter(torch.zeros(1, 1, config.d_model))
        self.decoder_event_position = nn.Embedding(self.events_per_chunk, config.d_model)
        self.decoder_token_type = nn.Embedding(3, config.d_model)
        self.cross_decoder = nn.ModuleList(
            CrossAttentionDecoderLayer(config) for _ in range(max(1, int(config.decoder_layers)))
        )
        self.decoder_norm = nn.LayerNorm(config.d_model)
        self.event_bit_head = nn.Linear(config.d_model, EVENT_BYTES * BITS_PER_BYTE)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.normal_(self.cls_token, std=0.02)
        nn.init.normal_(self.decoder_mask_token, std=0.02)

    def forward(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config=None,
    ) -> EventMAEOutput:
        encoded_tokens, token_embeddings, chunk_embedding, target_events = self.encode_tokens_for_training(header_uint8, events_uint8, masks, mask_config)
        decoder_memory = self.embedding_to_decoder(token_embeddings)
        event_logits = self.decode_masked_events(decoder_memory, masks)
        return EventMAEOutput(
            event_bit_logits=event_logits,
            masked_event_indices=masks.masked_event_indices,
            target_events_uint8=target_events,
            chunk_embedding=chunk_embedding,
            token_embeddings=token_embeddings,
        )

    @torch.no_grad()
    def encode(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        _, _, chunk_embedding = self._encode_tokens(header_uint8, events_uint8, visible_event_indices=None, mask_config=None, training=False)
        return chunk_embedding

    @torch.no_grad()
    def encode_events(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        _, token_embeddings, _ = self._encode_tokens(header_uint8, events_uint8, visible_event_indices=None, mask_config=None, training=False)
        return token_embeddings[:, 2:, :]

    def encode_tokens_for_training(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        target_events = gather_events(events_uint8, masks.masked_event_indices)
        encoded, embeddings, chunk_embedding = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=masks.visible_event_indices,
            mask_config=mask_config,
            training=True,
        )
        return encoded, embeddings, chunk_embedding, target_events

    def _encode_tokens(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        *,
        visible_event_indices: torch.Tensor | None,
        mask_config,
        training: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        if training and mask_config is not None:
            header_input_uint8 = maybe_corrupt_header(header_uint8, mask_config)
        else:
            header_input_uint8 = header_uint8
        if visible_event_indices is None:
            event_input_uint8 = events_uint8
            event_indices = torch.arange(self.events_per_chunk, device=events_uint8.device).view(1, -1).expand(events_uint8.shape[0], -1)
        else:
            event_input_uint8 = gather_events(events_uint8, visible_event_indices)
            if training and mask_config is not None:
                event_input_uint8 = maybe_corrupt_visible_events(event_input_uint8, mask_config)
            event_indices = visible_event_indices

        header_input = uint8_to_pm1_bits(header_input_uint8).flatten(1)
        event_input = uint8_to_pm1_bits(event_input_uint8).flatten(2)
        header_token = self.header_projection(header_input).unsqueeze(1)
        event_tokens = self.event_projection(event_input)
        event_tokens = event_tokens + self.event_position(event_indices)
        header_token = header_token + self.token_type(torch.ones(1, dtype=torch.long, device=header_uint8.device)).view(1, 1, -1)
        event_tokens = event_tokens + self.token_type(torch.full((1,), 2, dtype=torch.long, device=events_uint8.device)).view(1, 1, -1)
        cls = self.cls_token.expand(header_uint8.shape[0], -1, -1) + self.token_type(torch.zeros(1, dtype=torch.long, device=header_uint8.device)).view(1, 1, -1)
        tokens = torch.cat([cls, header_token, event_tokens], dim=1)
        encoded = self.encoder_norm(self.encoder(tokens))
        embeddings = self.to_embedding(encoded)
        return encoded, embeddings, embeddings[:, 0, :]

    def decode_masked_events(self, decoder_memory: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        batch_size = decoder_memory.shape[0]
        queries = self.decoder_mask_token.expand(batch_size, masks.masked_count, -1).clone()
        queries = queries + self.decoder_event_position(masks.masked_event_indices)
        queries = queries + self.decoder_token_type(
            torch.full((1,), 2, dtype=torch.long, device=decoder_memory.device)
        ).view(1, 1, -1)

        for layer in self.cross_decoder:
            queries = layer(queries, decoder_memory)
        decoded = self.decoder_norm(queries)
        return self.event_bit_head(decoded).view(batch_size, masks.masked_count, EVENT_BYTES, BITS_PER_BYTE)


def uint8_to_pm1_bits(values: torch.Tensor) -> torch.Tensor:
    shifts = torch.arange(BITS_PER_BYTE, device=values.device, dtype=torch.long)
    bits = ((values.long().unsqueeze(-1) >> shifts) & 1).to(torch.float32)
    return bits.mul(2.0).sub(1.0)
