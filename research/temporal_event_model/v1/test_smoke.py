from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from research.temporal_event_model.v1.config import LossConfig, ModelConfig
from research.temporal_event_model.v1.losses import temporal_next_chunk_loss
from research.temporal_event_model.v1.model import TemporalEventPredictor


class DummyEventEncoder(nn.Module):
    def __init__(self, embedding_dim: int) -> None:
        super().__init__()
        self.projection = nn.Linear(14 + 128 * 16, embedding_dim)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        values = torch.cat([header_uint8.float(), events_uint8.float().flatten(1)], dim=1) / 255.0
        return self.projection(values)


def main() -> None:
    batch_size = 2
    context_chunks = 16
    target_chunks = 1
    embedding_dim = 32
    model = TemporalEventPredictor(
        event_encoder=DummyEventEncoder(embedding_dim),
        config=ModelConfig(embedding_dim=embedding_dim, temporal_d_model=64, temporal_layers=1, temporal_heads=4, decoder_layers=1),
        context_chunks=context_chunks,
        target_chunks=target_chunks,
    )
    context_header = torch.randint(0, 256, (batch_size, context_chunks, 14), dtype=torch.uint8)
    context_events = torch.randint(0, 256, (batch_size, context_chunks, 128, 16), dtype=torch.uint8)
    target_header = torch.randint(0, 256, (batch_size, target_chunks, 14), dtype=torch.uint8)
    target_events = torch.randint(0, 256, (batch_size, target_chunks, 128, 16), dtype=torch.uint8)
    output = model(context_header, context_events)
    assert output.chunk_embeddings.shape == (batch_size, context_chunks, embedding_dim)
    assert output.header_bit_logits.shape == (batch_size, target_chunks, 14, 8)
    assert output.event_bit_logits.shape == (batch_size, target_chunks, 128, 16, 8)
    loss_config = LossConfig()
    assert loss_config.header_weight > loss_config.event_weight
    loss = temporal_next_chunk_loss(output, target_header, target_events, loss_config, detailed=True)
    assert torch.isfinite(loss.loss)
    print("temporal_event_model/v1 smoke passed", flush=True)


if __name__ == "__main__":
    main()
