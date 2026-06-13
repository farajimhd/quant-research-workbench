from __future__ import annotations

import time
from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.masked_event_model.v5.config import LossConfig
from research.masked_event_model.v5.model import EventMAEOutput


BIT_WEIGHTS = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.float32)
BYTE_MAX_VALUE = 255.0
PSNR_EPSILON = 1e-12


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def masked_event_bce_loss(
    output: EventMAEOutput,
    config: LossConfig,
    *,
    include_diagnostics: bool = False,
    profile_metrics: bool = False,
    metric_level: str = "standard",
) -> LossResult:
    logits = output.event_bit_logits
    target_bytes = output.target_events_uint8.long()
    target_bits = unpack_bits(target_bytes).to(dtype=logits.dtype, device=logits.device)
    if logits.is_cuda:
        with torch.amp.autocast("cuda", enabled=False):
            loss = F.binary_cross_entropy_with_logits(logits.float(), target_bits.float())
    else:
        loss = F.binary_cross_entropy_with_logits(logits, target_bits)
    loss = loss * float(config.event_weight)

    metrics_started = time.perf_counter()
    with torch.no_grad():
        probabilities = torch.sigmoid(logits.float())
        hard_bits = probabilities >= 0.5
        target_bool = target_bits.bool()
        bit_acc = (hard_bits == target_bool).float().mean()
        hard_bytes = pack_bits(hard_bits)
        exact = (hard_bytes == target_bytes).float().mean()
        confidence = (probabilities - 0.5).abs() * 2.0
        metrics = {
            "pretrain/loss_total": float(loss.detach().cpu()),
            "pretrain/loss_event": float(loss.detach().cpu()),
            "pretrain/event_masked_events": float(output.masked_event_indices.numel()),
            "pretrain/event_masked_bytes": float(target_bytes.numel()),
            "pretrain/event_bit_acc_pct": float(bit_acc.detach().cpu() * 100.0),
            "pretrain/event_byte_exact_acc_pct": float(exact.detach().cpu() * 100.0),
            "pretrain/event_bit_conf_mean": float(confidence.mean().detach().cpu()),
            "mask/event_masked_events": float(output.masked_event_indices.numel()),
            "mask/event_masked_bytes": float(target_bytes.numel()),
            "mask/total_masked_bytes": float(target_bytes.numel()),
        }
        if metric_level != "cheap":
            target_one_rate = target_bits.float().mean()
            pred_one_rate = hard_bits.float().mean()
            majority_baseline = torch.maximum(target_one_rate, 1.0 - target_one_rate)
            one_mask = target_bool
            zero_mask = ~target_bool
            one_acc = (hard_bits[one_mask] == target_bool[one_mask]).float().mean() if one_mask.any() else probabilities.new_tensor(0.0)
            zero_acc = (hard_bits[zero_mask] == target_bool[zero_mask]).float().mean() if zero_mask.any() else probabilities.new_tensor(0.0)
            balanced_bit_acc = (one_acc + zero_acc) * 0.5 if one_mask.any() and zero_mask.any() else bit_acc
            target_float = target_bytes.float()
            hard_mae = (hard_bytes.float() - target_float).abs().mean()
            soft_bytes = (probabilities.float() * BIT_WEIGHTS.to(probabilities.device)).sum(dim=-1)
            soft_mae = (soft_bytes - target_float).abs().mean()
            mode_count = torch.bincount(target_bytes.flatten(), minlength=256).max()
            byte_mode_baseline = mode_count.float() / target_bytes.numel()
            metrics.update(
                {
                    "pretrain/event_bit_majority_baseline_pct": float(majority_baseline.detach().cpu() * 100.0),
                    "pretrain/event_bit_acc_lift_pct": float((bit_acc - majority_baseline).detach().cpu() * 100.0),
                    "pretrain/event_balanced_bit_acc_pct": float(balanced_bit_acc.detach().cpu() * 100.0),
                    "pretrain/event_zero_bit_acc_pct": float(zero_acc.detach().cpu() * 100.0),
                    "pretrain/event_one_bit_acc_pct": float(one_acc.detach().cpu() * 100.0),
                    "pretrain/event_target_one_rate_pct": float(target_one_rate.detach().cpu() * 100.0),
                    "pretrain/event_pred_one_rate_pct": float(pred_one_rate.detach().cpu() * 100.0),
                    "pretrain/event_byte_mode_baseline_pct": float(byte_mode_baseline.detach().cpu() * 100.0),
                    "pretrain/event_byte_exact_lift_pct": float((exact - byte_mode_baseline).detach().cpu() * 100.0),
                    "pretrain/event_hard_byte_mae": float(hard_mae.detach().cpu()),
                    "pretrain/event_soft_byte_mae": float(soft_mae.detach().cpu()),
                    "pretrain/event_bit_conf_min": float(confidence.min().detach().cpu()),
                }
            )
            if include_diagnostics:
                hard_mse = (hard_bytes.float() - target_float).pow(2).mean()
                soft_mse = (soft_bytes - target_float).pow(2).mean()
                metrics["pretrain/event_hard_byte_psnr_db"] = float(byte_psnr_db(hard_mse).detach().cpu())
                metrics["pretrain/event_soft_byte_psnr_db"] = float(byte_psnr_db(soft_mse).detach().cpu())
        if profile_metrics:
            if logits.is_cuda:
                torch.cuda.synchronize(logits.device)
            metrics["profile/event_metrics_seconds"] = time.perf_counter() - metrics_started
            metrics["profile/metrics_seconds"] = metrics["profile/event_metrics_seconds"]
    return LossResult(loss=loss, metrics=metrics)


def unpack_bits(values: torch.Tensor) -> torch.Tensor:
    shifts = torch.arange(8, device=values.device, dtype=values.dtype)
    return ((values.unsqueeze(-1) >> shifts) & 1).float()


def pack_bits(bits: torch.Tensor) -> torch.Tensor:
    weights = BIT_WEIGHTS.to(bits.device, dtype=torch.long)
    return (bits.long() * weights).sum(dim=-1)


def byte_psnr_db(mse: torch.Tensor) -> torch.Tensor:
    return 10.0 * torch.log10(mse.new_tensor(BYTE_MAX_VALUE**2) / mse.clamp_min(PSNR_EPSILON))
