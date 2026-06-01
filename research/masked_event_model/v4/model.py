from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v4.config import ModelConfig
from research.masked_event_model.v4.masking import ByteMaskBatch


HEADER_BYTES = 14
EVENT_BYTES = 16
BYTE_VOCAB_SIZE = 257
MASK_BYTE_ID = 256


@dataclass(slots=True)
class ByteMAEOutput:
    header_bit_logits: torch.Tensor
    header_indices: torch.Tensor
    event_bit_logits: torch.Tensor
    event_indices: torch.Tensor
    chunk_embedding: torch.Tensor
    token_embeddings: torch.Tensor


def transformer_encoder(layer: nn.TransformerEncoderLayer, *, num_layers: int) -> nn.TransformerEncoder:
    try:
        return nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
    except TypeError:
        return nn.TransformerEncoder(layer, num_layers=num_layers)


class CompactByteMaskedAutoencoder(nn.Module):
    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.byte_embedding = nn.Embedding(BYTE_VOCAB_SIZE, config.d_byte)
        self.header_byte_position = nn.Embedding(HEADER_BYTES, config.d_byte)
        self.event_byte_position = nn.Embedding(EVENT_BYTES, config.d_byte)
        self.event_position = nn.Embedding(self.events_per_chunk, config.d_model)
        self.token_type = nn.Embedding(2, config.d_model)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, config.d_model))

        self.header_projection = nn.Sequential(
            nn.Linear(HEADER_BYTES * config.d_byte, config.d_model),
            nn.GELU(),
            nn.LayerNorm(config.d_model),
        )
        self.event_projection = nn.Sequential(
            nn.Linear(EVENT_BYTES * config.d_byte, config.d_model),
            nn.GELU(),
            nn.LayerNorm(config.d_model),
        )
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

        self.decoder_up = nn.Linear(config.embedding_dim, config.d_model)
        decoder_layers: list[nn.Module] = []
        for _ in range(max(1, int(config.decoder_layers))):
            decoder_layers.extend(
                [
                    nn.Linear(config.d_model, config.ff_dim),
                    nn.GELU(),
                    nn.Dropout(config.dropout),
                    nn.Linear(config.ff_dim, config.d_model),
                    nn.GELU(),
                    nn.LayerNorm(config.d_model),
                ]
            )
        self.decoder = nn.Sequential(*decoder_layers)
        self.header_decode_byte_position = nn.Embedding(HEADER_BYTES, config.d_model)
        self.event_decode_byte_position = nn.Embedding(EVENT_BYTES, config.d_model)
        self.decode_type = nn.Embedding(2, config.d_model)
        self.bit_head = nn.Linear(config.d_model, 8)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.normal_(self.cls_token, std=0.02)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor, masks: ByteMaskBatch) -> ByteMAEOutput:
        encoded_tokens, token_embeddings, chunk_embedding = self._encode_tokens(header_uint8, events_uint8, masks)
        header_indices = masks.header_mask.nonzero(as_tuple=False)
        event_indices = masks.event_mask.nonzero(as_tuple=False)
        header_logits = self._decode_header(encoded_tokens, header_indices)
        event_logits = self._decode_events(encoded_tokens, event_indices)
        return ByteMAEOutput(
            header_bit_logits=header_logits,
            header_indices=header_indices,
            event_bit_logits=event_logits,
            event_indices=event_indices,
            chunk_embedding=chunk_embedding,
            token_embeddings=token_embeddings,
        )

    @torch.no_grad()
    def encode(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        _, _, chunk_embedding = self._encode_tokens(header_uint8, events_uint8, masks=None)
        return chunk_embedding

    @torch.no_grad()
    def encode_events(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        _, token_embeddings, _ = self._encode_tokens(header_uint8, events_uint8, masks=None)
        return token_embeddings[:, 2:, :]

    def _encode_tokens(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: ByteMaskBatch | None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        header_ids = header_uint8.long()
        event_ids = events_uint8.long()
        if masks is not None:
            header_ids = header_ids.masked_fill(masks.header_mask, MASK_BYTE_ID)
            event_ids = event_ids.masked_fill(masks.event_mask, MASK_BYTE_ID)

        header_positions = torch.arange(HEADER_BYTES, device=header_uint8.device)
        event_byte_positions = torch.arange(EVENT_BYTES, device=events_uint8.device)
        header_bytes = self.byte_embedding(header_ids) + self.header_byte_position(header_positions).view(1, HEADER_BYTES, -1)
        event_bytes = self.byte_embedding(event_ids) + self.event_byte_position(event_byte_positions).view(1, 1, EVENT_BYTES, -1)
        header_token = self.header_projection(header_bytes.flatten(1)).unsqueeze(1)
        event_tokens = self.event_projection(event_bytes.flatten(2))
        event_positions = torch.arange(self.events_per_chunk, device=events_uint8.device)
        event_tokens = event_tokens + self.event_position(event_positions).view(1, self.events_per_chunk, -1)
        header_token = header_token + self.token_type(torch.zeros(1, dtype=torch.long, device=header_uint8.device)).view(1, 1, -1)
        event_tokens = event_tokens + self.token_type(torch.ones(1, dtype=torch.long, device=events_uint8.device)).view(1, 1, -1)
        cls = self.cls_token.expand(header_uint8.shape[0], -1, -1)
        tokens = torch.cat([cls, header_token, event_tokens], dim=1)
        encoded = self.encoder_norm(self.encoder(tokens))
        embeddings = self.to_embedding(encoded)
        return encoded, embeddings, embeddings[:, 0, :]

    def _decode_header(self, encoded_tokens: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
        if indices.numel() == 0:
            return encoded_tokens.new_empty((0, 8))
        token = encoded_tokens[indices[:, 0], 1]
        hidden = self.decoder_up(self.to_embedding(token))
        hidden = hidden + self.header_decode_byte_position(indices[:, 1]) + self.decode_type(torch.zeros(indices.shape[0], dtype=torch.long, device=indices.device))
        return self.bit_head(self.decoder(hidden))

    def _decode_events(self, encoded_tokens: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
        if indices.numel() == 0:
            return encoded_tokens.new_empty((0, 8))
        token = encoded_tokens[indices[:, 0], indices[:, 1] + 2]
        hidden = self.decoder_up(self.to_embedding(token))
        hidden = hidden + self.event_decode_byte_position(indices[:, 2]) + self.decode_type(torch.ones(indices.shape[0], dtype=torch.long, device=indices.device))
        return self.bit_head(self.decoder(hidden))
