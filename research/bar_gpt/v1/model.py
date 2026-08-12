from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import torch
import torch.nn.functional as F
from torch import nn

from research.bar_gpt.v1.config import BarGPTConfig
from research.bar_gpt.v1.data import AUTOREGRESSIVE_VIEW_NAMES
from research.bar_gpt.v1.targets import (
    AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT,
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    DIRECTION_TARGET_COUNT,
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
      G["Autoregressive heads\\nnext-bar reconstruction per intraday view"]
      H["Physical horizon head\\n6 horizons x 23 target channels"]
      I["Availability heads\\nvalidity and event-risk masks"]
      J["Direction heads\\nneutral-aware up/down logits"]
      A --> B --> C --> D --> E --> F
      F --> G
      F --> H
      F --> I
      F --> J
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


class RotaryEmbedding(nn.Module):
    inverse_frequency: torch.Tensor

    def __init__(self, head_dim: int, base: float) -> None:
        super().__init__()
        inverse = 1.0 / (float(base) ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
        self.register_buffer("inverse_frequency", inverse, persistent=False)

    def forward(self, length: int, device: torch.device, dtype: torch.dtype) -> tuple[torch.Tensor, torch.Tensor]:
        positions = torch.arange(length, device=device, dtype=torch.float32)
        angles = torch.outer(positions, self.inverse_frequency.to(device=device))
        doubled = torch.cat((angles, angles), dim=-1)[None, None, :, :]
        return doubled.cos().to(dtype=dtype), doubled.sin().to(dtype=dtype)


class CausalSelfAttention(nn.Module):
    WINDOW_QUERY_CHUNK = 256

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

    def forward(
        self, value: torch.Tensor, *, attention_window: int | None = None,
        token_mask: torch.Tensor | None = None,
        rotary: tuple[torch.Tensor, torch.Tensor] | None = None,
        window_plan: tuple[tuple[int, int, int, int, torch.Tensor], ...] | None = None,
    ) -> torch.Tensor:
        batch, length, _ = value.shape
        if token_mask is not None and token_mask.shape != (batch, length):
            raise ValueError("token_mask must have shape [B,T]")
        query = self.q_proj(value).view(batch, length, self.n_heads, self.head_dim).transpose(1, 2)
        key = self.k_proj(value).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        val = self.v_proj(value).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        query = self.q_norm(query)
        key = self.k_norm(key)
        cosine, sine = rotary or self.rope(length, value.device, value.dtype)
        query = query * cosine + _rotate_half(query) * sine
        key = key * cosine + _rotate_half(key) * sine
        grouped_query = self.n_kv_heads != self.n_heads
        # Fast path: a dense, unpadded sequence with no local-window limit can
        # delegate the lower-triangular mask directly to SDPA.  In this branch
        # ``is_causal=True`` is the sole mechanism that blocks future keys.
        if token_mask is None and (attention_window is None or int(attention_window) >= length):
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
            # Banded path: padding and/or a finite local window are combined
            # with causality inside bounded query/key bands. Every band is
            # lower triangular; its window only removes old keys and can never
            # admit a key to the right of its query.
            window = length if attention_window is None else int(attention_window)
            if window <= 0:
                raise ValueError("attention_window must be positive")
            plan = window_plan or self.build_window_plan(length, window, value.device)
            attended = self._window_attention(query, key, val, token_mask, plan)
        attended = attended.transpose(1, 2).contiguous().view(batch, length, -1)
        output = self.out_proj(attended)
        return output if token_mask is None else output * token_mask.unsqueeze(-1)

    @classmethod
    def build_window_plan(
        cls, length: int, window: int, device: torch.device,
    ) -> tuple[tuple[int, int, int, int, torch.Tensor], ...]:
        """Build exact causal bands whose score storage is O(T * (W + C))."""
        chunks: list[tuple[int, int, int, int, torch.Tensor]] = []
        for query_left in range(0, length, cls.WINDOW_QUERY_CHUNK):
            query_right = min(length, query_left + cls.WINDOW_QUERY_CHUNK)
            key_left = max(0, query_left - window + 1)
            key_right = query_right
            query_positions = torch.arange(query_left, query_right, device=device)[:, None]
            key_positions = torch.arange(key_left, key_right, device=device)[None, :]
            allowed = (
                (key_positions <= query_positions)
                & (key_positions > query_positions - window)
            )
            chunks.append((query_left, query_right, key_left, key_right, allowed))
        return tuple(chunks)

    def _window_attention(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        token_mask: torch.Tensor | None,
        plan: tuple[tuple[int, int, int, int, torch.Tensor], ...],
    ) -> torch.Tensor:
        """Exact grouped-query local attention without dense T-by-T scores."""
        batch, _heads, _length, head_dim = query.shape
        repeats = self.n_heads // self.n_kv_heads
        grouped_query = query.reshape(
            batch, self.n_kv_heads, repeats, query.shape[2], head_dim
        )
        chunks: list[torch.Tensor] = []
        scale = 1.0 / math.sqrt(head_dim)
        for query_left, query_right, key_left, key_right, base_allowed in plan:
            query_chunk = grouped_query[:, :, :, query_left:query_right]
            key_chunk = key[:, :, key_left:key_right]
            value_chunk = value[:, :, key_left:key_right]
            scores = torch.matmul(
                query_chunk,
                key_chunk.transpose(-2, -1).unsqueeze(2),
            ) * scale
            if token_mask is None:
                allowed = base_allowed
            else:
                allowed = base_allowed.unsqueeze(0) & token_mask[:, None, key_left:key_right]
                invalid_query = ~token_mask[:, query_left:query_right]
                query_positions = torch.arange(query_left, query_right, device=query.device)[:, None]
                key_positions = torch.arange(key_left, key_right, device=query.device)[None, :]
                allowed |= invalid_query[:, :, None] & (query_positions == key_positions).unsqueeze(0)
                allowed = allowed[:, None, None]
            scores = scores.masked_fill(~allowed, torch.finfo(scores.dtype).min)
            probabilities = torch.softmax(scores.float(), dim=-1).to(dtype=query.dtype)
            probabilities = F.dropout(
                probabilities,
                p=self.dropout,
                training=self.training,
            )
            chunks.append(torch.matmul(probabilities, value_chunk.unsqueeze(2)))
        attended = torch.cat(chunks, dim=3)
        return attended.reshape(batch, self.n_heads, query.shape[2], head_dim)


class SwiGLU(nn.Module):
    def __init__(self, config: BarGPTConfig) -> None:
        super().__init__()
        hidden = int(config.ff_multiplier * config.d_model)
        hidden = max(256, 256 * ((hidden + 255) // 256))
        self.gate = nn.Linear(config.d_model, hidden, bias=False)
        self.up = nn.Linear(config.d_model, hidden, bias=False)
        self.down = nn.Linear(hidden, config.d_model, bias=False)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.down(F.silu(self.gate(value)) * self.up(value))


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
        window_plan: tuple[tuple[int, int, int, int, torch.Tensor], ...] | None = None,
    ) -> torch.Tensor:
        value = value + self.attention(
            self.attention_norm(value), attention_window=attention_window,
            token_mask=token_mask, rotary=rotary, window_plan=window_plan,
        )
        value = value + self.ffn(self.ffn_norm(value))
        return value if token_mask is None else value * token_mask.unsqueeze(-1)


@dataclass(slots=True)
class BarGPTOutput:
    embeddings: torch.Tensor
    scale_embeddings: dict[str, torch.Tensor]
    autoregressive: dict[str, torch.Tensor]
    autoregressive_direction_logits: dict[str, torch.Tensor]
    latent_predictions: dict[str, torch.Tensor]
    horizon_quantiles: torch.Tensor | None
    horizon_availability_logits: torch.Tensor | None
    horizon_direction_logits: torch.Tensor | None


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


class BarGPTV1(nn.Module):
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
        self.autoregressive_direction_head = nn.Linear(config.d_model, DIRECTION_TARGET_COUNT, bias=True)
        self.latent_prediction_head = nn.Linear(config.d_model, config.d_model, bias=False)
        self.horizon_embedding = nn.Embedding(config.max_horizons, config.horizon_rank)
        self.horizon_state = nn.Linear(config.d_model, config.horizon_rank, bias=False)
        self.horizon_head = nn.Linear(
            config.horizon_rank,
            self.continuous_target_dim * len(config.quantiles),
            bias=True,
        )

        self.horizon_availability_head = nn.Linear(config.horizon_rank, AVAILABILITY_TARGET_COUNT, bias=True)
        self.horizon_direction_head = nn.Linear(config.horizon_rank, DIRECTION_TARGET_COUNT, bias=True)

    def encode(
        self,
        features: torch.Tensor,
        timeframe_us: int,
        pathway_id: int,
        *,
        attention_window: int | None = None,
        token_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if features.ndim != 3 or features.shape[-1] != self.config.feature_dim:
            raise ValueError(f"features must have shape [B,T,{self.config.feature_dim}]")
        state = self.input_projection(self.input_norm(features))
        scale = self.timeframe_embedding(timeframe_us, device=features.device, dtype=features.dtype).view(1, 1, -1)
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
        rotary = self.blocks[0].attention.rope(
            features.shape[1], features.device, features.dtype
        )
        window_plans = {
            window: CausalSelfAttention.build_window_plan(
                features.shape[1], window, features.device
            )
            for window in set(layer_windows)
            if window is not None and (token_mask is not None or window < features.shape[1])
        }
        for block, layer_window in zip(self.blocks, layer_windows, strict=True):
            state = block(
                state,
                attention_window=layer_window,
                token_mask=token_mask,
                rotary=rotary,
                window_plan=window_plans.get(layer_window),
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
        origin_mask: torch.Tensor | None = None,
        valid_origin_count: int | None = None,
        valid_origin_indices: torch.Tensor | None = None,
        asof_indices: Mapping[str, torch.Tensor] | None = None,
        valid_asof_origin_indices: Mapping[str, torch.Tensor] | None = None,
        valid_view_token_indices: Mapping[str, torch.Tensor] | None = None,
        view_masks: Mapping[str, torch.Tensor] | None = None,
        attention_windows: Mapping[str, int] | None = None,
        horizon_ids: torch.Tensor | None = None,
    ) -> BarGPTOutput:
        if origin_mask is not None and valid_origin_indices is None:
            valid_origin_indices = torch.nonzero(
                origin_mask.reshape(-1), as_tuple=False
            ).squeeze(-1)
        fused, encoded = self.embed(
            views,
            timeframe_us=timeframe_us,
            pathway_ids=pathway_ids,
            base_view=base_view,
            origin_indices=origin_indices,
            origin_mask=origin_mask,
            valid_origin_count=valid_origin_count,
            valid_origin_indices=valid_origin_indices,
            asof_indices=asof_indices,
            valid_asof_origin_indices=valid_asof_origin_indices,
            view_masks=view_masks,
            attention_windows=attention_windows,
        )
        autoregressive = {}
        autoregressive_direction_logits = {}
        latent_predictions = {}
        for name in AUTOREGRESSIVE_VIEW_NAMES:
            if name not in encoded:
                continue
            state = encoded[name]
            state_mask = None if view_masks is None else view_masks.get(name)
            state_indices = (
                None if valid_view_token_indices is None
                else valid_view_token_indices.get(name)
            )
            # Calendar views are context-only by contract and intentionally
            # have no autoregressive targets or heads.
            autoregressive[name] = torch.cat(
                (
                    self._sequence_head(
                        self.autoregressive_continuous_head, state, state_mask, state_indices
                    ),
                    self._sequence_head(
                        self.autoregressive_availability_head, state, state_mask, state_indices
                    ),
                ),
                dim=-1,
            )
            autoregressive_direction_logits[name] = self._sequence_head(
                self.autoregressive_direction_head, state, state_mask, state_indices
            )
            latent_predictions[name] = self._sequence_head(
                self.latent_prediction_head, state, state_mask, state_indices
            )
        quantiles = None
        availability_logits = None
        direction_logits = None
        if horizon_ids is not None:
            if horizon_ids.ndim != 1:
                raise ValueError("horizon_ids must have shape [H]")
            horizon_input = (
                fused
                if origin_mask is None
                else fused.reshape(-1, fused.shape[-1]).index_select(
                    0,
                    valid_origin_indices,
                )
            )
            state_rank = self.horizon_state(horizon_input).unsqueeze(-2)
            horizon_rank = self.horizon_embedding(horizon_ids.long()).view(1, 1, -1, self.config.horizon_rank)
            if origin_mask is not None:
                horizon_rank = horizon_rank.squeeze(0)
            conditioned = F.silu(state_rank + horizon_rank)
            raw = self.horizon_head(conditioned)
            raw_quantiles = raw.view(
                *horizon_input.shape[:-1],
                horizon_ids.numel(),
                self.continuous_target_dim,
                len(self.config.quantiles),
            )
            if len(self.config.quantiles) > 1:
                first = raw_quantiles[..., :1]
                quantiles = torch.cat((first, first + F.softplus(raw_quantiles[..., 1:]).cumsum(dim=-1)), dim=-1)
            else:
                quantiles = raw_quantiles
            availability_logits = self.horizon_availability_head(conditioned)
            direction_logits = self.horizon_direction_head(conditioned)
            if origin_mask is not None:
                quantiles = self._restore_origins(
                    quantiles, origin_mask, valid_origin_indices
                )
                availability_logits = self._restore_origins(
                    availability_logits, origin_mask, valid_origin_indices
                )
                direction_logits = self._restore_origins(
                    direction_logits, origin_mask, valid_origin_indices
                )
        return BarGPTOutput(
            embeddings=fused,
            scale_embeddings=encoded,
            autoregressive=autoregressive,
            autoregressive_direction_logits=autoregressive_direction_logits,
            latent_predictions=latent_predictions,
            horizon_quantiles=quantiles,
            horizon_availability_logits=availability_logits,
            horizon_direction_logits=direction_logits,
        )

    @staticmethod
    def _sequence_head(
        head: nn.Linear,
        state: torch.Tensor,
        token_mask: torch.Tensor | None,
        valid_indices: torch.Tensor | None,
    ) -> torch.Tensor:
        source = state[:, :-1]
        if token_mask is None:
            return head(source)
        active = token_mask[:, :-1]
        flattened = source.reshape(-1, source.shape[-1])
        indices = (
            valid_indices
            if valid_indices is not None
            else torch.nonzero(active.reshape(-1), as_tuple=False).squeeze(-1)
        )
        packed = head(flattened.index_select(0, indices))
        output = source.new_zeros((*source.shape[:-1], head.out_features))
        return output.reshape(-1, head.out_features).index_copy(
            0, indices, packed
        ).view_as(output)

    @staticmethod
    def _restore_origins(
        value: torch.Tensor,
        origin_mask: torch.Tensor,
        valid_origin_indices: torch.Tensor | None,
    ) -> torch.Tensor:
        output = value.new_zeros((*origin_mask.shape, *value.shape[1:]))
        indices = (
            valid_origin_indices
            if valid_origin_indices is not None
            else torch.nonzero(origin_mask.reshape(-1), as_tuple=False).squeeze(-1)
        )
        return output.flatten(0, 1).index_copy(0, indices, value).view_as(output)

    def embed(
        self,
        views: Mapping[str, torch.Tensor],
        *,
        timeframe_us: Mapping[str, int],
        pathway_ids: Mapping[str, int],
        base_view: str,
        origin_indices: torch.Tensor,
        origin_mask: torch.Tensor | None = None,
        valid_origin_count: int | None = None,
        valid_origin_indices: torch.Tensor | None = None,
        asof_indices: Mapping[str, torch.Tensor] | None = None,
        valid_asof_origin_indices: Mapping[str, torch.Tensor] | None = None,
        view_masks: Mapping[str, torch.Tensor] | None = None,
        attention_windows: Mapping[str, int] | None = None,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        if base_view not in views:
            raise KeyError(f"base view {base_view!r} is absent")
        if attention_windows is not None:
            missing = sorted(set(views) - set(attention_windows))
            if missing:
                raise KeyError(f"missing attention windows for views: {missing}")
        encoded = {
            name: self.encode(
                value,
                timeframe_us[name],
                pathway_ids[name],
                attention_window=None if attention_windows is None else int(attention_windows[name]),
                token_mask=None if view_masks is None else view_masks.get(name),
            )
            for name, value in views.items()
        }
        fused_full = self._gather_sequence(encoded[base_view], origin_indices)
        if origin_mask is not None and origin_mask.shape != origin_indices.shape:
            raise ValueError("origin_mask must have the same shape as origin_indices")
        if origin_mask is not None and valid_origin_indices is None:
            valid_origin_indices = torch.nonzero(
                origin_mask.reshape(-1), as_tuple=False
            ).squeeze(-1)
        fused = (
            fused_full
            if origin_mask is None
            else fused_full.reshape(-1, fused_full.shape[-1]).index_select(
                0, valid_origin_indices
            )
        )
        packed_origin_count = (
            int(valid_origin_count) if valid_origin_count is not None else None
        )
        for name, state in encoded.items():
            if name == base_view:
                continue
            if asof_indices is None or name not in asof_indices:
                raise KeyError(f"missing causal as-of index for {name!r}")
            available = asof_indices[name] >= 0
            coarse = self._gather_sequence(state, asof_indices[name])
            if origin_mask is not None:
                available = available[origin_mask]
                coarse = coarse[origin_mask]
            active = (
                None
                if valid_asof_origin_indices is None
                else valid_asof_origin_indices.get(name)
            )
            if (
                origin_mask is not None
                and packed_origin_count is not None
                and active is not None
                and int(active.shape[0]) < packed_origin_count
            ):
                selected_fused = fused.index_select(0, active)
                selected_coarse = coarse.index_select(0, active)
                gate = self.scale_gate(
                    torch.cat((selected_fused, selected_coarse), dim=-1)
                )
                candidate = selected_fused + gate * selected_coarse
                fused = fused.index_copy(0, active, candidate)
            else:
                gate = self.scale_gate(torch.cat((fused, coarse), dim=-1))
                candidate = fused + gate * coarse
                fused = torch.where(available.unsqueeze(-1), candidate, fused)
        if origin_mask is not None:
            fused = self._restore_origins(fused, origin_mask, valid_origin_indices)
        return fused, encoded
