from __future__ import annotations

import sys
from pathlib import Path

import torch
import numpy as np


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v4.config import LossConfig, MaskConfig, ModelConfig
from research.masked_event_model.v4.losses import masked_byte_bce_loss
from research.masked_event_model.v4.masking import build_byte_masks
from research.masked_event_model.v4.model import CompactByteMaskedAutoencoder
from research.mlops.clickhouse_events import DEFAULT_CONTEXT_EVENTS, EVENT_ROW_DTYPE, encode_unified_event_window


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
    assert output.header_bit_probs.shape[-1] == 8
    assert output.event_bit_probs.shape[-1] == 8
    assert float(output.header_bit_probs.detach().min()) >= 0.0
    assert float(output.header_bit_probs.detach().max()) <= 1.0
    assert float(output.event_bit_probs.detach().min()) >= 0.0
    assert float(output.event_bit_probs.detach().max()) <= 1.0
    result = masked_byte_bce_loss(output, header, event_bytes, masks, LossConfig(), include_diagnostics=True)
    assert torch.isfinite(result.loss)
    embedding = model.encode(header, event_bytes)
    assert embedding.shape == (batch, 8)
    event_embedding = model.encode_events(header, event_bytes)
    assert event_embedding.shape == (batch, events, 8)


def test_final_events_schema_encoder_shapes() -> None:
    rows = np.zeros((DEFAULT_CONTEXT_EVENTS,), dtype=EVENT_ROW_DTYPE)
    rows["ordinal"] = np.arange(DEFAULT_CONTEXT_EVENTS, dtype=np.uint64)
    rows["event_type"] = 0
    rows["sip_timestamp_us"] = 1_700_000_000_000_000 + np.arange(DEFAULT_CONTEXT_EVENTS, dtype=np.uint64) * 500
    rows["price_primary_int"] = 10_010
    rows["price_secondary_int"] = 10_000
    rows["size_primary"] = 100.0
    rows["size_secondary"] = 200.0
    rows["exchange_primary"] = 1
    rows["exchange_secondary"] = 2
    rows["event_flags"] = 0
    rows["conditions_packed"] = 0x04030201
    rows["event_type"][::5] = 1
    rows["price_primary_int"][::5] = 10_005
    rows["price_secondary_int"][::5] = 0
    rows["size_primary"][::5] = 50.0
    rows["size_secondary"][::5] = 0.0
    rows["exchange_primary"][::5] = 3
    encoded = encode_unified_event_window(rows)
    assert not isinstance(encoded, str)
    header, events = encoded
    assert header.shape == (14,)
    assert events.shape == (DEFAULT_CONTEXT_EVENTS, 16)
    assert int(header[11]) == int(np.count_nonzero(rows["event_type"] == 0))
    assert int(header[12]) == int(np.count_nonzero(rows["event_type"] == 1))
    assert bytes(events[0, 12:16]) == int(0x04030201).to_bytes(4, "little")


if __name__ == "__main__":
    test_forward_and_encode_shapes()
    test_final_events_schema_encoder_shapes()
    print("v4 smoke passed")
