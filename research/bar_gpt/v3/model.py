from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import torch
import torch.nn.functional as F
from torch import nn

from research.bar_gpt.v3.config import BarGPTConfig
from research.bar_gpt.v3.data import AUTOREGRESSIVE_VIEW_NAMES, TARGET_CLOCK_FEATURE_COUNT
from research.bar_gpt.v3.targets import (
    AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT,
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    NEXT_EVENT_GAP_CLASS_COUNT,
)


def build_model_mermaid() -> str:
    """Stable architecture diagram written into every training artifact."""
    return """flowchart TD
      A["As-of multiscale bars\\n1s 5s 10s 30s 1m 5m 30m 1h 1D 1W 1MO"]
      B["Stationary feature projection\\n50 input channels"]
      C["Continuous timeframe + pathway embeddings"]
      D["Causal decoder\\n8 RMSNorm + GQA RoPE blocks\\nd_model=384, heads=8, KV heads=4"]
      E["As-of fusion at each 1s origin"]
      F["Origin embedding\\nmodel representation"]
      G["Autoregressive mark heads\\n14 regression + 4 availability targets per intraday view"]
      H["Physical horizon heads\\n15 quantile-regression + 8 availability/condition targets"]
      I["Time heads\\nnext-event gap distribution + known target-clock residual"]
      A --> B --> C --> D --> E --> F
      F --> G
      F --> H
      F --> I --> G
    """


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = float(eps)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value_float = value.float()
        normalized = value_float * torch.rsqrt(value_float.pow(2).mean(dim=-1, keepdim=True) + self.eps)
        return normalized.to(dtype=value.dtype) * self.weight


def _rotate_half(value: torch.Tensor) -> torch.Tensor:
    first, second = value.chunk(2, dim=-1)
    return torch.cat((-second, first), dim=-1)


def _fused_linear_group(
    value: torch.Tensor,
    *layers: nn.Linear,
    fused_weight: torch.Tensor | None = None,
    fused_bias: torch.Tensor | None = None,
) -> tuple[torch.Tensor, ...]:
    """Evaluate compatible projections as one GEMM without changing parameters.

    Keeping the original ``nn.Linear`` modules preserves model keys and
    optimizer slots for strict checkpoint resume. Concatenating their weights
    only for execution replaces several reads of the same activation with one
    larger matrix multiplication; gradients still flow to each source weight.
    """
    if not layers:
        raise ValueError("at least one linear layer is required")
    input_features = int(layers[0].in_features)
    if any(int(layer.in_features) != input_features for layer in layers):
        raise ValueError("fused linear layers must have the same input width")
    expected_output_features = sum(int(layer.out_features) for layer in layers)
    if fused_weight is None:
        fused_weight = torch.cat(tuple(layer.weight for layer in layers), dim=0)
    if fused_weight.shape != (expected_output_features, input_features):
        raise ValueError("fused linear weight has the wrong shape")
    if fused_bias is None and any(layer.bias is not None for layer in layers):
        bias_parts: list[torch.Tensor] = []
        for layer in layers:
            bias = layer.bias
            bias_parts.append(
                bias
                if bias is not None
                else layer.weight.new_zeros(int(layer.out_features))
            )
        fused_bias = torch.cat(bias_parts)
    if fused_bias is not None and fused_bias.shape != (expected_output_features,):
        raise ValueError("fused linear bias has the wrong shape")
    projected = F.linear(value, fused_weight, fused_bias)
    return projected.split(
        tuple(int(layer.out_features) for layer in layers), dim=-1,
    )


class RotaryEmbedding(nn.Module):
    inverse_frequency: torch.Tensor

    def __init__(self, head_dim: int, base: float) -> None:
        super().__init__()
        inverse = 1.0 / (float(base) ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
        self.register_buffer("inverse_frequency", inverse, persistent=False)
        self._cache_key: tuple[torch.device, torch.dtype] | None = None
        self._cosine_cache: torch.Tensor | None = None
        self._sine_cache: torch.Tensor | None = None

    def forward(self, length: int, device: torch.device, dtype: torch.dtype) -> tuple[torch.Tensor, torch.Tensor]:
        key = (device, dtype)
        if (
            self._cache_key == key
            and self._cosine_cache is not None
            and self._sine_cache is not None
            and int(self._cosine_cache.shape[2]) >= int(length)
        ):
            return (
                self._cosine_cache[:, :, :length],
                self._sine_cache[:, :, :length],
            )
        positions = torch.arange(length, device=device, dtype=torch.float32)
        angles = torch.outer(positions, self.inverse_frequency.to(device=device))
        doubled = torch.cat((angles, angles), dim=-1)[None, None, :, :]
        cosine = doubled.cos().to(dtype=dtype)
        sine = doubled.sin().to(dtype=dtype)
        self._cache_key = key
        self._cosine_cache = cosine
        self._sine_cache = sine
        return cosine, sine


class CausalSelfAttention(nn.Module):
    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        self.n_heads = int(config.n_heads)
        self.n_kv_heads = int(config.n_kv_heads)
        self.head_dim = int(config.d_model // config.n_heads)
        self.q_proj = nn.Linear(config.d_model, self.n_heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(config.d_model, self.n_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(config.d_model, self.n_kv_heads * self.head_dim, bias=False)
        self.out_proj = nn.Linear(config.d_model, config.d_model, bias=False)
        self.q_norm = RMSNorm(self.head_dim)
        self.k_norm = RMSNorm(self.head_dim)
        self.rope = RotaryEmbedding(self.head_dim, config.rope_base)
        self.dropout = float(config.dropout)

    @staticmethod
    def build_attention_mask(
        length: int,
        *,
        attention_window: int | None,
        device: torch.device,
        token_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Build the exact combined causal, local-window, and padding mask."""
        window = int(length) if attention_window is None else int(attention_window)
        if window <= 0:
            raise ValueError("attention_window must be positive")
        positions = torch.arange(int(length), device=device)
        query_positions = positions[:, None]
        key_positions = positions[None, :]
        allowed = (key_positions <= query_positions) & (
            key_positions > query_positions - window
        )
        if token_mask is None:
            return allowed
        if token_mask.ndim != 2 or int(token_mask.shape[1]) != int(length):
            raise ValueError("token_mask must have shape [B,T]")
        allowed = allowed.view(1, 1, length, length) & token_mask[:, None, None, :]
        # Fully masked prefix queries are discarded after attention, but a
        # self edge prevents undefined all-masked SDPA rows.
        invalid_query = ~token_mask
        diagonal = torch.eye(
            length, dtype=torch.bool, device=device
        ).view(1, 1, length, length)
        return allowed | (invalid_query[:, None, :, None] & diagonal)

    def forward(
        self, value: torch.Tensor, *, attention_window: int | None = None,
        token_mask: torch.Tensor | None = None,
        rotary: tuple[torch.Tensor, torch.Tensor] | None = None,
        attention_mask: torch.Tensor | None = None,
        qkv_weight: torch.Tensor | None = None,
    ) -> torch.Tensor:
        batch, length, _ = value.shape
        if token_mask is not None and token_mask.shape != (batch, length):
            raise ValueError("token_mask must have shape [B,T]")
        query, key, val = _fused_linear_group(
            value, self.q_proj, self.k_proj, self.v_proj,
            fused_weight=qkv_weight,
        )
        query = query.view(batch, length, self.n_heads, self.head_dim).transpose(1, 2)
        key = key.view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        val = val.view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        query = self.q_norm(query)
        key = self.k_norm(key)
        cosine, sine = rotary or self.rope(length, value.device, value.dtype)
        query = query * cosine + _rotate_half(query) * sine
        key = key * cosine + _rotate_half(key) * sine
        grouped_query = self.n_kv_heads != self.n_heads
        # Fast path: a dense, unpadded sequence with no local-window limit can
        # delegate the lower-triangular mask directly to SDPA.  In this branch
        # ``is_causal=True`` is the sole mechanism that blocks future keys.
        if (
            attention_mask is None
            and token_mask is None
            and (attention_window is None or int(attention_window) >= length)
        ):
            enable_gqa = grouped_query and value.device.type == "cuda"
            if grouped_query and not enable_gqa:
                repeats = self.n_heads // self.n_kv_heads
                key = key.repeat_interleave(repeats, dim=1)
                val = val.repeat_interleave(repeats, dim=1)
            attended = F.scaled_dot_product_attention(
                query,
                key,
                val,
                dropout_p=self.dropout if self.training else 0.0,
                is_causal=True,
                enable_gqa=enable_gqa,
            )
        else:
            # Masked path: padding and/or a finite local window must be
            # combined with causality in one explicit lower-triangular mask.
            allowed = attention_mask
            if allowed is None:
                allowed = self.build_attention_mask(
                    length,
                    attention_window=attention_window,
                    device=value.device,
                    token_mask=token_mask,
                )
            # Dense masks make native CUDA GQA fall back to the quadratic math
            # kernel. Expanding compact K/V only on this masked branch lets
            # SDPA select its fused memory-efficient CUDA implementation.
            if grouped_query:
                repeats = self.n_heads // self.n_kv_heads
                key = key.repeat_interleave(repeats, dim=1)
                val = val.repeat_interleave(repeats, dim=1)
            attended = F.scaled_dot_product_attention(
                query,
                key,
                val,
                attn_mask=allowed,
                dropout_p=self.dropout if self.training else 0.0,
                # The explicit mask already contains causality, window, and
                # padding constraints; adding implicit causality is redundant.
                is_causal=False,
            )
        attended = attended.transpose(1, 2).contiguous().view(batch, length, -1)
        output = self.out_proj(attended)
        return output if token_mask is None else output * token_mask.unsqueeze(-1)

class SwiGLU(nn.Module):
    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        hidden = int(config.ff_multiplier * config.d_model)
        hidden = max(256, 256 * ((hidden + 255) // 256))
        self.gate = nn.Linear(config.d_model, hidden, bias=False)
        self.up = nn.Linear(config.d_model, hidden, bias=False)
        self.down = nn.Linear(hidden, config.d_model, bias=False)

    def forward(
        self, value: torch.Tensor, *, gate_up_weight: torch.Tensor | None = None,
    ) -> torch.Tensor:
        gate, up = _fused_linear_group(
            value, self.gate, self.up, fused_weight=gate_up_weight,
        )
        return self.down(F.silu(gate) * up)


class DecoderBlock(nn.Module):
    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        self.attention_norm = RMSNorm(config.d_model)
        self.attention = CausalSelfAttention(config)
        self.ffn_norm = RMSNorm(config.d_model)
        self.ffn = SwiGLU(config)

    def forward(
        self, value: torch.Tensor, *, attention_window: int | None = None,
        token_mask: torch.Tensor | None = None,
        rotary: tuple[torch.Tensor, torch.Tensor] | None = None,
        attention_mask: torch.Tensor | None = None,
        qkv_weight: torch.Tensor | None = None,
        gate_up_weight: torch.Tensor | None = None,
    ) -> torch.Tensor:
        value = value + self.attention(
            self.attention_norm(value), attention_window=attention_window,
            token_mask=token_mask, rotary=rotary, attention_mask=attention_mask,
            qkv_weight=qkv_weight,
        )
        value = value + self.ffn(
            self.ffn_norm(value), gate_up_weight=gate_up_weight,
        )
        return value if token_mask is None else value * token_mask.unsqueeze(-1)


@dataclass(slots=True)
class BarGPTOutput:
    embeddings: torch.Tensor
    scale_embeddings: dict[str, torch.Tensor]
    autoregressive: dict[str, torch.Tensor]
    autoregressive_gap_logits: dict[str, torch.Tensor]
    horizon_quantiles: torch.Tensor | None
    horizon_availability_logits: torch.Tensor | None


class ContinuousTimeframeEmbedding(nn.Module):
    """Encode physical duration so the shared decoder can generalize beyond a fixed scale vocabulary."""

    frequencies: torch.Tensor

    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        half = config.timeframe_fourier_dim // 2
        frequencies = torch.exp(torch.linspace(0.0, 6.0, half))
        self.register_buffer("frequencies", frequencies, persistent=False)
        self.projection = nn.Sequential(
            nn.Linear(config.timeframe_fourier_dim + 1, config.d_model, bias=False),
            nn.SiLU(),
            nn.Linear(config.d_model, config.d_model, bias=False),
        )

    def forward(self, timeframe_us: int | float | torch.Tensor, *, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        duration = torch.as_tensor(timeframe_us, device=device, dtype=torch.float32).clamp_min(1.0)
        log_seconds = torch.log(duration / 1_000_000.0).reshape(-1, 1)
        angles = log_seconds * self.frequencies.to(device=device).reshape(1, -1)
        encoded = torch.cat((log_seconds, angles.sin(), angles.cos()), dim=-1)
        return self.projection(encoded.to(dtype=dtype))


class TargetClockEmbedding(nn.Module):
    """Encode each known physical target timestamp as a zero-init residual."""

    def __init__(self, rank: int) -> None:
        super().__init__()
        self.projection = nn.Linear(TARGET_CLOCK_FEATURE_COUNT, rank, bias=False)
        nn.init.zeros_(self.projection.weight)

    def forward(
        self,
        features: torch.Tensor,
        *,
        dtype: torch.dtype,
    ) -> torch.Tensor:
        if features.ndim != 4 or features.shape[-1] != TARGET_CLOCK_FEATURE_COUNT:
            raise ValueError("target clock features must have shape [B,N,H,8]")
        return self.projection(features.to(dtype=dtype))


class BarGPTV3(nn.Module):
    """Shared causal decoder over continuous bar streams with as-of multiscale fusion."""

    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        config.validate()
        self.config = config
        self.input_norm = RMSNorm(config.feature_dim)
        self.input_projection = nn.Linear(config.feature_dim, config.d_model, bias=False)
        self.timeframe_embedding = ContinuousTimeframeEmbedding(config)
        self.pathway_embedding = nn.Embedding(config.pathway_count, config.d_model)
        self.blocks = nn.ModuleList(DecoderBlock(config) for _ in range(config.n_layers))
        self.output_norm = RMSNorm(config.d_model)
        self.scale_gate = nn.Sequential(
            nn.Linear(config.d_model * 2, config.d_model, bias=False),
            nn.Sigmoid(),
        )
        self.continuous_target_dim = CONTINUOUS_TARGET_COUNT
        if config.target_dim != CONTINUOUS_TARGET_COUNT + AVAILABILITY_TARGET_COUNT:
            raise ValueError("target_dim does not match the sparse OHLC physical target contract")
        if config.autoregressive_target_dim != (
            AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT + AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT
        ):
            raise ValueError("autoregressive_target_dim does not match the sparse OHLC AR target contract")
        self.autoregressive_continuous_head = nn.Linear(
            config.d_model, AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT, bias=False
        )
        self.autoregressive_availability_head = nn.Linear(
            config.d_model, AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT, bias=True
        )
        self.autoregressive_gap_head = nn.Linear(
            config.d_model, NEXT_EVENT_GAP_CLASS_COUNT, bias=True
        )
        nn.init.zeros_(self.autoregressive_gap_head.weight)
        nn.init.zeros_(self.autoregressive_gap_head.bias)
        self.autoregressive_gap_condition = nn.Embedding(
            NEXT_EVENT_GAP_CLASS_COUNT, config.d_model
        )
        nn.init.zeros_(self.autoregressive_gap_condition.weight)
        self.horizon_embedding = nn.Embedding(config.max_horizons, config.horizon_rank)
        self.horizon_state = nn.Linear(config.d_model, config.horizon_rank, bias=False)
        self.target_clock_embedding = TargetClockEmbedding(config.horizon_rank)
        self.horizon_head = nn.Linear(
            config.horizon_rank,
            self.continuous_target_dim * len(config.quantiles),
            bias=True,
        )

        self.horizon_availability_head = nn.Linear(config.horizon_rank, AVAILABILITY_TARGET_COUNT, bias=True)

    def encode(
        self,
        features: torch.Tensor,
        timeframe_us: int,
        pathway_id: int,
        *,
        attention_window: int | None = None,
        token_mask: torch.Tensor | None = None,
        scale_embedding: torch.Tensor | None = None,
        layer_projection_weights: tuple[
            tuple[torch.Tensor, torch.Tensor], ...
        ] | None = None,
    ) -> torch.Tensor:
        if features.ndim != 3 or features.shape[-1] != self.config.feature_dim:
            raise ValueError(f"features must have shape [B,T,{self.config.feature_dim}]")
        state = self.input_projection(self.input_norm(features))
        scale = (
            self.timeframe_embedding(
                timeframe_us,
                device=features.device,
                dtype=features.dtype,
            )
            if scale_embedding is None
            else scale_embedding
        ).view(1, 1, -1)
        pathway = self.pathway_embedding.weight[int(pathway_id)].view(1, 1, -1)
        state = state + scale + pathway
        if token_mask is not None:
            state = state * token_mask.unsqueeze(-1)
        layer_windows: list[int | None]
        if attention_window is None:
            layer_windows = [None] * len(self.blocks)
        else:
            # A local window repeated unchanged at every layer expands the
            # stack's effective receptive field. Distribute the total causal
            # radius across layers so no representation can depend on bars
            # older than the configured view context, while the full stack
            # can still connect the newest token to the oldest allowed token.
            total_radius = max(0, int(attention_window) - 1)
            radius, extra = divmod(total_radius, max(1, len(self.blocks)))
            layer_windows = [radius + (1 if index < extra else 0) + 1 for index in range(len(self.blocks))]
        # RoPE depends only on view length/device/dtype, so all shared decoder
        # layers can reuse one exact table without changing attention math.
        rotary = self.blocks[0].attention.rope(
            features.shape[1], features.device, features.dtype
        )
        # A view has at most two distinct per-layer windows. Materialize each
        # exact dense mask once and retain it through this view's decoder
        # stack, trading a small bounded amount of memory for fewer quadratic
        # mask-building kernels. The same mask is never shared across batches.
        attention_masks: dict[int | None, torch.Tensor] = {}
        for layer_window in dict.fromkeys(layer_windows):
            needs_explicit_mask = token_mask is not None or (
                layer_window is not None and int(layer_window) < features.shape[1]
            )
            if needs_explicit_mask:
                attention_masks[layer_window] = CausalSelfAttention.build_attention_mask(
                    features.shape[1],
                    attention_window=layer_window,
                    device=features.device,
                    token_mask=token_mask,
                )
        if layer_projection_weights is not None and len(layer_projection_weights) != len(self.blocks):
            raise ValueError("layer_projection_weights must match the decoder depth")
        for index, (block, layer_window) in enumerate(
            zip(self.blocks, layer_windows, strict=True)
        ):
            qkv_weight = gate_up_weight = None
            if layer_projection_weights is not None:
                qkv_weight, gate_up_weight = layer_projection_weights[index]
            state = block(
                state, attention_window=layer_window,
                token_mask=token_mask, rotary=rotary,
                attention_mask=attention_masks.get(layer_window),
                qkv_weight=qkv_weight,
                gate_up_weight=gate_up_weight,
            )
        state = self.output_norm(state)
        return state if token_mask is None else state * token_mask.unsqueeze(-1)

    @staticmethod
    def _gather_sequence(state: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
        if indices.ndim != 2 or indices.shape[0] != state.shape[0]:
            raise ValueError("as-of/origin indices must have shape [B,N]")
        safe = indices.clamp(min=0, max=max(state.shape[1] - 1, 0))
        return torch.gather(state, 1, safe.unsqueeze(-1).expand(-1, -1, state.shape[-1]))

    def forward(
        self,
        views: Mapping[str, torch.Tensor],
        *,
        timeframe_us: Mapping[str, int],
        pathway_ids: Mapping[str, int],
        base_view: str,
        origin_indices: torch.Tensor,
        asof_indices: Mapping[str, torch.Tensor] | None = None,
        view_masks: Mapping[str, torch.Tensor] | None = None,
        attention_windows: Mapping[str, int] | None = None,
        horizon_ids: torch.Tensor | None = None,
        target_clock_features: torch.Tensor | None = None,
    ) -> BarGPTOutput:
        fused, encoded = self.embed(
            views,
            timeframe_us=timeframe_us,
            pathway_ids=pathway_ids,
            base_view=base_view,
            origin_indices=origin_indices,
            asof_indices=asof_indices,
            view_masks=view_masks,
            attention_windows=attention_windows,
        )
        autoregressive = {}
        autoregressive_gap_logits = {}
        autoregressive_weight = torch.cat((
            self.autoregressive_continuous_head.weight,
            self.autoregressive_availability_head.weight,
        ), dim=0)
        availability_bias = self.autoregressive_availability_head.bias
        gap_bias = self.autoregressive_gap_head.bias
        if availability_bias is None or gap_bias is None:
            raise RuntimeError("autoregressive classification heads require biases")
        autoregressive_bias = torch.cat((
            self.autoregressive_continuous_head.weight.new_zeros(
                self.autoregressive_continuous_head.out_features
            ),
            availability_bias,
        ))
        for name in AUTOREGRESSIVE_VIEW_NAMES:
            if name not in encoded:
                continue
            state = encoded[name]
            # Calendar views are context-only by contract and intentionally
            # have no autoregressive targets or heads.
            gap_logits = self.autoregressive_gap_head(state[:, :-1])
            gap_context = torch.matmul(
                gap_logits.softmax(dim=-1),
                self.autoregressive_gap_condition.weight,
            )
            continuous, availability = _fused_linear_group(
                state[:, :-1] + gap_context,
                self.autoregressive_continuous_head,
                self.autoregressive_availability_head,
                fused_weight=autoregressive_weight,
                fused_bias=autoregressive_bias,
            )
            autoregressive[name] = torch.cat((continuous, availability), dim=-1)
            autoregressive_gap_logits[name] = gap_logits
        quantiles = None
        availability_logits = None
        if horizon_ids is not None:
            if horizon_ids.ndim != 1:
                raise ValueError("horizon_ids must have shape [H]")
            state_rank = self.horizon_state(fused).unsqueeze(2)
            horizon_rank = self.horizon_embedding(horizon_ids.long()).view(1, 1, -1, self.config.horizon_rank)
            clock_rank = (
                self.target_clock_embedding(
                    target_clock_features,
                    dtype=fused.dtype,
                )
                if target_clock_features is not None
                else torch.zeros_like(state_rank + horizon_rank)
            )
            conditioned = F.silu(state_rank + horizon_rank + clock_rank)
            raw, availability_logits = _fused_linear_group(
                conditioned,
                self.horizon_head,
                self.horizon_availability_head,
            )
            raw_quantiles = raw.view(
                fused.shape[0],
                fused.shape[1],
                horizon_ids.numel(),
                self.continuous_target_dim,
                len(self.config.quantiles),
            )
            if len(self.config.quantiles) > 1:
                first = raw_quantiles[..., :1]
                quantiles = torch.cat((first, first + F.softplus(raw_quantiles[..., 1:]).cumsum(dim=-1)), dim=-1)
            else:
                quantiles = raw_quantiles
        return BarGPTOutput(
            embeddings=fused,
            scale_embeddings=encoded,
            autoregressive=autoregressive,
            autoregressive_gap_logits=autoregressive_gap_logits,
            horizon_quantiles=quantiles,
            horizon_availability_logits=availability_logits,
        )

    def embed(
        self,
        views: Mapping[str, torch.Tensor],
        *,
        timeframe_us: Mapping[str, int],
        pathway_ids: Mapping[str, int],
        base_view: str,
        origin_indices: torch.Tensor,
        asof_indices: Mapping[str, torch.Tensor] | None = None,
        view_masks: Mapping[str, torch.Tensor] | None = None,
        attention_windows: Mapping[str, int] | None = None,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        if base_view not in views:
            raise KeyError(f"base view {base_view!r} is absent")
        if attention_windows is not None:
            missing = sorted(set(views) - set(attention_windows))
            if missing:
                raise KeyError(f"missing attention windows for views: {missing}")
        # Decoder weights are shared across all views and remain unchanged for
        # the complete forward/backward pass. Concatenate each projection
        # group once, retain the resulting autograd tensors while the views
        # execute, and release them with this forward graph.
        layer_projection_weights = tuple(
            (
                torch.cat((
                    block.attention.q_proj.weight,
                    block.attention.k_proj.weight,
                    block.attention.v_proj.weight,
                ), dim=0),
                torch.cat((block.ffn.gate.weight, block.ffn.up.weight), dim=0),
            )
            for block in self.blocks
        )
        view_names = tuple(views)
        reference = views[base_view]
        # Durations are constant for a forward pass. Evaluate the shared
        # trainable timeframe MLP once for all views instead of launching its
        # small Fourier/linear sequence independently eleven times. Indexing
        # rows preserves exactly the same parameters and gradient aggregation.
        scale_rows = self.timeframe_embedding(
            torch.as_tensor(
                [timeframe_us[name] for name in view_names],
                device=reference.device,
                dtype=torch.float32,
            ),
            device=reference.device,
            dtype=reference.dtype,
        )
        scale_by_name = {
            name: scale_rows[index] for index, name in enumerate(view_names)
        }
        encoded = {
            name: self.encode(
                value,
                timeframe_us[name],
                pathway_ids[name],
                attention_window=None if attention_windows is None else int(attention_windows[name]),
                token_mask=None if view_masks is None else view_masks.get(name),
                scale_embedding=scale_by_name[name],
                layer_projection_weights=layer_projection_weights,
            )
            for name, value in views.items()
        }
        fused = self._gather_sequence(encoded[base_view], origin_indices)
        for name, state in encoded.items():
            if name == base_view:
                continue
            if asof_indices is None or name not in asof_indices:
                raise KeyError(f"missing causal as-of index for {name!r}")
            available = asof_indices[name] >= 0
            coarse = self._gather_sequence(state, asof_indices[name])
            gate = self.scale_gate(torch.cat((fused, coarse), dim=-1))
            candidate = fused + gate * coarse
            fused = torch.where(available.unsqueeze(-1), candidate, fused)
        return fused, encoded
