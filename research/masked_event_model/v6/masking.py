from __future__ import annotations

from dataclasses import dataclass

import torch

from research.masked_event_model.v6.config import MaskConfig


@dataclass(slots=True)
class EventMaskBatch:
    """Per-sample event indices used to split context into visible and target sets."""

    visible_event_indices: torch.Tensor
    masked_event_indices: torch.Tensor
    visible_count: int
    masked_count: int


def build_event_masks(events_uint8: torch.Tensor, config: MaskConfig) -> EventMaskBatch:
    """Choose event records to remove from the encoder and reconstruct later.

    Masking happens at the event-record level here, not at the byte level. The
    encoder sees a shorter sequence of intact visible events; the decoder gets
    learned queries at the masked event positions and predicts their 16 bytes.
    """

    if events_uint8.ndim != 3:
        raise ValueError(f"Expected events_uint8 [B,E,16], got {tuple(events_uint8.shape)}")
    batch_size, event_count, _ = events_uint8.shape
    mask_ratio = min(max(float(config.event_mask_ratio), 0.0), 0.99)
    masked_count = max(int(config.min_masked_events), int(round(event_count * mask_ratio)))
    masked_count = min(max(1, masked_count), event_count - 1)
    visible_count = event_count - masked_count

    scores = torch.rand((batch_size, event_count), device=events_uint8.device)
    visible = torch.topk(scores, k=visible_count, dim=1, largest=False).indices.sort(dim=1).values
    masked = torch.topk(scores, k=masked_count, dim=1, largest=True).indices.sort(dim=1).values
    return EventMaskBatch(
        visible_event_indices=visible,
        masked_event_indices=masked,
        visible_count=int(visible_count),
        masked_count=int(masked_count),
    )


def maybe_corrupt_header(header_uint8: torch.Tensor, config: MaskConfig) -> torch.Tensor:
    """Apply low-rate bit flips to the header without removing the header token."""

    return maybe_xor_corrupt_uint8(
        header_uint8,
        sample_probability=float(config.header_bit_corruption_prob),
        bit_probability=float(config.header_bit_corruption_ratio),
    )


def maybe_corrupt_visible_events(visible_events_uint8: torch.Tensor, config: MaskConfig) -> torch.Tensor:
    """Regularize visible event tokens with optional bit flips after masking."""

    return maybe_xor_corrupt_uint8(
        visible_events_uint8,
        sample_probability=float(config.event_bit_corruption_prob),
        bit_probability=float(config.event_bit_corruption_ratio),
    )


def maybe_xor_corrupt_uint8(values: torch.Tensor, *, sample_probability: float, bit_probability: float) -> torch.Tensor:
    """Flip random bits using XOR so the tensor stays packed as uint8 until projection."""

    sample_probability = min(max(float(sample_probability), 0.0), 1.0)
    bit_probability = min(max(float(bit_probability), 0.0), 1.0)
    if sample_probability <= 0.0 or bit_probability <= 0.0:
        return values
    batch_size = int(values.shape[0])
    device = values.device
    sample_gate = torch.rand((batch_size,), device=device) < sample_probability
    if not bool(sample_gate.any()):
        return values

    bit_shape = (*values.shape, 8)
    bit_mask = torch.rand(bit_shape, device=device) < bit_probability
    view_shape = (batch_size,) + (1,) * (values.ndim - 1) + (1,)
    bit_mask = bit_mask & sample_gate.view(view_shape)
    shifts = torch.arange(8, device=device, dtype=torch.long)
    xor_mask = ((bit_mask.to(torch.long) << shifts).sum(dim=-1)).to(torch.uint8)
    return torch.bitwise_xor(values, xor_mask)


def gather_events(events_uint8: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
    """Batch gather event rows with shape preservation for `[B, selected, 16]`."""

    gather_index = indices.unsqueeze(-1).expand(-1, -1, events_uint8.shape[-1])
    return torch.gather(events_uint8, dim=1, index=gather_index)
