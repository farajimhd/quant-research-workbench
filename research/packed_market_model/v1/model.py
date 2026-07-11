from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn
import torch.nn.functional as F

from research.packed_market_model.v1.config import ModelConfig


@dataclass(slots=True)
class PackedModelOutput:
    label_predictions: dict[str, torch.Tensor]
    origin_embeddings: torch.Tensor
    profile: dict[str, float]


class CausalConvBlock(nn.Module):
    def __init__(self, d_model: int, kernel_size: int, dropout: float) -> None:
        super().__init__()
        self.kernel_size = int(kernel_size)
        self.norm = nn.LayerNorm(d_model)
        self.depthwise = nn.Conv1d(d_model, d_model, kernel_size=self.kernel_size, groups=d_model)
        self.pointwise = nn.Sequential(
            nn.GELU(),
            nn.Conv1d(d_model, d_model * 4, kernel_size=1),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Conv1d(d_model * 4, d_model, kernel_size=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        x = self.norm(x)
        y = x.transpose(0, 1).unsqueeze(0)
        y = F.pad(y, (self.kernel_size - 1, 0))
        y = self.depthwise(y)
        y = self.pointwise(y)
        y = y.squeeze(0).transpose(0, 1)
        return residual + y


class PackedMarketModelV1(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        event_dim = int(config.event_feature_dim or len(config.event_feature_names) or 1)
        d_model = int(config.d_model)
        self.event_input = nn.Sequential(
            nn.LayerNorm(event_dim),
            nn.Linear(event_dim, d_model),
            nn.GELU(),
            nn.Linear(d_model, d_model),
        )
        if bool(config.use_position_embedding):
            self.position_embedding = nn.Embedding(int(config.max_position_embeddings), d_model)
        else:
            self.position_embedding = None
        self.event_blocks = nn.ModuleList(
            CausalConvBlock(d_model, int(config.event_kernel_size), float(config.event_dropout))
            for _ in range(int(config.event_layers))
        )
        self.event_norm = nn.LayerNorm(d_model)
        self.heads = nn.ModuleDict(
            {
                name: nn.Sequential(
                    nn.LayerNorm(d_model),
                    nn.Linear(d_model, int(config.head_hidden_dim)),
                    nn.GELU(),
                    nn.Linear(int(config.head_hidden_dim), 1),
                )
                for name in config.label_names
            }
        )

    def forward(self, x: dict[str, Any]) -> PackedModelOutput:
        events = x["events"]
        positions = x["origin_positions"].long().clamp(min=0, max=max(int(events.shape[0]) - 1, 0))
        hidden = self.event_input(events)
        if self.position_embedding is not None:
            pos = torch.arange(hidden.shape[0], device=hidden.device, dtype=torch.long)
            pos = pos.clamp(max=self.position_embedding.num_embeddings - 1)
            hidden = hidden + self.position_embedding(pos)
        for block in self.event_blocks:
            hidden = block(hidden)
        hidden = self.event_norm(hidden)
        origin_hidden = hidden.index_select(0, positions)
        predictions = {name: head(origin_hidden).squeeze(-1) for name, head in self.heads.items()}
        return PackedModelOutput(label_predictions=predictions, origin_embeddings=origin_hidden, profile={})

    def forward_with_timings(self, x: dict[str, Any], *, sync_cuda: bool = False) -> PackedModelOutput:
        start = time.perf_counter()
        output = self.forward(x)
        if sync_cuda and output.origin_embeddings.is_cuda:
            torch.cuda.synchronize()
        output.profile["model_forward_seconds"] = time.perf_counter() - start
        return output


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  events["Packed event stream [T,F]"] --> proj["Event projection MLP"]',
            '  proj --> conv["Causal Conv Event Encoder"]',
            '  origins["origin_positions [M]"] --> gather["Gather origin states"]',
            "  conv --> gather",
            '  gather --> heads["Per-label prediction heads"]',
            '  heads --> loss["Grouped loss over all origins in block"]',
        ]
    )
