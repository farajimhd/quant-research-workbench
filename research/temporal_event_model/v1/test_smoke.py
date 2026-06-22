from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from research.temporal_event_model.v1.cache_probe import PRICE_TARGET_BITS_PER_HORIZON
from research.temporal_event_model.v1.model import SingleChunkFutureLabelPredictor


class DummyEventEncoder(nn.Module):
    def __init__(self, embedding_dim: int) -> None:
        super().__init__()
        self.projection = nn.Linear(14 + 128 * 16, embedding_dim)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        values = torch.cat([header_uint8.float(), events_uint8.float().flatten(1)], dim=1) / 255.0
        return self.projection(values)


def main() -> None:
    batch_size = 2
    target_chunks = 2
    embedding_dim = 32
    model = SingleChunkFutureLabelPredictor(
        event_encoder=DummyEventEncoder(embedding_dim),
        embedding_dim=embedding_dim,
        hidden_dim=64,
        target_chunks=target_chunks,
        target_bits=PRICE_TARGET_BITS_PER_HORIZON,
        dropout=0.0,
    )
    header = torch.randint(0, 256, (batch_size, 14), dtype=torch.uint8)
    events = torch.randint(0, 256, (batch_size, 128, 16), dtype=torch.uint8)
    output = model(header, events)
    assert output.chunk_embedding.shape == (batch_size, embedding_dim)
    assert output.price_target_logits.shape == (batch_size, target_chunks, PRICE_TARGET_BITS_PER_HORIZON)
    loss = torch.nn.functional.binary_cross_entropy_with_logits(output.price_target_logits, torch.zeros_like(output.price_target_logits))
    assert torch.isfinite(loss)
    print("temporal_event_model/v1 smoke passed", flush=True)


if __name__ == "__main__":
    main()
