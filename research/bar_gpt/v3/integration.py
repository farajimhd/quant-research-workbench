from __future__ import annotations

import torch
from torch import nn


class PackedBarEmbeddingAdapter(nn.Module):
    """Project frozen or fine-tuned BarGPT origin states into a packed-model modality width."""

    def __init__(self, bar_width: int, packed_width: int, *, dropout: float = 0.0) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(bar_width)
        self.projection = nn.Linear(bar_width, packed_width, bias=False)
        self.dropout = nn.Dropout(dropout)
        self.missing_embedding = nn.Parameter(torch.zeros(packed_width))

    def forward(self, embeddings: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if embeddings.ndim != 3 or valid.shape != embeddings.shape[:2]:
            raise ValueError("BarGPT embeddings must be [B,T,D] with a matching [B,T] validity mask")
        projected = self.dropout(self.projection(self.norm(embeddings)))
        projected = torch.where(valid.unsqueeze(-1), projected, self.missing_embedding.view(1, 1, -1))
        return projected, valid
