from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v6.config import ModelConfig
from research.masked_event_model.v6.masking import EventMaskBatch, gather_events, maybe_corrupt_header, maybe_corrupt_visible_events


HEADER_BYTES = 14
EVENT_BYTES = 16
BITS_PER_BYTE = 8


@dataclass(slots=True)
class EventMAEOutput:
    """Training output for the masked event objective.

    `chunk_embedding` is the representation we intend to reuse downstream.
    The decoder must reconstruct masked event bytes from this embedding, so the
    loss backpropagates through the exported representation instead of through a
    side path that will not exist at inference time.
    """

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


def single_role_vector(role_embedding: nn.Embedding, *, device: torch.device) -> torch.Tensor:
    """Return the single learned role vector through the embedding module.

    Calling the module instead of reading `.weight` directly keeps diagram and
    summary tools aware of which semantic role embedding is being used.
    """

    role_id = torch.zeros((1,), device=device, dtype=torch.long)
    return role_embedding(role_id).view(1, 1, -1)


class UInt8BytesToSignedBitFeatures(nn.Module):
    """Convert packed bytes into -1/+1 bit features that the linear layers can read.

    The sample cache stores each event compactly as uint8 bytes. The model should
    not treat a byte value like 127 as an ordinal market feature; it should see
    the eight binary decisions inside that byte. Returning -1/+1 rather than
    0/1 keeps the input centered for the first projection layer.
    """

    def __init__(self, *, flatten_from_byte_axis: bool) -> None:
        super().__init__()
        self.flatten_from_byte_axis = bool(flatten_from_byte_axis)

    def forward(self, values_uint8: torch.Tensor) -> torch.Tensor:
        shifts = torch.arange(BITS_PER_BYTE, device=values_uint8.device, dtype=torch.long)
        bits = ((values_uint8.long().unsqueeze(-1) >> shifts) & 1).to(torch.float32)
        signed_bits = bits.mul(2.0).sub(1.0)
        if self.flatten_from_byte_axis:
            return signed_bits.flatten(1)
        return signed_bits.flatten(2)


class VisibleEventTokenSelector(nn.Module):
    """Gather only unmasked events before the encoder so masked events use no encoder compute."""

    def forward(self, events_uint8: torch.Tensor, visible_event_indices: torch.Tensor | None) -> tuple[torch.Tensor, torch.Tensor]:
        if visible_event_indices is None:
            event_count = int(events_uint8.shape[1])
            event_indices = torch.arange(event_count, device=events_uint8.device).view(1, -1).expand(events_uint8.shape[0], -1)
            return events_uint8, event_indices
        return gather_events(events_uint8, visible_event_indices), visible_event_indices


class HeaderRoleEmbedding(nn.Embedding):
    """Learned role vector that marks the single header token."""


class VisibleEventPositionEmbedding(nn.Embedding):
    """Position embedding for visible event tokens entering the encoder."""


class EventRoleEmbedding(nn.Embedding):
    """Learned role vector shared by event tokens before the encoder."""


class ChunkClsRoleEmbedding(nn.Embedding):
    """Learned role vector added to the chunk-level CLS token."""


class MaskedEventPositionEmbedding(nn.Embedding):
    """Position embedding for decoder queries at masked event locations."""


class MaskedEventQueryRoleEmbedding(nn.Embedding):
    """Learned role vector shared by masked-event decoder queries."""


class LearnedChunkClsToken(nn.Module):
    """Learned CLS token whose encoded state becomes the chunk embedding."""

    def __init__(self, d_model: int) -> None:
        super().__init__()
        self.token = nn.Parameter(torch.zeros(1, 1, d_model))

    def reset_parameters(self) -> None:
        nn.init.normal_(self.token, std=0.02)

    def forward(self, batch_size: int) -> torch.Tensor:
        return self.token.expand(batch_size, -1, -1)


class LearnedMaskedEventQueryToken(nn.Module):
    """Learned decoder query template used for every masked event."""

    def __init__(self, d_model: int) -> None:
        super().__init__()
        self.token = nn.Parameter(torch.zeros(1, 1, d_model))

    def reset_parameters(self) -> None:
        nn.init.normal_(self.token, std=0.02)

    def forward(self, batch_size: int, masked_count: int) -> torch.Tensor:
        return self.token.expand(batch_size, masked_count, -1).clone()


class HeaderTokenEncoder(nn.Module):
    """Project the 14-byte chunk header into one transformer token."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.header_bytes_to_signed_bits = UInt8BytesToSignedBitFeatures(flatten_from_byte_axis=True)
        self.header_bits_to_model_token = nn.Sequential(
            OrderedDict(
                [
                    ("header_112_bits_to_model_width", nn.Linear(HEADER_BYTES * BITS_PER_BYTE, config.d_model)),
                    ("header_projection_gelu", nn.GELU()),
                    ("header_projection_layer_norm", nn.LayerNorm(config.d_model)),
                ]
            )
        )
        self.header_role_embedding = HeaderRoleEmbedding(1, config.d_model)

    def forward(self, header_uint8: torch.Tensor) -> torch.Tensor:
        header_bits = self.header_bytes_to_signed_bits(header_uint8)
        header_token = self.header_bits_to_model_token(header_bits).unsqueeze(1)
        return header_token + single_role_vector(self.header_role_embedding, device=header_uint8.device)


class EventTokenEncoder(nn.Module):
    """Project visible 16-byte event records into position-aware event tokens."""

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.event_bytes_to_signed_bits = UInt8BytesToSignedBitFeatures(flatten_from_byte_axis=False)
        self.event_bits_to_model_tokens = nn.Sequential(
            OrderedDict(
                [
                    ("event_128_bits_to_model_width", nn.Linear(EVENT_BYTES * BITS_PER_BYTE, config.d_model)),
                    ("event_projection_gelu", nn.GELU()),
                    ("event_projection_layer_norm", nn.LayerNorm(config.d_model)),
                ]
            )
        )
        self.event_position_embedding_for_encoder = VisibleEventPositionEmbedding(events_per_chunk, config.d_model)
        self.event_role_embedding = EventRoleEmbedding(1, config.d_model)

    def forward(self, visible_events_uint8: torch.Tensor, visible_event_indices: torch.Tensor) -> torch.Tensor:
        event_bits = self.event_bytes_to_signed_bits(visible_events_uint8)
        event_tokens = self.event_bits_to_model_tokens(event_bits)
        event_tokens = event_tokens + self.event_position_embedding_for_encoder(visible_event_indices)
        return event_tokens + single_role_vector(self.event_role_embedding, device=visible_events_uint8.device)


class EncoderSequenceBuilder(nn.Module):
    """Prepend the learned CLS token and concatenate header plus visible event tokens."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.learned_chunk_cls_token = LearnedChunkClsToken(config.d_model)
        self.cls_role_embedding = ChunkClsRoleEmbedding(1, config.d_model)

    def reset_parameters(self) -> None:
        self.learned_chunk_cls_token.reset_parameters()

    def forward(self, header_token: torch.Tensor, event_tokens: torch.Tensor) -> torch.Tensor:
        batch_size = int(header_token.shape[0])
        cls_token = self.learned_chunk_cls_token(batch_size)
        cls_token = cls_token + single_role_vector(self.cls_role_embedding, device=header_token.device)
        return torch.cat([cls_token, header_token, event_tokens], dim=1)

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
        old_key = prefix + "learned_chunk_cls_token"
        new_key = prefix + "learned_chunk_cls_token.token"
        if old_key in state_dict and new_key not in state_dict:
            state_dict[new_key] = state_dict.pop(old_key)
        super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs)


class ChunkEmbeddingBottleneck(nn.Module):
    """Create the exported embedding from all encoded tokens.

    Earlier v6 used only the projected CLS token as the chunk embedding. That
    made the production embedding depend on one token's summary behavior. This
    bottleneck now projects every encoded token to embedding space and averages
    all projected tokens, so header and event tokens contribute directly to the
    exported representation while the output shape remains `[B, embedding_dim]`.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.encoder_token_to_embedding_space = nn.Linear(config.d_model, config.embedding_dim)
        self.all_projected_tokens_mean_pool = nn.AdaptiveAvgPool1d(1)

    def forward(self, encoded_tokens: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        token_embeddings = self.encoder_token_to_embedding_space(encoded_tokens)
        # AdaptiveAvgPool1d expects channels first. The transpose is only for
        # pooling; `token_embeddings` keeps its original `[B, T, embedding_dim]`
        # shape for inspection and downstream per-event experiments.
        chunk_embedding = self.all_projected_tokens_mean_pool(token_embeddings.transpose(1, 2)).squeeze(-1)
        return token_embeddings, chunk_embedding


class ChunkEmbeddingToDecoderMemory(nn.Module):
    """Expand the single exported chunk embedding into one decoder memory token."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.chunk_embedding_layer_norm = nn.LayerNorm(config.embedding_dim)
        self.chunk_embedding_to_decoder_width = nn.Linear(config.embedding_dim, config.d_model)

    def forward(self, chunk_embedding: torch.Tensor) -> torch.Tensor:
        normalized_embedding = self.chunk_embedding_layer_norm(chunk_embedding)
        decoder_memory_token = self.chunk_embedding_to_decoder_width(normalized_embedding)
        return decoder_memory_token.unsqueeze(1)


class MaskedEventQueryBuilder(nn.Module):
    """Build one learned decoder query for each event removed from the encoder."""

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.learned_masked_event_query_token = LearnedMaskedEventQueryToken(config.d_model)
        self.masked_event_position_embedding_for_decoder = MaskedEventPositionEmbedding(events_per_chunk, config.d_model)
        self.masked_event_query_role_embedding = MaskedEventQueryRoleEmbedding(1, config.d_model)

    def reset_parameters(self) -> None:
        self.learned_masked_event_query_token.reset_parameters()

    def forward(self, masked_event_indices: torch.Tensor) -> torch.Tensor:
        batch_size = int(masked_event_indices.shape[0])
        masked_count = int(masked_event_indices.shape[1])
        queries = self.learned_masked_event_query_token(batch_size, masked_count)
        queries = queries + self.masked_event_position_embedding_for_decoder(masked_event_indices)
        return queries + single_role_vector(self.masked_event_query_role_embedding, device=masked_event_indices.device)

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
        old_key = prefix + "learned_masked_event_query_token"
        new_key = prefix + "learned_masked_event_query_token.token"
        if old_key in state_dict and new_key not in state_dict:
            state_dict[new_key] = state_dict.pop(old_key)
        super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs)


class MaskedQueryCrossAttentionDecoderLayer(nn.Module):
    """Let masked-event queries read the chunk embedding memory without seeing target bytes."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.masked_query_layer_norm_before_cross_attention = nn.LayerNorm(config.d_model)
        self.chunk_memory_layer_norm_before_cross_attention = nn.LayerNorm(config.d_model)
        self.masked_query_to_chunk_memory_cross_attention = nn.MultiheadAttention(
            embed_dim=config.d_model,
            num_heads=config.n_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.cross_attention_residual_dropout = nn.Dropout(config.dropout)
        self.masked_query_layer_norm_before_ffn = nn.LayerNorm(config.d_model)
        self.masked_query_feed_forward = nn.Sequential(
            OrderedDict(
                [
                    ("masked_query_expand_to_ffn_width", nn.Linear(config.d_model, config.ff_dim)),
                    ("masked_query_ffn_gelu", nn.GELU()),
                    ("masked_query_ffn_dropout_after_gelu", nn.Dropout(config.dropout)),
                    ("masked_query_contract_to_model_width", nn.Linear(config.ff_dim, config.d_model)),
                    ("masked_query_ffn_output_dropout", nn.Dropout(config.dropout)),
                ]
            )
        )

    def forward(self, masked_event_queries: torch.Tensor, chunk_embedding_memory: torch.Tensor) -> torch.Tensor:
        normalized_queries = self.masked_query_layer_norm_before_cross_attention(masked_event_queries)
        normalized_memory = self.chunk_memory_layer_norm_before_cross_attention(chunk_embedding_memory)
        attended_queries, _ = self.masked_query_to_chunk_memory_cross_attention(
            normalized_queries,
            normalized_memory,
            normalized_memory,
            need_weights=False,
        )
        masked_event_queries = masked_event_queries + self.cross_attention_residual_dropout(attended_queries)
        masked_event_queries = masked_event_queries + self.masked_query_feed_forward(
            self.masked_query_layer_norm_before_ffn(masked_event_queries)
        )
        return masked_event_queries


class MaskedEventBitPredictionHead(nn.Module):
    """Convert decoder query states into 16 reconstructed bytes represented as 8 logits each."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.masked_query_final_layer_norm = nn.LayerNorm(config.d_model)
        self.masked_query_to_16x8_bit_logits = nn.Linear(config.d_model, EVENT_BYTES * BITS_PER_BYTE)

    def forward(self, decoded_masked_event_queries: torch.Tensor) -> torch.Tensor:
        normalized_queries = self.masked_query_final_layer_norm(decoded_masked_event_queries)
        logits = self.masked_query_to_16x8_bit_logits(normalized_queries)
        return logits.view(logits.shape[0], logits.shape[1], EVENT_BYTES, BITS_PER_BYTE)


class EventTokenMaskedAutoencoder(nn.Module):
    """Masked autoencoder over compact market-event chunks.

    The encoder receives the chunk header plus only the visible event records.
    Masked event records are removed before the transformer, which saves encoder
    compute and mirrors MAE-style training. The decoder then receives exactly one
    memory token derived from `chunk_embedding`; this prevents the decoder from
    reconstructing events by reading high-dimensional encoder states directly.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.input_representation = str(config.input_representation)
        if self.input_representation != "bit":
            raise ValueError("v6 currently supports input_representation='bit' only")

        self.header_token_encoder = HeaderTokenEncoder(config)
        self.visible_event_token_selector = VisibleEventTokenSelector()
        self.visible_event_token_encoder = EventTokenEncoder(events_per_chunk=self.events_per_chunk, config=config)
        self.encoder_sequence_builder = EncoderSequenceBuilder(config)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.visible_context_transformer_encoder = transformer_encoder(encoder_layer, num_layers=config.encoder_layers)
        self.encoded_token_output_layer_norm = nn.LayerNorm(config.d_model)
        self.chunk_embedding_bottleneck = ChunkEmbeddingBottleneck(config)
        self.chunk_embedding_to_decoder_memory = ChunkEmbeddingToDecoderMemory(config)

        self.masked_event_query_builder = MaskedEventQueryBuilder(events_per_chunk=self.events_per_chunk, config=config)
        self.masked_query_cross_attention_decoder = nn.ModuleList(
            MaskedQueryCrossAttentionDecoderLayer(config) for _ in range(max(1, int(config.decoder_layers)))
        )
        self.masked_event_bit_prediction_head = MaskedEventBitPredictionHead(config)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        self.encoder_sequence_builder.reset_parameters()
        self.masked_event_query_builder.reset_parameters()

    def forward(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config=None,
    ) -> EventMAEOutput:
        # The training path has three explicit phases:
        # 1. encode visible context,
        # 2. compress the CLS token into the exported chunk embedding,
        # 3. reconstruct only the event records that were removed from context.
        encoded_tokens, token_embeddings, chunk_embedding, target_events = self.encode_tokens_for_training(
            header_uint8, events_uint8, masks, mask_config
        )
        decoder_memory = self.chunk_embedding_to_decoder_memory(chunk_embedding)
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
        """Production embedding path: no masks, no decoder, no reconstruction work."""
        _, _, chunk_embedding = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return chunk_embedding

    @torch.no_grad()
    def encode_events(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        """Return per-event embeddings for diagnostics or downstream sequence experiments."""
        _, token_embeddings, _ = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return token_embeddings[:, 2:, :]

    def encode_tokens_for_training(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        target_events = gather_events(events_uint8, masks.masked_event_indices)
        encoded_tokens, token_embeddings, chunk_embedding = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=masks.visible_event_indices,
            mask_config=mask_config,
            training=True,
        )
        return encoded_tokens, token_embeddings, chunk_embedding, target_events

    def _encode_tokens(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        *,
        visible_event_indices: torch.Tensor | None,
        mask_config,
        training: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        # Header/event corruption is deliberately weaker than event removal. It
        # regularizes the encoder against missing/corrupt bits without changing
        # the sequence length semantics learned by the transformer.
        if training and mask_config is not None:
            header_input_uint8 = maybe_corrupt_header(header_uint8, mask_config)
        else:
            header_input_uint8 = header_uint8

        selected_events_uint8, selected_event_indices = self.visible_event_token_selector(events_uint8, visible_event_indices)
        if training and visible_event_indices is not None and mask_config is not None:
            selected_events_uint8 = maybe_corrupt_visible_events(selected_events_uint8, mask_config)

        header_token = self.header_token_encoder(header_input_uint8)
        visible_event_tokens = self.visible_event_token_encoder(selected_events_uint8, selected_event_indices)
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        token_embeddings, chunk_embedding = self.chunk_embedding_bottleneck(encoded_tokens)
        return encoded_tokens, token_embeddings, chunk_embedding

    def decode_masked_events(self, decoder_memory: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        # The decoder queries contain position only, not masked event bytes. Any
        # successful reconstruction therefore has to come from the chunk
        # embedding memory and the learned market-structure priors.
        masked_event_queries = self.masked_event_query_builder(masks.masked_event_indices)
        for decoder_layer in self.masked_query_cross_attention_decoder:
            masked_event_queries = decoder_layer(masked_event_queries, decoder_memory)
        return self.masked_event_bit_prediction_head(masked_event_queries)
