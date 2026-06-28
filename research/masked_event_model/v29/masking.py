from __future__ import annotations

import random
from dataclasses import dataclass

import torch

from research.masked_event_model.v29.config import MaskConfig


@dataclass(slots=True)
class EventMaskBatch:
    """Per-sample event indices used to split context into visible and target sets.

    B = batch size, E = total event records, V = visible records, M = masked records.
    """

    # Shape: [B, V]. Sorted event positions that remain visible to the encoder.
    visible_event_indices: torch.Tensor
    # Shape: [B, M]. Sorted event positions removed from encoder and reconstructed.
    masked_event_indices: torch.Tensor
    # Scalar V. Number of visible event records per sample.
    visible_count: int
    # Scalar M. Number of masked event records per sample.
    masked_count: int
    # Scalar E. Total event records per compact sample.
    event_count: int
    # Scalar. Mask ratio sampled or configured before integer rounding.
    requested_mask_ratio: float
    # Scalar. Effective `M / E` mask ratio after rounding.
    actual_mask_ratio: float
    # Scalar. Numeric identifier for the sampled masking policy.
    mask_policy_id: int
    # Scalar string label for the sampled masking policy.
    mask_policy_name: str


@dataclass(slots=True)
class HeaderBitMaskBatch:
    """Batch-shared header-bit split used by the pooled header token.

    H = 14 * 8 compact header bits, VH = visible bits, HM = masked bits.
    """

    # Shape: [VH]. Original header-bit positions that remain visible.
    visible_header_bit_indices: torch.Tensor
    # Shape: [HM]. Original header-bit positions removed from the header token
    # and reconstructed by the header decoder.
    masked_header_bit_indices: torch.Tensor
    # Scalar VH. Number of header bits pooled into the encoder header token.
    visible_count: int
    # Scalar HM. Number of header bits reconstructed by the decoder.
    masked_count: int
    # Scalar H. Total compact header bits.
    header_bit_count: int
    # Scalar. Requested header mask ratio before integer rounding.
    requested_mask_ratio: float
    # Scalar. Effective `HM / H` ratio after rounding.
    actual_mask_ratio: float


def build_event_masks(events_uint8: torch.Tensor, config: MaskConfig) -> EventMaskBatch:
    """Choose event records to remove from the encoder and reconstruct later.

    Masking happens at the event-record level here, not at the byte level. The
    encoder sees a shorter sequence of intact visible events. The decoder gets
    the exported chunk embedding plus decoder-only position embeddings at the
    masked event indices and predicts only those masked events.
    """

    if events_uint8.ndim != 3:
        raise ValueError(f"Expected events_uint8 [B,E,16], got {tuple(events_uint8.shape)}")
    batch_size, event_count, _ = events_uint8.shape
    mask_ratio, policy_id, policy_name = sample_event_mask_ratio(config, device=events_uint8.device)
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
        event_count=int(event_count),
        requested_mask_ratio=float(mask_ratio),
        actual_mask_ratio=float(masked_count / max(1, event_count)),
        mask_policy_id=int(policy_id),
        mask_policy_name=policy_name,
    )


def build_header_bit_masks(header_uint8: torch.Tensor, config: MaskConfig) -> HeaderBitMaskBatch:
    """Choose one batch-shared set of header bit positions to remove.

    The transformer still receives one pooled header token. Only the contents of
    that token change: visible header bits are pooled with learned bit-position
    embeddings, while masked header bits become reconstruction targets.
    """

    if header_uint8.ndim != 2:
        raise ValueError(f"Expected header_uint8 [B,14], got {tuple(header_uint8.shape)}")
    header_bit_count = int(header_uint8.shape[1] * 8)
    mask_ratio = min(max(float(config.header_mask_ratio), 0.0), 0.99)
    masked_count = max(int(config.min_masked_header_bits), int(round(header_bit_count * mask_ratio)))
    masked_count = min(max(1, masked_count), header_bit_count - 1)
    visible_count = header_bit_count - masked_count

    scores = torch.rand((header_bit_count,), device=header_uint8.device)
    visible = torch.topk(scores, k=visible_count, largest=False).indices.sort().values
    masked = torch.topk(scores, k=masked_count, largest=True).indices.sort().values
    return HeaderBitMaskBatch(
        visible_header_bit_indices=visible,
        masked_header_bit_indices=masked,
        visible_count=int(visible_count),
        masked_count=int(masked_count),
        header_bit_count=int(header_bit_count),
        requested_mask_ratio=float(mask_ratio),
        actual_mask_ratio=float(masked_count / max(1, header_bit_count)),
    )


def sample_event_mask_ratio(config: MaskConfig, *, device: torch.device) -> tuple[float, int, str]:
    """Sample one event-mask ratio for the whole batch.

    The model needs rectangular tensors, so a batch uses one visible/masked
    count. The default mixed schedule gives the encoder production-like dense
    contexts sometimes, sparse contexts sometimes, and the original heavy MAE
    masking most of the time. Even the zero-mask branch is still clamped by
    `min_masked_events` later so the BCE reconstruction objective has a target.
    """

    schedule = str(getattr(config, "event_mask_schedule", "fixed")).lower()
    if schedule != "mixed":
        return min(max(float(config.event_mask_ratio), 0.0), 0.99), -1, "fixed"

    high_probability = max(0.0, float(config.event_mask_high_probability))
    zero_probability = max(0.0, float(config.event_mask_zero_probability))
    low_probability = max(0.0, float(config.event_mask_low_probability))
    total = high_probability + zero_probability + low_probability
    if total <= 0.0:
        return min(max(float(config.event_mask_ratio), 0.0), 0.99), -1, "fixed"

    # Draw scalar schedule choices with Python RNG. This avoids tensor `.item()`
    # in the mask-construction path, which can otherwise show up as a Dynamo
    # graph-break candidate if the caller later compiles a wider train step.
    draw = random.random() * total
    if draw < high_probability:
        ratio = uniform_ratio(config.event_mask_high_min, config.event_mask_high_max, device=device)
        return ratio, 2, "high"
    if draw < high_probability + zero_probability:
        return 0.0, 0, "zero"
    ratio = uniform_ratio(config.event_mask_low_min, config.event_mask_low_max, device=device)
    return ratio, 1, "low"


def uniform_ratio(low: float, high: float, *, device: torch.device) -> float:
    low = min(max(float(low), 0.0), 0.99)
    high = min(max(float(high), 0.0), 0.99)
    if high < low:
        low, high = high, low
    return random.random() * (high - low) + low


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
