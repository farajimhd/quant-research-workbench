from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import torch
import torch.nn.functional as F
from torch import nn

from research.bar_gpt.v1.config import BarGPTConfig
from research.bar_gpt.v1.targets import AVAILABILITY_TARGET_COUNT


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = float(eps)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        normalized = value.float() * torch.rsqrt(value.float().pow(2).mean(dim=-1, keepdim=True) + self.eps)
        return normalized.to(dtype=value.dtype) * self.weight


def _rotate_half(value: torch.Tensor) -> torch.Tensor:
    first, second = value.chunk(2, dim=-1)
    return torch.cat((-second, first), dim=-1)


class RotaryEmbedding(nn.Module):
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

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        batch, length, _ = value.shape
        query = self.q_proj(value).view(batch, length, self.n_heads, self.head_dim).transpose(1, 2)
        key = self.k_proj(value).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        val = self.v_proj(value).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        query = self.q_norm(query)
        key = self.k_norm(key)
        cosine, sine = self.rope(length, value.device, value.dtype)
        query = query * cosine + _rotate_half(query) * sine
        key = key * cosine + _rotate_half(key) * sine
        if self.n_kv_heads != self.n_heads:
            repeats = self.n_heads // self.n_kv_heads
            key = key.repeat_interleave(repeats, dim=1)
            val = val.repeat_interleave(repeats, dim=1)
        attended = F.scaled_dot_product_attention(
            query,
            key,
            val,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=True,
        )
        attended = attended.transpose(1, 2).contiguous().view(batch, length, -1)
        return self.out_proj(attended)


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

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value = value + self.attention(self.attention_norm(value))
        return value + self.ffn(self.ffn_norm(value))


@dataclass(slots=True)
class BarGPTOutput:
    embeddings: torch.Tensor
    scale_embeddings: dict[str, torch.Tensor]
    autoregressive: dict[str, torch.Tensor]
    latent_predictions: dict[str, torch.Tensor]
    horizon_quantiles: torch.Tensor | None
    horizon_availability_logits: torch.Tensor | None


class ContinuousTimeframeEmbedding(nn.Module):
    """Encode physical duration so the shared decoder can generalize beyond a fixed scale vocabulary."""

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
        self.continuous_target_dim = config.target_dim - AVAILABILITY_TARGET_COUNT
        if self.continuous_target_dim <= 0:
            raise ValueError("target_dim must leave room for continuous and availability targets")
        self.autoregressive_continuous_head = nn.Linear(config.d_model, self.continuous_target_dim, bias=False)
        self.autoregressive_availability_head = nn.Linear(config.d_model, AVAILABILITY_TARGET_COUNT, bias=True)
        self.latent_prediction_head = nn.Linear(config.d_model, config.d_model, bias=False)
        self.horizon_embedding = nn.Embedding(config.max_horizons, config.horizon_rank)
        self.horizon_state = nn.Linear(config.d_model, config.horizon_rank, bias=False)
        self.horizon_head = nn.Linear(
            config.horizon_rank,
            self.continuous_target_dim * len(config.quantiles),
            bias=True,
        )

        self.horizon_availability_head = nn.Linear(config.horizon_rank, AVAILABILITY_TARGET_COUNT, bias=True)

    def encode(self, features: torch.Tensor, timeframe_us: int, pathway_id: int) -> torch.Tensor:
        if features.ndim != 3 or features.shape[-1] != self.config.feature_dim:
            raise ValueError(f"features must have shape [B,T,{self.config.feature_dim}]")
        state = self.input_projection(self.input_norm(features))
        scale = self.timeframe_embedding(timeframe_us, device=features.device, dtype=features.dtype).view(1, 1, -1)
        pathway = self.pathway_embedding.weight[int(pathway_id)].view(1, 1, -1)
        state = state + scale + pathway
        for block in self.blocks:
            state = block(state)
        return self.output_norm(state)

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
        horizon_ids: torch.Tensor | None = None,
    ) -> BarGPTOutput:
        fused, encoded = self.embed(
            views,
            timeframe_us=timeframe_us,
            pathway_ids=pathway_ids,
            base_view=base_view,
            origin_indices=origin_indices,
            asof_indices=asof_indices,
        )
        autoregressive = {}
        latent_predictions = {}
        for name, state in encoded.items():
            if state.shape[1] > 1:
                autoregressive[name] = torch.cat(
                    (self.autoregressive_continuous_head(state[:, :-1]), self.autoregressive_availability_head(state[:, :-1])),
                    dim=-1,
                )
                latent_predictions[name] = self.latent_prediction_head(state[:, :-1])
        quantiles = None
        availability_logits = None
        if horizon_ids is not None:
            if horizon_ids.ndim != 1:
                raise ValueError("horizon_ids must have shape [H]")
            state_rank = self.horizon_state(fused).unsqueeze(2)
            horizon_rank = self.horizon_embedding(horizon_ids.long()).view(1, 1, -1, self.config.horizon_rank)
            conditioned = F.silu(state_rank + horizon_rank)
            raw = self.horizon_head(conditioned)
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
            availability_logits = self.horizon_availability_head(conditioned)
        return BarGPTOutput(
            embeddings=fused,
            scale_embeddings=encoded,
            autoregressive=autoregressive,
            latent_predictions=latent_predictions,
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
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        if base_view not in views:
            raise KeyError(f"base view {base_view!r} is absent")
        encoded = {name: self.encode(value, timeframe_us[name], pathway_ids[name]) for name, value in views.items()}
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
