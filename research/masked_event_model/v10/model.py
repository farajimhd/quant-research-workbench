from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v10.config import ModelConfig
from research.masked_event_model.v10.masking import EventMaskBatch, gather_events, maybe_corrupt_header, maybe_corrupt_visible_events


HEADER_BYTES = 14
EVENT_BYTES = 16
BITS_PER_BYTE = 8

# Shape shorthand used by the forward-path comments:
# B = batch size, E = total events per chunk, V = visible events, M = masked events,
# T = encoder tokens (CLS + header + visible events), D = model width,
# Z = intermediate embedding width, F = final per-token embedding features.


@dataclass(slots=True)
class EventMAEOutput:
    """Training output for the masked event objective.

    `chunk_embedding` is the representation we intend to reuse downstream.
    The decoder must reconstruct masked event bytes from this embedding, so the
    loss backpropagates through the exported representation instead of through a
    side path that will not exist at inference time.
    """

    # Shape: [B, M, 16, 8]. Raw decoder logits for each reconstructed event bit.
    event_bit_logits: torch.Tensor
    # Shape: [B, M]. Original event positions selected as decoder targets.
    masked_event_indices: torch.Tensor
    # Shape: [B, M, 16]. Original uint8 event bytes gathered at masked positions.
    target_events_uint8: torch.Tensor
    # Shape: [B, T, F]. Exportable encoder representation for downstream models.
    chunk_embedding: torch.Tensor
    # Scalar E. Total event records available in each compact sample.
    event_count: int
    # Scalar V. Number of event records sent through the encoder.
    visible_event_count: int
    # Scalar. Mask ratio requested by the active masking policy.
    requested_mask_ratio: float
    # Scalar. Effective `M / E` ratio after integer event-count rounding.
    actual_mask_ratio: float
    # Scalar. Numeric identifier for the sampled masking policy.
    mask_policy_id: int

    @property
    def event_bit_probs(self) -> torch.Tensor:
        # Input shape: [B, M, 16, 8]. Output shape: [B, M, 16, 8].
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

    # Input shape: scalar role id. Output shape: [1].
    role_id = torch.zeros((1,), device=device, dtype=torch.long)
    # Input shape: [1]. Output shape: [1, 1, D] for broadcast over batch/tokens.
    return role_embedding(role_id).view(1, 1, -1)


def build_signed_bit_lookup() -> torch.Tensor:
    """Create a `[256, 8]` little-endian lookup for byte inputs.

    The forward path indexes this buffer instead of shifting every input byte
    into eight bits on every step. The table is tiny, moves with the module, and
    keeps byte unpacking deterministic across training and inference.
    """

    # Input shape: [256]. Output shape after view: [256, 1].
    values = torch.arange(256, dtype=torch.long).view(256, 1)
    # Input shape: [8]. Output shape after view: [1, 8].
    shifts = torch.arange(BITS_PER_BYTE, dtype=torch.long).view(1, BITS_PER_BYTE)
    # Input shapes: values [256, 1], shifts [1, 8]. Output shape: [256, 8].
    bits = ((values >> shifts) & 1).to(torch.float32)
    # Input shape: [256, 8] in {0, 1}. Output shape: [256, 8] in {-1, +1}.
    return bits.mul(2.0).sub(1.0)


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
        self.register_buffer("signed_bit_lookup", build_signed_bit_lookup(), persistent=False)

    def forward(self, values_uint8: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, bytes] or [B, events, bytes]. Output shape: input + trailing [8].
        signed_bits = self.signed_bit_lookup[values_uint8.long()]
        if self.flatten_from_byte_axis:
            # Input shape: [B, bytes, 8]. Output shape: [B, bytes * 8].
            return signed_bits.flatten(1)
        # Input shape: [B, events, bytes, 8]. Output shape: [B, events, bytes * 8].
        return signed_bits.flatten(2)


class VisibleEventTokenSelector(nn.Module):
    """Gather only unmasked events before the encoder so masked events use no encoder compute."""

    def forward(self, events_uint8: torch.Tensor, visible_event_indices: torch.Tensor | None) -> tuple[torch.Tensor, torch.Tensor]:
        if visible_event_indices is None:
            event_count = int(events_uint8.shape[1])
            # Input shape: E. Output shape after expand: [B, E].
            event_indices = torch.arange(event_count, device=events_uint8.device).view(1, -1).expand(events_uint8.shape[0], -1)
            # Input events shape: [B, E, 16]. Output events/indices shapes: [B, E, 16], [B, E].
            return events_uint8, event_indices
        # Input shapes: events [B, E, 16], indices [B, V]. Output shapes: [B, V, 16], [B, V].
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
        # Input shape: learned [1, 1, D]. Output shape: [B, 1, D].
        return self.token.expand(batch_size, -1, -1)


class LearnedMaskedEventQueryToken(nn.Module):
    """Learned decoder query template used for every masked event."""

    def __init__(self, d_model: int) -> None:
        super().__init__()
        self.token = nn.Parameter(torch.zeros(1, 1, d_model))

    def reset_parameters(self) -> None:
        nn.init.normal_(self.token, std=0.02)

    def forward(self, batch_size: int, masked_count: int) -> torch.Tensor:
        # Input shape: learned [1, 1, D]. Output shape: [B, M, D].
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
        # Input shape: [B, 14]. Output shape: [B, 112].
        header_bits = self.header_bytes_to_signed_bits(header_uint8)
        # Input shape: [B, 112]. Output shape after unsqueeze: [B, 1, D].
        header_token = self.header_bits_to_model_token(header_bits).unsqueeze(1)
        # Input shapes: header token [B, 1, D], role [1, 1, D]. Output shape: [B, 1, D].
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
        # Input shape: [B, V, 16]. Output shape: [B, V, 128].
        event_bits = self.event_bytes_to_signed_bits(visible_events_uint8)
        # Input shape: [B, V, 128]. Output shape: [B, V, D].
        event_tokens = self.event_bits_to_model_tokens(event_bits)
        # Input shapes: tokens [B, V, D], positions [B, V]. Output shape: [B, V, D].
        event_tokens = event_tokens + self.event_position_embedding_for_encoder(visible_event_indices)
        # Input shapes: tokens [B, V, D], role [1, 1, D]. Output shape: [B, V, D].
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
        # Input shape: batch size B. Output shape: [B, 1, D].
        cls_token = self.learned_chunk_cls_token(batch_size)
        # Input shapes: CLS [B, 1, D], role [1, 1, D]. Output shape: [B, 1, D].
        cls_token = cls_token + single_role_vector(self.cls_role_embedding, device=header_token.device)
        # Input shapes: CLS [B, 1, D], header [B, 1, D], events [B, V, D]. Output shape: [B, 2 + V, D].
        return torch.cat([cls_token, header_token, event_tokens], dim=1)

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
        old_key = prefix + "learned_chunk_cls_token"
        new_key = prefix + "learned_chunk_cls_token.token"
        if old_key in state_dict and new_key not in state_dict:
            state_dict[new_key] = state_dict.pop(old_key)
        super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs)


class ChunkEmbeddingBottleneck(nn.Module):
    """Create the exported per-token embedding from encoded context tokens.

    v10 keeps the event/token axis instead of mean-pooling it away. The encoded
    transformer width is projected directly to `event_embedding_features`; there
    is no intermediate `embedding_dim` projection here because two adjacent
    linear layers without an activation collapse to one equivalent linear map.
    The decoder bridge flattens this compact token sequence into one v9-like
    memory token, while downstream users can still reuse the tokenwise
    representation directly.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.encoder_token_to_event_features = nn.Linear(config.d_model, config.event_embedding_features)
        self.chunk_embedding_output = ChunkEmbeddingOutput()

    def project_encoded_tokens(self, encoded_tokens: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, T, D]. Output shape: [B, T, F].
        return self.encoder_token_to_event_features(encoded_tokens)

    def forward(self, encoded_tokens: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, T, D]. Output shape: [B, T, F].
        event_feature_embeddings = self.project_encoded_tokens(encoded_tokens)
        # Input shape: [B, T, F]. Output shape: [B, T, F].
        return self.chunk_embedding_output(event_feature_embeddings)

    def event_only(self, encoded_tokens: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, 2 + E, D]. Output shape: [B, E, F].
        return self.forward(encoded_tokens)[:, 2:, :]


class ChunkEmbeddingOutput(nn.Identity):
    """Named terminal layer for the reusable chunk embedding."""


class ChunkEmbeddingToDecoderMemory(nn.Module):
    """Flatten compact token embeddings into one v9-like decoder memory token."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.decoder_bottleneck_tokens = int(config.decoder_bottleneck_tokens)
        self.event_embedding_features = int(config.event_embedding_features)
        self.flattened_bottleneck_width = self.decoder_bottleneck_tokens * self.event_embedding_features
        self.chunk_embedding_layer_norm = nn.LayerNorm(self.flattened_bottleneck_width)
        self.chunk_embedding_to_decoder_width = nn.Linear(self.flattened_bottleneck_width, config.d_model)

    def forward(self, chunk_embedding: torch.Tensor) -> torch.Tensor:
        if int(chunk_embedding.shape[1]) != self.decoder_bottleneck_tokens:
            raise ValueError(
                "v10 decoder bridge expects "
                f"{self.decoder_bottleneck_tokens} bottleneck tokens, got {int(chunk_embedding.shape[1])}. "
                "Update ModelConfig.decoder_bottleneck_tokens when changing the event mask ratio."
            )
        if int(chunk_embedding.shape[2]) != self.event_embedding_features:
            raise ValueError(
                "v10 decoder bridge expects "
                f"{self.event_embedding_features} features per token, got {int(chunk_embedding.shape[2])}."
            )
        # Input shape: [B, T, F]. Output shape: [B, T * F].
        flattened_embedding = chunk_embedding.reshape(chunk_embedding.shape[0], self.flattened_bottleneck_width)
        # Input shape: [B, T * F]. Output shape: [B, T * F].
        normalized_embedding = self.chunk_embedding_layer_norm(flattened_embedding)
        # Input shape: [B, T * F]. Output shape: [B, D].
        decoder_memory_token = self.chunk_embedding_to_decoder_width(normalized_embedding)
        # Input shape: [B, D]. Output shape: [B, 1, D].
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
        # Input shape: batch B and masked count M. Output shape: [B, M, D].
        queries = self.learned_masked_event_query_token(batch_size, masked_count)
        # Input shapes: queries [B, M, D], masked positions [B, M]. Output shape: [B, M, D].
        queries = queries + self.masked_event_position_embedding_for_decoder(masked_event_indices)
        # Input shapes: queries [B, M, D], role [1, 1, D]. Output shape: [B, M, D].
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
        # Input shape: [B, M, D]. Output shape: [B, M, D].
        normalized_queries = self.masked_query_layer_norm_before_cross_attention(masked_event_queries)
        # Input shape: [B, T, D]. Output shape: [B, T, D].
        normalized_memory = self.chunk_memory_layer_norm_before_cross_attention(chunk_embedding_memory)
        # Input shapes: query [B, M, D], key/value [B, T, D]. Output shape: [B, M, D].
        attended_queries, _ = self.masked_query_to_chunk_memory_cross_attention(
            normalized_queries,
            normalized_memory,
            normalized_memory,
            need_weights=False,
        )
        # Input shapes: residual [B, M, D], attention [B, M, D]. Output shape: [B, M, D].
        masked_event_queries = masked_event_queries + self.cross_attention_residual_dropout(attended_queries)
        # Input shape: [B, M, D]. Output shape after FFN residual: [B, M, D].
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
        # Input shape: [B, M, D]. Output shape: [B, M, D].
        normalized_queries = self.masked_query_final_layer_norm(decoded_masked_event_queries)
        # Input shape: [B, M, D]. Output shape: [B, M, 128].
        logits = self.masked_query_to_16x8_bit_logits(normalized_queries)
        # Input shape: [B, M, 128]. Output shape: [B, M, 16, 8].
        return logits.view(logits.shape[0], logits.shape[1], EVENT_BYTES, BITS_PER_BYTE)


class EventChunkEncoder(nn.Module):
    """Standalone encoder that ends at reusable `[B, 2 + E, F]` token embeddings.

    This module contains only the pieces that should survive after MAE-style
    pretraining: header/event tokenization, visible-context transformer
    encoding, and the chunk embedding bottleneck. It deliberately has no
    decoder, no masked-event query tokens, and no reconstruction head, so it can
    be exported and loaded by downstream models without pulling in pretraining
    machinery.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.visible_event_token_selector = VisibleEventTokenSelector()
        self.header_token_encoder = HeaderTokenEncoder(config)
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
        self.reset_parameters()

    def reset_parameters(self) -> None:
        self.encoder_sequence_builder.reset_parameters()

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        """Production path: encode all records and return CLS + header + events."""

        # Input shapes: header [B, 14], events [B, E, 16]. Output shapes: encoded [B, 2 + E, D], embedding [B, 2 + E, F].
        encoded_tokens, _ = self.encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return self.chunk_embedding_bottleneck(encoded_tokens)

    def encode_tokens(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        *,
        visible_event_indices: torch.Tensor | None,
        mask_config,
        training: bool,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if training and mask_config is not None:
            # Input shape: [B, 14]. Output shape: [B, 14].
            header_input_uint8 = maybe_corrupt_header(header_uint8, mask_config)
        else:
            # Input shape: [B, 14]. Output shape: [B, 14].
            header_input_uint8 = header_uint8

        # Input shapes: events [B, E, 16], optional indices [B, V]. Output shapes: [B, V or E, 16], [B, V or E].
        selected_events_uint8, selected_event_indices = self.visible_event_token_selector(events_uint8, visible_event_indices)
        if training and visible_event_indices is not None and mask_config is not None:
            # Input shape: [B, V, 16]. Output shape: [B, V, 16].
            selected_events_uint8 = maybe_corrupt_visible_events(selected_events_uint8, mask_config)

        # Input shape: [B, 14]. Output shape: [B, 1, D].
        header_token = self.header_token_encoder(header_input_uint8)
        # Input shapes: events [B, V or E, 16], indices [B, V or E]. Output shape: [B, V or E, D].
        visible_event_tokens = self.visible_event_token_encoder(selected_events_uint8, selected_event_indices)
        # Input shapes: header [B, 1, D], events [B, V or E, D]. Output shape: [B, 2 + V or 2 + E, D].
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        # Input shape: [B, T, D]. Output shape: [B, T, D].
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        # Input shape: [B, T, D]. Output shape: [B, T, F].
        chunk_embedding = self.chunk_embedding_bottleneck(encoded_tokens)
        return encoded_tokens, chunk_embedding


ENCODER_MODULE_NAMES = (
    "visible_event_token_selector",
    "header_token_encoder",
    "visible_event_token_encoder",
    "encoder_sequence_builder",
    "visible_context_transformer_encoder",
    "encoded_token_output_layer_norm",
    "chunk_embedding_bottleneck",
)


class EventTokenMaskedAutoencoder(nn.Module):
    """Masked autoencoder over compact market-event chunks.

    The encoder receives the chunk header plus only the visible event records.
    Masked event records are removed before the transformer, which saves encoder
    compute and mirrors MAE-style training. The decoder then receives exactly one
    compact per-token memory derived from `chunk_embedding`; this prevents the
    decoder from reconstructing events by reading high-dimensional encoder
    states directly.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.input_representation = str(config.input_representation)
        if self.input_representation != "bit":
            raise ValueError("v10 currently supports input_representation='bit' only")

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

    def encoder_state_dict(self) -> dict[str, torch.Tensor]:
        """Return a standalone encoder state dict with decoder weights excluded."""

        state: dict[str, torch.Tensor] = {}
        for module_name in ENCODER_MODULE_NAMES:
            module = getattr(self, module_name)
            for key, value in module.state_dict().items():
                state[f"{module_name}.{key}"] = value.detach().clone()
        return state

    def build_encoder_model(self) -> EventChunkEncoder:
        """Create an independent encoder module initialized from this pretrained model."""

        encoder = EventChunkEncoder(events_per_chunk=self.events_per_chunk, config=self.config)
        encoder.load_state_dict(self.encoder_state_dict(), strict=True)
        first_parameter = next(self.parameters(), None)
        if first_parameter is not None:
            encoder = encoder.to(device=first_parameter.device, dtype=first_parameter.dtype)
        return encoder

    def forward(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config=None,
    ) -> EventMAEOutput:
        # The training path has three explicit phases:
        # 1. encode visible context,
        # 2. pool all encoded tokens into the exported chunk embedding,
        # 3. reconstruct only the event records that were removed from context.
        # Input shapes: header [B, 14], events [B, E, 16], masks [B, M]/[B, V]. Output shapes: [B, T, D], [B, T, F], [B, M, 16].
        encoded_tokens, chunk_embedding, target_events = self.encode_tokens_for_training(
            header_uint8, events_uint8, masks, mask_config
        )
        # Input shape: [B, T, F]. Output shape: [B, M, 16, 8].
        event_logits = self.decode_from_chunk_embedding(chunk_embedding, masks)
        return EventMAEOutput(
            event_bit_logits=event_logits,
            masked_event_indices=masks.masked_event_indices,
            target_events_uint8=target_events,
            chunk_embedding=chunk_embedding,
            event_count=masks.event_count,
            visible_event_count=masks.visible_count,
            requested_mask_ratio=masks.requested_mask_ratio,
            actual_mask_ratio=masks.actual_mask_ratio,
            mask_policy_id=masks.mask_policy_id,
        )

    @torch.no_grad()
    def encode(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        """Production embedding path returning CLS + header + event embeddings.

        Downstream models can keep the richer non-event summary tokens when
        useful, while `encode_events()` remains available for strictly
        event-aligned tensors.
        """
        # Input shapes: header [B, 14], events [B, E, 16]. Output shapes: encoded [B, 2 + E, D], embedding [B, 2 + E, F].
        encoded_tokens, _ = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return self.chunk_embedding_bottleneck(encoded_tokens)

    def decode_from_chunk_embedding(self, chunk_embedding: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        """Reconstruct masked events, optionally forcing the decoder path to FP32.

        The encoder remains inside the training loop's autocast context, so it
        keeps the BF16 speed benefit. The decoder bridge and cross-attention
        operate on the compact `[B, T, F]` bottleneck representation; replay
        debugging showed that BF16 backward through this bridge can create very
        large finite gradients even when the same batch is stable in FP32.
        """

        if self.config.decoder_force_fp32 and chunk_embedding.is_cuda:
            with torch.amp.autocast("cuda", enabled=False):
                # Input shape: [B, T, F]. Output shape: [B, 1, D].
                decoder_memory = self.chunk_embedding_to_decoder_memory(chunk_embedding.float())
                # Input shapes: memory [B, 1, D], masks [B, M]. Output shape: [B, M, 16, 8].
                return self.decode_masked_events(decoder_memory, masks)

        # Input shape: [B, T, F]. Output shape: [B, 1, D].
        decoder_memory = self.chunk_embedding_to_decoder_memory(chunk_embedding)
        # Input shapes: memory [B, 1, D], masks [B, M]. Output shape: [B, M, 16, 8].
        return self.decode_masked_events(decoder_memory, masks)

    @torch.no_grad()
    def encode_events(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        """Return per-event embeddings for diagnostics or downstream sequence experiments."""
        # Input shapes: header [B, 14], events [B, E, 16]. Output shapes: encoded [B, 2 + E, D], embedding [B, E, F].
        encoded_tokens, _ = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return self.chunk_embedding_bottleneck.event_only(encoded_tokens)

    def encode_tokens_for_training(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        # Input shapes: events [B, E, 16], masked indices [B, M]. Output shape: [B, M, 16].
        target_events = gather_events(events_uint8, masks.masked_event_indices)
        # Input shapes: header [B, 14], events [B, E, 16], visible indices [B, V]. Output shapes: [B, 2 + V, D], [B, 2 + V, F].
        encoded_tokens, chunk_embedding = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=masks.visible_event_indices,
            mask_config=mask_config,
            training=True,
        )
        return encoded_tokens, chunk_embedding, target_events

    def _encode_tokens(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        *,
        visible_event_indices: torch.Tensor | None,
        mask_config,
        training: bool,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        # Header/event corruption is deliberately weaker than event removal. It
        # regularizes the encoder against missing/corrupt bits without changing
        # the sequence length semantics learned by the transformer.
        if training and mask_config is not None:
            # Input shape: [B, 14]. Output shape: [B, 14].
            header_input_uint8 = maybe_corrupt_header(header_uint8, mask_config)
        else:
            # Input shape: [B, 14]. Output shape: [B, 14].
            header_input_uint8 = header_uint8

        # Input shapes: events [B, E, 16], optional indices [B, V]. Output shapes: [B, V or E, 16], [B, V or E].
        selected_events_uint8, selected_event_indices = self.visible_event_token_selector(events_uint8, visible_event_indices)
        if training and visible_event_indices is not None and mask_config is not None:
            # Input shape: [B, V, 16]. Output shape: [B, V, 16].
            selected_events_uint8 = maybe_corrupt_visible_events(selected_events_uint8, mask_config)

        # Input shape: [B, 14]. Output shape: [B, 1, D].
        header_token = self.header_token_encoder(header_input_uint8)
        # Input shapes: events [B, V or E, 16], indices [B, V or E]. Output shape: [B, V or E, D].
        visible_event_tokens = self.visible_event_token_encoder(selected_events_uint8, selected_event_indices)
        # Input shapes: header [B, 1, D], events [B, V or E, D]. Output shape: [B, 2 + V or 2 + E, D].
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        # Input shape: [B, T, D]. Output shape: [B, T, D].
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        # Input shape: [B, T, D]. Output shape: [B, T, F].
        chunk_embedding = self.chunk_embedding_bottleneck(encoded_tokens)
        return encoded_tokens, chunk_embedding

    def decode_masked_events(self, decoder_memory: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        # The decoder queries contain position only, not masked event bytes. Any
        # successful reconstruction therefore has to come from the chunk
        # embedding memory and the learned market-structure priors.
        # Input shape: masked indices [B, M]. Output shape: [B, M, D].
        masked_event_queries = self.masked_event_query_builder(masks.masked_event_indices)
        for decoder_layer in self.masked_query_cross_attention_decoder:
            # Input shapes: queries [B, M, D], memory [B, 1, D]. Output shape: [B, M, D].
            masked_event_queries = decoder_layer(masked_event_queries, decoder_memory)
        # Input shape: [B, M, D]. Output shape: [B, M, 16, 8].
        return self.masked_event_bit_prediction_head(masked_event_queries)
