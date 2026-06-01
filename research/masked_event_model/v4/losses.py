from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.masked_event_model.v4.config import LossConfig
from research.masked_event_model.v4.masking import ByteMaskBatch
from research.masked_event_model.v4.model import ByteMAEOutput


BIT_WEIGHTS = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.float32)


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def masked_byte_bce_loss(
    output: ByteMAEOutput,
    header_uint8: torch.Tensor,
    events_uint8: torch.Tensor,
    masks: ByteMaskBatch,
    config: LossConfig,
    *,
    include_diagnostics: bool = False,
) -> LossResult:
    header_loss, header_metrics = masked_group_loss(
        output.header_bit_logits,
        output.header_indices,
        header_uint8,
        masks.header_mask,
        prefix="header",
        include_diagnostics=include_diagnostics,
    )
    event_loss, event_metrics = masked_group_loss(
        output.event_bit_logits,
        output.event_indices,
        events_uint8,
        masks.event_mask,
        prefix="event",
        include_diagnostics=include_diagnostics,
    )
    total_weight = 0.0
    loss = header_loss.new_tensor(0.0)
    if header_metrics["pretrain/header_masked_bytes"] > 0:
        loss = loss + float(config.header_weight) * header_loss
        total_weight += float(config.header_weight)
    if event_metrics["pretrain/event_masked_bytes"] > 0:
        loss = loss + float(config.event_weight) * event_loss
        total_weight += float(config.event_weight)
    if total_weight > 0.0:
        loss = loss / total_weight
    metrics = {
        "pretrain/loss_total": float(loss.detach().cpu()),
        "pretrain/loss_header": float(header_loss.detach().cpu()),
        "pretrain/loss_event": float(event_loss.detach().cpu()),
        "mask/header_masked_bytes": header_metrics["pretrain/header_masked_bytes"],
        "mask/event_masked_bytes": event_metrics["pretrain/event_masked_bytes"],
        "mask/total_masked_bytes": header_metrics["pretrain/header_masked_bytes"] + event_metrics["pretrain/event_masked_bytes"],
        **header_metrics,
        **event_metrics,
    }
    return LossResult(loss=loss, metrics=metrics)


def masked_group_loss(
    logits: torch.Tensor,
    indices: torch.Tensor,
    target_uint8: torch.Tensor,
    mask: torch.Tensor,
    *,
    prefix: str,
    include_diagnostics: bool,
) -> tuple[torch.Tensor, dict[str, float]]:
    if indices.numel() == 0:
        zero = logits.sum() * 0.0
        return zero, empty_metrics(prefix)
    target_bytes = target_uint8[tuple(indices.T)].long()
    target_bits = unpack_bits(target_bytes).to(dtype=logits.dtype, device=logits.device)
    loss = F.binary_cross_entropy_with_logits(logits, target_bits)
    with torch.no_grad():
        probabilities = torch.sigmoid(logits)
        hard_bits = probabilities >= 0.5
        target_bool = target_bits.bool()
        bit_acc = (hard_bits == target_bool).float().mean()
        hard_bytes = pack_bits(hard_bits)
        target_float = target_bytes.float()
        hard_mae = (hard_bytes.float() - target_float).abs().mean()
        soft_bytes = (probabilities.float() * BIT_WEIGHTS.to(probabilities.device)).sum(dim=-1)
        soft_mae = (soft_bytes - target_float).abs().mean()
        exact = (hard_bytes == target_bytes).float().mean()
        confidence = (probabilities - 0.5).abs() * 2.0
        metrics = {
            f"pretrain/{prefix}_masked_bytes": float(indices.shape[0]),
            f"pretrain/{prefix}_bit_acc_pct": float(bit_acc.detach().cpu() * 100.0),
            f"pretrain/{prefix}_byte_exact_acc_pct": float(exact.detach().cpu() * 100.0),
            f"pretrain/{prefix}_hard_byte_mae": float(hard_mae.detach().cpu()),
            f"pretrain/{prefix}_soft_byte_mae": float(soft_mae.detach().cpu()),
            f"pretrain/{prefix}_bit_conf_mean": float(confidence.mean().detach().cpu()),
            f"pretrain/{prefix}_bit_conf_min": float(confidence.min().detach().cpu()),
        }
        if include_diagnostics:
            high_conf = confidence >= 0.8
            if high_conf.any():
                metrics[f"pretrain/{prefix}_high_conf_bit_acc_pct"] = float((hard_bits[high_conf] == target_bool[high_conf]).float().mean().detach().cpu() * 100.0)
            low_conf = confidence <= 0.2
            if low_conf.any():
                metrics[f"pretrain/{prefix}_low_conf_bit_acc_pct"] = float((hard_bits[low_conf] == target_bool[low_conf]).float().mean().detach().cpu() * 100.0)
    return loss, metrics


def empty_metrics(prefix: str) -> dict[str, float]:
    return {
        f"pretrain/{prefix}_masked_bytes": 0.0,
        f"pretrain/{prefix}_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_byte_exact_acc_pct": 0.0,
        f"pretrain/{prefix}_hard_byte_mae": 0.0,
        f"pretrain/{prefix}_soft_byte_mae": 0.0,
        f"pretrain/{prefix}_bit_conf_mean": 0.0,
        f"pretrain/{prefix}_bit_conf_min": 0.0,
    }


def unpack_bits(values: torch.Tensor) -> torch.Tensor:
    shifts = torch.arange(8, device=values.device, dtype=values.dtype)
    return ((values.unsqueeze(-1) >> shifts) & 1).float()


def pack_bits(bits: torch.Tensor) -> torch.Tensor:
    weights = BIT_WEIGHTS.to(bits.device, dtype=torch.long)
    return (bits.long() * weights).sum(dim=-1)
