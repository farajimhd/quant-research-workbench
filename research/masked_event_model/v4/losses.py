from __future__ import annotations

import time
from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.masked_event_model.v4.config import LossConfig
from research.masked_event_model.v4.masking import ByteMaskBatch
from research.masked_event_model.v4.model import ByteMAEOutput


BIT_WEIGHTS = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.float32)
BYTE_MAX_VALUE = 255.0
PSNR_EPSILON = 1e-12


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
    profile_metrics: bool = False,
) -> LossResult:
    header_loss, header_metrics = masked_group_loss(
        output.header_bit_logits,
        output.header_indices,
        header_uint8,
        masks.header_mask,
        prefix="header",
        include_diagnostics=include_diagnostics,
        profile_metrics=profile_metrics,
    )
    event_loss, event_metrics = masked_group_loss(
        output.event_bit_logits,
        output.event_indices,
        events_uint8,
        masks.event_mask,
        prefix="event",
        include_diagnostics=include_diagnostics,
        profile_metrics=profile_metrics,
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
    profile_metrics: bool,
) -> tuple[torch.Tensor, dict[str, float]]:
    if indices.numel() == 0:
        zero = logits.sum() * 0.0
        return zero, empty_metrics(prefix)
    target_bytes = target_uint8[tuple(indices.T)].long()
    target_bits = unpack_bits(target_bytes).to(dtype=logits.dtype, device=logits.device)
    if logits.is_cuda:
        with torch.amp.autocast("cuda", enabled=False):
            loss = F.binary_cross_entropy_with_logits(logits.float(), target_bits.float())
    else:
        loss = F.binary_cross_entropy_with_logits(logits, target_bits)
    metrics_started = time.perf_counter()
    with torch.no_grad():
        probabilities = torch.sigmoid(logits.float())
        hard_bits = probabilities >= 0.5
        target_bool = target_bits.bool()
        bit_acc = (hard_bits == target_bool).float().mean()
        target_one_rate = target_bits.float().mean()
        pred_one_rate = hard_bits.float().mean()
        majority_baseline = torch.maximum(target_one_rate, 1.0 - target_one_rate)
        one_mask = target_bool
        zero_mask = ~target_bool
        one_acc = (hard_bits[one_mask] == target_bool[one_mask]).float().mean() if one_mask.any() else probabilities.new_tensor(0.0)
        zero_acc = (hard_bits[zero_mask] == target_bool[zero_mask]).float().mean() if zero_mask.any() else probabilities.new_tensor(0.0)
        balanced_bit_acc = (one_acc + zero_acc) * 0.5 if one_mask.any() and zero_mask.any() else bit_acc
        hard_bytes = pack_bits(hard_bits)
        target_float = target_bytes.float()
        hard_mae = (hard_bytes.float() - target_float).abs().mean()
        soft_bytes = (probabilities.float() * BIT_WEIGHTS.to(probabilities.device)).sum(dim=-1)
        soft_mae = (soft_bytes - target_float).abs().mean()
        exact = (hard_bytes == target_bytes).float().mean()
        mode_count = torch.bincount(target_bytes, minlength=256).max()
        byte_mode_baseline = mode_count.float() / target_bytes.numel()
        confidence = (probabilities - 0.5).abs() * 2.0
        metrics = {
            f"pretrain/{prefix}_masked_bytes": float(indices.shape[0]),
            f"pretrain/{prefix}_bit_acc_pct": float(bit_acc.detach().cpu() * 100.0),
            f"pretrain/{prefix}_bit_majority_baseline_pct": float(majority_baseline.detach().cpu() * 100.0),
            f"pretrain/{prefix}_bit_acc_lift_pct": float((bit_acc - majority_baseline).detach().cpu() * 100.0),
            f"pretrain/{prefix}_balanced_bit_acc_pct": float(balanced_bit_acc.detach().cpu() * 100.0),
            f"pretrain/{prefix}_zero_bit_acc_pct": float(zero_acc.detach().cpu() * 100.0),
            f"pretrain/{prefix}_one_bit_acc_pct": float(one_acc.detach().cpu() * 100.0),
            f"pretrain/{prefix}_target_one_rate_pct": float(target_one_rate.detach().cpu() * 100.0),
            f"pretrain/{prefix}_pred_one_rate_pct": float(pred_one_rate.detach().cpu() * 100.0),
            f"pretrain/{prefix}_byte_exact_acc_pct": float(exact.detach().cpu() * 100.0),
            f"pretrain/{prefix}_byte_mode_baseline_pct": float(byte_mode_baseline.detach().cpu() * 100.0),
            f"pretrain/{prefix}_byte_exact_lift_pct": float((exact - byte_mode_baseline).detach().cpu() * 100.0),
            f"pretrain/{prefix}_hard_byte_mae": float(hard_mae.detach().cpu()),
            f"pretrain/{prefix}_soft_byte_mae": float(soft_mae.detach().cpu()),
            f"pretrain/{prefix}_bit_conf_mean": float(confidence.mean().detach().cpu()),
            f"pretrain/{prefix}_bit_conf_min": float(confidence.min().detach().cpu()),
        }
        per_bit_acc = (hard_bits == target_bool).float().mean(dim=0)
        per_bit_one_rate = target_bits.float().mean(dim=0)
        per_bit_pred_one_rate = hard_bits.float().mean(dim=0)
        for bit_index in range(8):
            metrics[f"pretrain/{prefix}_bit{bit_index}_acc_pct"] = float(per_bit_acc[bit_index].detach().cpu() * 100.0)
            metrics[f"pretrain/{prefix}_bit{bit_index}_target_one_rate_pct"] = float(per_bit_one_rate[bit_index].detach().cpu() * 100.0)
            metrics[f"pretrain/{prefix}_bit{bit_index}_pred_one_rate_pct"] = float(per_bit_pred_one_rate[bit_index].detach().cpu() * 100.0)
        if include_diagnostics:
            if prefix == "event":
                hard_mse = (hard_bytes.float() - target_float).pow(2).mean()
                soft_mse = (soft_bytes - target_float).pow(2).mean()
                metrics[f"pretrain/{prefix}_hard_byte_psnr_db"] = float(byte_psnr_db(hard_mse).detach().cpu())
                metrics[f"pretrain/{prefix}_soft_byte_psnr_db"] = float(byte_psnr_db(soft_mse).detach().cpu())
            high_conf = confidence >= 0.8
            if high_conf.any():
                metrics[f"pretrain/{prefix}_high_conf_bit_acc_pct"] = float((hard_bits[high_conf] == target_bool[high_conf]).float().mean().detach().cpu() * 100.0)
            low_conf = confidence <= 0.2
            if low_conf.any():
                metrics[f"pretrain/{prefix}_low_conf_bit_acc_pct"] = float((hard_bits[low_conf] == target_bool[low_conf]).float().mean().detach().cpu() * 100.0)
    if profile_metrics:
        if probabilities.is_cuda:
            torch.cuda.synchronize(probabilities.device)
        metrics[f"profile/{prefix}_metrics_seconds"] = time.perf_counter() - metrics_started
    return loss, metrics


def empty_metrics(prefix: str) -> dict[str, float]:
    return {
        f"pretrain/{prefix}_masked_bytes": 0.0,
        f"pretrain/{prefix}_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_bit_majority_baseline_pct": 0.0,
        f"pretrain/{prefix}_bit_acc_lift_pct": 0.0,
        f"pretrain/{prefix}_balanced_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_zero_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_one_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_target_one_rate_pct": 0.0,
        f"pretrain/{prefix}_pred_one_rate_pct": 0.0,
        f"pretrain/{prefix}_byte_exact_acc_pct": 0.0,
        f"pretrain/{prefix}_byte_mode_baseline_pct": 0.0,
        f"pretrain/{prefix}_byte_exact_lift_pct": 0.0,
        f"pretrain/{prefix}_hard_byte_mae": 0.0,
        f"pretrain/{prefix}_soft_byte_mae": 0.0,
        f"pretrain/{prefix}_bit_conf_mean": 0.0,
        f"pretrain/{prefix}_bit_conf_min": 0.0,
        **{f"pretrain/{prefix}_bit{bit_index}_acc_pct": 0.0 for bit_index in range(8)},
        **{f"pretrain/{prefix}_bit{bit_index}_target_one_rate_pct": 0.0 for bit_index in range(8)},
        **{f"pretrain/{prefix}_bit{bit_index}_pred_one_rate_pct": 0.0 for bit_index in range(8)},
    }


def unpack_bits(values: torch.Tensor) -> torch.Tensor:
    shifts = torch.arange(8, device=values.device, dtype=values.dtype)
    return ((values.unsqueeze(-1) >> shifts) & 1).float()


def pack_bits(bits: torch.Tensor) -> torch.Tensor:
    weights = BIT_WEIGHTS.to(bits.device, dtype=torch.long)
    return (bits.long() * weights).sum(dim=-1)


def byte_psnr_db(mse: torch.Tensor) -> torch.Tensor:
    return 10.0 * torch.log10(mse.new_tensor(BYTE_MAX_VALUE**2) / mse.clamp_min(PSNR_EPSILON))
