from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.temporal_event_model.v1.config import LossConfig
from research.temporal_event_model.v1.model import TemporalEventOutput


BYTE_VALUE_BIT_WEIGHTS = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.float32)
MAX_SEMANTIC_BIT_WEIGHT = float(BYTE_VALUE_BIT_WEIGHTS[-1])
HEADER_BYTES = 14
EVENT_BYTES = 16
BYTE_MAX_VALUE = 255.0
PSNR_EPSILON = 1e-12


@dataclass(slots=True)
class TemporalLossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def build_semantic_event_bit_weights() -> torch.Tensor:
    numeric = BYTE_VALUE_BIT_WEIGHTS.tolist()
    categorical = [MAX_SEMANTIC_BIT_WEIGHT] * 8
    return torch.tensor(
        [
            categorical,
            numeric,
            numeric,
            numeric,
            numeric,
            numeric,
            numeric,
            numeric,
            numeric,
            categorical,
            categorical,
            categorical,
            categorical,
            categorical,
            categorical,
            categorical,
        ],
        dtype=torch.float32,
    )


SEMANTIC_EVENT_BIT_WEIGHTS = build_semantic_event_bit_weights()


def temporal_next_chunk_loss(
    output: TemporalEventOutput,
    target_header_uint8: torch.Tensor,
    target_events_uint8: torch.Tensor,
    config: LossConfig,
    *,
    detailed: bool = False,
) -> TemporalLossResult:
    target_header_bits = unpack_bits(target_header_uint8.long()).to(device=output.header_bit_logits.device, dtype=output.header_bit_logits.dtype)
    target_event_bits = unpack_bits(target_events_uint8.long()).to(device=output.event_bit_logits.device, dtype=output.event_bit_logits.dtype)
    semantic_weights = (
        SEMANTIC_EVENT_BIT_WEIGHTS.to(device=output.event_bit_logits.device, dtype=output.event_bit_logits.dtype).view(1, 1, 1, EVENT_BYTES, 8)
        / BYTE_VALUE_BIT_WEIGHTS.to(device=output.event_bit_logits.device, dtype=output.event_bit_logits.dtype).sum()
    )
    with torch.amp.autocast("cuda", enabled=False):
        header_loss = F.binary_cross_entropy_with_logits(output.header_bit_logits.float(), target_header_bits.float())
        event_loss = F.binary_cross_entropy_with_logits(
            output.event_bit_logits.float(),
            target_event_bits.float(),
            weight=semantic_weights.float(),
        )
        loss = float(config.header_weight) * header_loss + float(config.event_weight) * event_loss
    metrics = {
        "temporal/loss_total": float(loss.detach().cpu()),
        "temporal/loss_header": float(header_loss.detach().cpu()),
        "temporal/loss_event": float(event_loss.detach().cpu()),
    }
    with torch.no_grad():
        event_probs = torch.sigmoid(output.event_bit_logits.float())
        header_probs = torch.sigmoid(output.header_bit_logits.float())
        event_hard = event_probs >= 0.5
        header_hard = header_probs >= 0.5
        target_event_bool = target_event_bits.bool()
        target_header_bool = target_header_bits.bool()
        metrics.update(
            {
                "temporal/event_bit_acc_pct": float((event_hard == target_event_bool).float().mean().detach().cpu() * 100.0),
                "temporal/header_bit_acc_pct": float((header_hard == target_header_bool).float().mean().detach().cpu() * 100.0),
                "temporal/event_byte_exact_acc_pct": float((pack_bits(event_hard) == target_events_uint8.long()).float().mean().detach().cpu() * 100.0),
                "temporal/header_byte_exact_acc_pct": float((pack_bits(header_hard) == target_header_uint8.long()).float().mean().detach().cpu() * 100.0),
            }
        )
        if detailed:
            event_soft_bytes = (event_probs * BYTE_VALUE_BIT_WEIGHTS.to(event_probs.device)).sum(dim=-1)
            event_mse = (event_soft_bytes - target_events_uint8.float()).pow(2).mean()
            metrics["temporal/event_soft_byte_psnr_db"] = float(byte_psnr_db(event_mse).detach().cpu())
    return TemporalLossResult(loss=loss, metrics=metrics)


def unpack_bits(values: torch.Tensor) -> torch.Tensor:
    shifts = torch.arange(8, device=values.device, dtype=values.dtype)
    return ((values.unsqueeze(-1) >> shifts) & 1).float()


def pack_bits(bits: torch.Tensor) -> torch.Tensor:
    weights = BYTE_VALUE_BIT_WEIGHTS.to(bits.device, dtype=torch.long)
    return (bits.long() * weights).sum(dim=-1)


def byte_psnr_db(mse: torch.Tensor) -> torch.Tensor:
    return 10.0 * torch.log10((BYTE_MAX_VALUE**2) / torch.clamp(mse, min=PSNR_EPSILON))

