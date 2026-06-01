from __future__ import annotations

import torch

from research.masked_event_model.v4.config import LossConfig, MaskConfig, ModelConfig
from research.masked_event_model.v4.losses import masked_byte_bce_loss
from research.masked_event_model.v4.masking import build_byte_masks
from research.masked_event_model.v4.model import CompactByteMaskedAutoencoder


def test_forward_and_encode_shapes() -> None:
    batch = 2
    events = 16
    header = torch.randint(0, 256, (batch, 14), dtype=torch.uint8)
    event_bytes = torch.randint(0, 256, (batch, events, 16), dtype=torch.uint8)
    model = CompactByteMaskedAutoencoder(
        events_per_chunk=events,
        config=ModelConfig(d_byte=8, d_model=32, embedding_dim=8, n_heads=4, encoder_layers=1, decoder_layers=1),
    )
    masks = build_byte_masks(header, event_bytes, MaskConfig(mask_ratio=0.5, header_mask_ratio=0.5))
    output = model(header, event_bytes, masks)
    assert output.header_bit_logits.shape[-1] == 8
    assert output.event_bit_logits.shape[-1] == 8
    result = masked_byte_bce_loss(output, header, event_bytes, masks, LossConfig(), include_diagnostics=True)
    assert torch.isfinite(result.loss)
    embedding = model.encode(header, event_bytes)
    assert embedding.shape == (batch, 8)
    event_embedding = model.encode_events(header, event_bytes)
    assert event_embedding.shape == (batch, events, 8)


if __name__ == "__main__":
    test_forward_and_encode_shapes()
    print("v4 smoke passed")
