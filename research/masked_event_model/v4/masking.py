from __future__ import annotations

from dataclasses import dataclass

import torch

from research.masked_event_model.v4.config import MaskConfig


@dataclass(slots=True)
class ByteMaskBatch:
    header_mask: torch.Tensor
    event_mask: torch.Tensor

    @property
    def masked_header_count(self) -> int:
        return int(self.header_mask.sum().item())

    @property
    def masked_event_count(self) -> int:
        return int(self.event_mask.sum().item())


def build_byte_masks(header_uint8: torch.Tensor, events_uint8: torch.Tensor, config: MaskConfig) -> ByteMaskBatch:
    header_probability = max(0.0, min(1.0, float(config.header_mask_ratio)))
    event_probability = max(0.0, min(1.0, float(config.mask_ratio)))
    header_mask = torch.rand(header_uint8.shape, device=header_uint8.device) < header_probability
    event_mask = torch.rand(events_uint8.shape, device=events_uint8.device) < event_probability
    if config.min_masked_bytes > 0:
        header_mask, event_mask = ensure_minimum_mask(header_mask, event_mask, int(config.min_masked_bytes))
    return ByteMaskBatch(header_mask=header_mask, event_mask=event_mask)


def ensure_minimum_mask(header_mask: torch.Tensor, event_mask: torch.Tensor, minimum: int) -> tuple[torch.Tensor, torch.Tensor]:
    batch = header_mask.shape[0]
    flat = torch.cat([header_mask.reshape(batch, -1), event_mask.reshape(batch, -1)], dim=1)
    counts = flat.sum(dim=1)
    missing_rows = (counts < minimum).nonzero(as_tuple=False).flatten()
    if missing_rows.numel() == 0:
        return header_mask, event_mask
    total_positions = flat.shape[1]
    random_positions = torch.randint(0, total_positions, (missing_rows.numel(), minimum), device=flat.device)
    flat[missing_rows[:, None], random_positions] = True
    header_count = header_mask[0].numel()
    return flat[:, :header_count].reshape_as(header_mask), flat[:, header_count:].reshape_as(event_mask)
