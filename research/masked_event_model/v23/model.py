from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import torch
from torch import nn

from research.masked_event_model.v23.config import ModelConfig
from research.masked_event_model.v23.masking import EventMaskBatch, gather_events, maybe_corrupt_header, maybe_corrupt_visible_events


HEADER_BYTES = 14
EVENT_BYTES = 16
BITS_PER_BYTE = 8

# Shape shorthand used by the forward-path comments:
# B = batch size, E = total events per chunk, V = visible events, M = masked events,
# T = encoder tokens (CLS + header + visible events), D = model width, Z = embedding width.


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
    # Shape: [B, Z]. Exportable encoder representation for downstream models.
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


class GlobalEventPositionEmbedding(nn.Embedding):
    """Event-position table used before the visible-token encoder transformer."""


class EventRoleEmbedding(nn.Embedding):
    """Learned role vector shared by event tokens before the encoder."""


class ChunkClsRoleEmbedding(nn.Embedding):
    """Learned role vector added to the chunk-level CLS token."""


class MaskedEventPositionEmbedding(nn.Embedding):
    """Decoder-only position embedding for masked event locations."""


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
        self.event_role_embedding = EventRoleEmbedding(1, config.d_model)

    def forward(
        self,
        visible_events_uint8: torch.Tensor,
        visible_event_indices: torch.Tensor,
        global_event_position_embedding: GlobalEventPositionEmbedding,
    ) -> torch.Tensor:
        # Input shape: [B, V, 16]. Output shape: [B, V, 128].
        event_bits = self.event_bytes_to_signed_bits(visible_events_uint8)
        # Input shape: [B, V, 128]. Output shape: [B, V, D].
        event_tokens = self.event_bits_to_model_tokens(event_bits)
        # Input shapes: tokens [B, V, D], positions [B, V]. Output shape: [B, V, D].
        event_tokens = event_tokens + global_event_position_embedding(visible_event_indices)
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


class FixedGridChunkBottleneck(nn.Module):
    """Create the exported embedding from semantic fixed-grid groups.

    The encoder still processes only visible event tokens during MAE training,
    but this bottleneck scatters those variable-length token outputs back into a
    fixed semantic layout:

    `[CLS, header, event_0, ..., event_127]`.

    The fixed grid starts as zeros. Encoded CLS/header tokens are placed in
    slots 0 and 1. Visible encoded event tokens are placed at slots `2 + event
    index`. Masked event slots remain zero, so the bottleneck receives no
    learned placeholder content for hidden events.

    v23 differs from v21 by removing the final nonlinear merge projection.
    CLS, header, and each 16-event block each get a two-layer nonlinear MLP
    branch ending in `bottleneck_branch_embedding_dim` features. The exported
    chunk embedding is the direct concatenation of those 10 branch outputs, so
    its width is `10 * bottleneck_branch_embedding_dim`.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.events_per_chunk = int(events_per_chunk)
        self.fixed_token_count = 2 + self.events_per_chunk
        self.event_group_size = 16
        if self.events_per_chunk % self.event_group_size != 0:
            raise ValueError(f"events_per_chunk={self.events_per_chunk} must be divisible by {self.event_group_size}")
        self.event_group_count = self.events_per_chunk // self.event_group_size
        self.branch_hidden_dim = int(config.bottleneck_branch_hidden_dim)
        self.branch_embedding_dim = int(config.bottleneck_branch_embedding_dim)
        self.exported_embedding_dim = (2 + self.event_group_count) * self.branch_embedding_dim
        if int(config.embedding_dim) != self.exported_embedding_dim:
            raise ValueError(
                f"v23 grouped bottleneck exports {self.exported_embedding_dim} features from "
                f"10 branches x {self.branch_embedding_dim}; "
                f"set embedding_dim={self.exported_embedding_dim}, got {config.embedding_dim}"
            )
        self.cls_token_to_branch_embedding = nn.Sequential(
            OrderedDict(
                [
                    ("cls_token_model_width_to_branch_hidden", nn.Linear(config.d_model, self.branch_hidden_dim)),
                    ("cls_token_branch_hidden_gelu", nn.GELU()),
                    ("cls_token_branch_hidden_layer_norm", nn.LayerNorm(self.branch_hidden_dim)),
                    ("cls_token_branch_hidden_to_branch_features", nn.Linear(self.branch_hidden_dim, self.branch_embedding_dim)),
                    ("cls_token_branch_output_gelu", nn.GELU()),
                    ("cls_token_branch_output_layer_norm", nn.LayerNorm(self.branch_embedding_dim)),
                ]
            )
        )
        self.header_token_to_branch_embedding = nn.Sequential(
            OrderedDict(
                [
                    ("header_token_model_width_to_branch_hidden", nn.Linear(config.d_model, self.branch_hidden_dim)),
                    ("header_token_branch_hidden_gelu", nn.GELU()),
                    ("header_token_branch_hidden_layer_norm", nn.LayerNorm(self.branch_hidden_dim)),
                    ("header_token_branch_hidden_to_branch_features", nn.Linear(self.branch_hidden_dim, self.branch_embedding_dim)),
                    ("header_token_branch_output_gelu", nn.GELU()),
                    ("header_token_branch_output_layer_norm", nn.LayerNorm(self.branch_embedding_dim)),
                ]
            )
        )
        self.event_group_to_branch_embedding = nn.ModuleDict(
            OrderedDict(
                (
                    f"event_group_{group_index:02d}_{group_index * self.event_group_size:03d}_{(group_index + 1) * self.event_group_size - 1:03d}",
                    nn.Sequential(
                        OrderedDict(
                            [
                                (
                                    "event_group_flattened_tokens_to_branch_hidden",
                                    nn.Linear(self.event_group_size * config.d_model, self.branch_hidden_dim),
                                ),
                                ("event_group_branch_hidden_gelu", nn.GELU()),
                                ("event_group_branch_hidden_layer_norm", nn.LayerNorm(self.branch_hidden_dim)),
                                ("event_group_branch_hidden_to_branch_features", nn.Linear(self.branch_hidden_dim, self.branch_embedding_dim)),
                                ("event_group_branch_output_gelu", nn.GELU()),
                                ("event_group_branch_output_layer_norm", nn.LayerNorm(self.branch_embedding_dim)),
                            ]
                        )
                    ),
                )
                for group_index in range(self.event_group_count)
            )
        )
        self.chunk_embedding_output = ChunkEmbeddingOutput()

    def build_fixed_token_grid(
        self,
        encoded_tokens: torch.Tensor,
        visible_event_indices: torch.Tensor,
    ) -> torch.Tensor:
        # Input shapes: encoded [B, 2 + V, D], indices [B, V]. Output shape: [B, 130, D].
        fixed_tokens = encoded_tokens.new_zeros((encoded_tokens.shape[0], self.fixed_token_count, encoded_tokens.shape[-1]))
        # Input shape: [B, 2 + V, D]. Output shape: [B, 2, D] added into fixed CLS/header slots.
        fixed_tokens[:, :2, :] = encoded_tokens[:, :2, :]
        # Input shape: [B, V, D]. Output shape: [B, V, D].
        visible_event_tokens = encoded_tokens[:, 2:, :]
        # Input shape: [B, V]. Output shape: [B, V, D] with event slots shifted by CLS/header.
        fixed_event_slots = (visible_event_indices.to(device=encoded_tokens.device, dtype=torch.long) + 2).unsqueeze(-1)
        fixed_event_slots = fixed_event_slots.expand(-1, -1, encoded_tokens.shape[-1])
        # Input shapes: fixed [B, 130, D], slots [B, V, D], visible tokens [B, V, D].
        # Output shape: [B, 130, D], with masked slots left as zero vectors.
        return fixed_tokens.scatter(1, fixed_event_slots, visible_event_tokens)

    def forward(
        self,
        encoded_tokens: torch.Tensor,
        visible_event_indices: torch.Tensor,
    ) -> torch.Tensor:
        # Input shapes: encoded [B, 2 + V, D], indices [B, V]. Output shape: [B, 130, D].
        fixed_tokens = self.build_fixed_token_grid(encoded_tokens, visible_event_indices)
        if self.config.bottleneck_force_fp32 and fixed_tokens.is_cuda:
            # Keep the exported representation projection in FP32 for precision
            # diagnostics while allowing the transformer and decoder to remain
            # in the active AMP dtype.
            with torch.amp.autocast("cuda", enabled=False):
                pooled_embedding = self.project_fixed_token_grid(fixed_tokens.float())
            # Input shape: [B, Z]. Output shape: [B, Z].
            return self.chunk_embedding_output(pooled_embedding)
        pooled_embedding = self.project_fixed_token_grid(fixed_tokens)
        # Input shape: [B, Z]. Output shape: [B, Z].
        return self.chunk_embedding_output(pooled_embedding)

    def project_fixed_token_grid(self, fixed_tokens: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, 130, D]. Output shape: [B, Z].
        branch_embeddings = [
            self.cls_token_to_branch_embedding(fixed_tokens[:, 0, :]),
            self.header_token_to_branch_embedding(fixed_tokens[:, 1, :]),
        ]
        event_tokens = fixed_tokens[:, 2:, :]
        for group_index, group_mlp in enumerate(self.event_group_to_branch_embedding.values()):
            group_start = group_index * self.event_group_size
            group_end = group_start + self.event_group_size
            # Input shape: [B, 16, D]. Output shape after flatten: [B, 16 * D].
            flattened_event_group = event_tokens[:, group_start:group_end, :].flatten(1)
            # Input shape: [B, 16 * D]. Output shape: [B, Z].
            branch_embeddings.append(group_mlp(flattened_event_group))
        # Input shape: 10 x [B, branch_embedding_dim]. Output shape: [B, embedding_dim].
        concatenated_branch_embeddings = torch.cat(branch_embeddings, dim=1)
        return concatenated_branch_embeddings


class ChunkEmbeddingOutput(nn.Identity):
    """Named terminal layer for the reusable chunk embedding."""


class ResidualDecoderBlock(nn.Module):
    """One residual MLP block for masked-event reconstruction.

    The block keeps sequence shape unchanged while giving the disposable
    pretraining decoder a deeper nonlinear path. This is intentionally cheaper
    than a transformer decoder and has no access to masked target bytes.
    """

    def __init__(self, *, config: ModelConfig, block_index: int) -> None:
        super().__init__()
        self.block_index = int(block_index)
        self.block = nn.Sequential(
            OrderedDict(
                [
                    (f"decoder_residual_{block_index:02d}_input_layer_norm", nn.LayerNorm(config.d_model)),
                    (f"decoder_residual_{block_index:02d}_expand_to_ffn_width", nn.Linear(config.d_model, config.ff_dim)),
                    (f"decoder_residual_{block_index:02d}_gelu", nn.GELU()),
                    (f"decoder_residual_{block_index:02d}_dropout_after_gelu", nn.Dropout(config.dropout)),
                    (f"decoder_residual_{block_index:02d}_contract_to_model_width", nn.Linear(config.ff_dim, config.d_model)),
                    (f"decoder_residual_{block_index:02d}_output_dropout", nn.Dropout(config.dropout)),
                ]
            )
        )

    def forward(self, decoder_state: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, M, D]. Output shape: [B, M, D].
        return decoder_state + self.block(decoder_state)


class PerMaskedEventResidualMlpDecoder(nn.Module):
    """Reconstruct masked event bytes with a residual per-event MLP decoder.

    Each masked event receives the projected chunk embedding plus a decoder-only
    position embedding for that masked event index. This position embedding is
    not passed to the fixed-grid chunk bottleneck; it exists only after the
    exported `[B, Z]` chunk embedding and belongs to disposable pretraining
    reconstruction machinery.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.residual_block_count = 2
        self.chunk_embedding_to_decoder_context = nn.Sequential(
            OrderedDict(
                [
                    ("chunk_embedding_to_decoder_width", nn.Linear(config.embedding_dim, config.d_model)),
                    ("chunk_embedding_decoder_context_gelu", nn.GELU()),
                    ("chunk_embedding_decoder_context_layer_norm", nn.LayerNorm(config.d_model)),
                ]
            )
        )
        self.masked_event_position_embedding_for_decoder = MaskedEventPositionEmbedding(events_per_chunk, config.d_model)
        self.position_memory_residual_mlp_decoder = nn.Sequential(
            OrderedDict(
                (
                    f"position_memory_residual_block_{block_index:02d}",
                    ResidualDecoderBlock(config=config, block_index=block_index),
                )
                for block_index in range(self.residual_block_count)
            )
        )
        self.position_memory_to_bit_logits = nn.Sequential(
            OrderedDict(
                [
                    ("position_memory_final_layer_norm", nn.LayerNorm(config.d_model)),
                    # Deliberately no activation here: BCE-with-logits expects
                    # unconstrained raw bit logits.
                    ("position_memory_to_16x8_bit_logits", nn.Linear(config.d_model, EVENT_BYTES * BITS_PER_BYTE)),
                ]
            )
        )

    def forward(self, chunk_embedding: torch.Tensor, masked_event_indices: torch.Tensor) -> torch.Tensor:
        # Input shape: [B, Z]. Output shape after unsqueeze: [B, 1, D].
        chunk_context = self.chunk_embedding_to_decoder_context(chunk_embedding).unsqueeze(1)
        # Input shape: [B, M]. Output shape: [B, M, D].
        masked_position_context = self.masked_event_position_embedding_for_decoder(masked_event_indices)
        # Input shapes: memory [B, 1, D], positions [B, M, D]. Output shape: [B, M, D].
        decoder_input = chunk_context + masked_position_context
        # Input shape: [B, M, D]. Output shape: [B, M, D].
        decoded_state = self.position_memory_residual_mlp_decoder(decoder_input)
        # Input shape: [B, M, D]. Output shape: [B, M, 128].
        logits = self.position_memory_to_bit_logits(decoded_state)
        # Input shape: [B, M, 128]. Output shape: [B, M, 16, 8].
        return logits.view(logits.shape[0], logits.shape[1], EVENT_BYTES, BITS_PER_BYTE)


class EventChunkEncoder(nn.Module):
    """Standalone encoder that ends at the reusable `[B, embedding_dim]` chunk embedding.

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
        self.global_event_position_embedding = GlobalEventPositionEmbedding(self.events_per_chunk, config.d_model)
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
        self.chunk_embedding_bottleneck = FixedGridChunkBottleneck(events_per_chunk=self.events_per_chunk, config=config)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        self.encoder_sequence_builder.reset_parameters()

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        """Production path: encode all event records and return only the chunk embedding."""

        # Input shapes: header [B, 14], events [B, E, 16]. Output shapes: encoded [B, 2 + E, D], embedding [B, Z].
        _, chunk_embedding = self.encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return chunk_embedding

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
        visible_event_tokens = self.visible_event_token_encoder(
            selected_events_uint8,
            selected_event_indices,
            self.global_event_position_embedding,
        )
        # Input shapes: header [B, 1, D], events [B, V or E, D]. Output shape: [B, 2 + V or 2 + E, D].
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        # Input shape: [B, T, D]. Output shape: [B, T, D].
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        # Input shapes: encoded [B, T, D], indices [B, V or E]. Output shape: [B, Z].
        chunk_embedding = self.chunk_embedding_bottleneck(
            encoded_tokens,
            selected_event_indices,
        )
        return encoded_tokens, chunk_embedding


ENCODER_MODULE_NAMES = (
    "visible_event_token_selector",
    "global_event_position_embedding",
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
    memory token derived from `chunk_embedding`; this prevents the decoder from
    reconstructing events by reading high-dimensional encoder states directly.
    """

    def __init__(self, *, events_per_chunk: int, config: ModelConfig) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.config = config
        self.input_representation = str(config.input_representation)
        if self.input_representation != "bit":
            raise ValueError("v23 currently supports input_representation='bit' only")

        self.header_token_encoder = HeaderTokenEncoder(config)
        self.visible_event_token_selector = VisibleEventTokenSelector()
        self.global_event_position_embedding = GlobalEventPositionEmbedding(self.events_per_chunk, config.d_model)
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
        self.chunk_embedding_bottleneck = FixedGridChunkBottleneck(events_per_chunk=self.events_per_chunk, config=config)
        self.per_masked_event_mlp_decoder = PerMaskedEventResidualMlpDecoder(events_per_chunk=self.events_per_chunk, config=config)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        self.encoder_sequence_builder.reset_parameters()

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
        # Input shapes: header [B, 14], events [B, E, 16], masks [B, M]/[B, V]. Output shapes: [B, T, D], [B, Z], [B, M, 16].
        encoded_tokens, chunk_embedding, target_events = self.encode_tokens_for_training(
            header_uint8, events_uint8, masks, mask_config
        )
        # Input shape: [B, Z]. Output shape: [B, M, 16, 8].
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
        """Production embedding path: no masks, no decoder, no reconstruction work."""
        # Input shapes: header [B, 14], events [B, E, 16]. Output shapes: encoded [B, 2 + E, D], embedding [B, Z].
        _, chunk_embedding = self._encode_tokens(
            header_uint8,
            events_uint8,
            visible_event_indices=None,
            mask_config=None,
            training=False,
        )
        return chunk_embedding

    def decode_from_chunk_embedding(self, chunk_embedding: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        """Reconstruct masked events, optionally forcing the decoder path to FP32.

        The encoder remains inside the training loop's autocast context, so it
        keeps the BF16 speed benefit. The disposable decoder maps the projected
        `[B, Z]` chunk embedding plus decoder-only masked-position embeddings
        to the masked positions used by the loss.
        """

        if self.config.decoder_force_fp32 and chunk_embedding.is_cuda:
            with torch.amp.autocast("cuda", enabled=False):
                # Input shapes: chunk embedding [B, Z], masks [B, M]. Output shape: [B, M, 16, 8].
                return self.decode_masked_events(chunk_embedding.float(), masks)

        # Input shapes: chunk embedding [B, Z], masks [B, M]. Output shape: [B, M, 16, 8].
        return self.decode_masked_events(chunk_embedding, masks)

    def encode_tokens_for_training(
        self,
        header_uint8: torch.Tensor,
        events_uint8: torch.Tensor,
        masks: EventMaskBatch,
        mask_config,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        # Input shapes: events [B, E, 16], masked indices [B, M]. Output shape: [B, M, 16].
        target_events = gather_events(events_uint8, masks.masked_event_indices)
        # Input shapes: header [B, 14], events [B, E, 16], visible indices [B, V]. Output shapes: [B, 2 + V, D], [B, Z].
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
        visible_event_tokens = self.visible_event_token_encoder(
            selected_events_uint8,
            selected_event_indices,
            self.global_event_position_embedding,
        )
        # Input shapes: header [B, 1, D], events [B, V or E, D]. Output shape: [B, 2 + V or 2 + E, D].
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        # Input shape: [B, T, D]. Output shape: [B, T, D].
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        # Input shapes: encoded [B, T, D], indices [B, V or E]. Output shape: [B, Z].
        chunk_embedding = self.chunk_embedding_bottleneck(
            encoded_tokens,
            selected_event_indices,
        )
        return encoded_tokens, chunk_embedding

    def decode_masked_events(self, chunk_embedding: torch.Tensor, masks: EventMaskBatch) -> torch.Tensor:
        # The decoder never receives masked event bytes. Its only position
        # signal is the v12-style decoder-only embedding for masked indices,
        # after the exported chunk embedding has already been formed.
        # Input shapes: chunk embedding [B, Z], masked indices [B, M]. Output shape: [B, M, 16, 8].
        return self.per_masked_event_mlp_decoder(chunk_embedding, masks.masked_event_indices)
