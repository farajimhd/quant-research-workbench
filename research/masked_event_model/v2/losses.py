from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F

from research.masked_event_model.v2.config import LossConfig
from research.masked_event_model.v2.masking import MaskBatch
from research.masked_event_model.v2.model import ModelOutput


def masked_autoencoder_loss(
    output: ModelOutput,
    batch: dict[str, Any],
    masks: MaskBatch,
    config: LossConfig,
) -> tuple[torch.Tensor, dict[str, float]]:
    quote_target = batch["quote_values"]
    trade_target = batch["trade_values"]
    summary_target = batch["chunk_summary"]
    event_target = batch["event_kinds"].clamp(0, 2)

    quote_valid = quote_target.abs().sum(dim=-1, keepdim=True) > 0.0
    trade_valid = trade_target.abs().sum(dim=-1, keepdim=True) > 0.0
    quote_mask = masks.quote_value_mask & quote_valid
    trade_mask = masks.trade_value_mask & trade_valid
    summary_mask = masks.summary_value_mask
    event_mask = masks.event_kind_mask & (event_target != 2)

    quote_loss = masked_mse(output.quote_reconstruction, quote_target, quote_mask)
    trade_loss = masked_mse(output.trade_reconstruction, trade_target, trade_mask)
    summary_loss = masked_mse(output.summary_reconstruction, summary_target, summary_mask)
    event_kind_loss = masked_cross_entropy(output.event_kind_logits, event_target, event_mask)

    total = (
        config.quote_weight * quote_loss
        + config.trade_weight * trade_loss
        + config.summary_weight * summary_loss
        + config.event_kind_weight * event_kind_loss
    )
    metrics = {
        "pretrain/loss_total": float(total.detach().cpu()),
        "pretrain/loss_quote": float(quote_loss.detach().cpu()),
        "pretrain/loss_trade": float(trade_loss.detach().cpu()),
        "pretrain/loss_summary": float(summary_loss.detach().cpu()),
        "pretrain/loss_event_kind": float(event_kind_loss.detach().cpu()),
        "pretrain/quote_price_rmse_bps": masked_rmse(output.quote_reconstruction[..., 2:5], quote_target[..., 2:5], quote_mask[..., 2:5]),
        "pretrain/trade_price_rmse_bps": masked_rmse(output.trade_reconstruction[..., 2:3], trade_target[..., 2:3], trade_mask[..., 2:3]),
        "pretrain/quote_psnr_peak6_db": masked_psnr(output.quote_reconstruction, quote_target, quote_mask),
        "pretrain/trade_psnr_peak6_db": masked_psnr(output.trade_reconstruction, trade_target, trade_mask),
        "pretrain/summary_psnr_peak6_db": masked_psnr(output.summary_reconstruction, summary_target, summary_mask),
        "pretrain/event_kind_acc_pct": masked_accuracy(output.event_kind_logits.argmax(dim=-1), event_target, event_mask),
    }
    metrics.update(masks.diagnostics())
    return total, metrics


def masked_mse(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    values = (prediction - target).pow(2)
    weights = mask.float()
    return (values * weights).sum() / weights.sum().clamp_min(1.0)


def masked_cross_entropy(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    loss = F.cross_entropy(logits.reshape(-1, logits.shape[-1]), target.reshape(-1), reduction="none")
    weights = mask.reshape(-1).float()
    return (loss * weights).sum() / weights.sum().clamp_min(1.0)


def masked_rmse(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    mse = masked_mse(prediction, target, mask)
    return float(torch.sqrt(mse.detach()).cpu())


def masked_psnr(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, *, peak: float = 6.0) -> float:
    mse = masked_mse(prediction, target, mask).detach().clamp_min(1e-12)
    psnr = 20.0 * torch.log10(torch.tensor(float(peak), device=mse.device)) - 10.0 * torch.log10(mse)
    return float(psnr.cpu())


def masked_accuracy(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    correct = ((prediction == target) & mask).float().sum()
    return float((correct / mask.float().sum().clamp_min(1.0) * 100.0).detach().cpu())


def forecast_bce_loss(logits: torch.Tensor, targets: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
    loss = F.binary_cross_entropy_with_logits(logits, targets)
    bit_acc = ((logits.detach().sigmoid() >= 0.5) == (targets >= 0.5)).float().mean() * 100.0
    sign_acc = ((logits.detach()[..., 0].sigmoid() >= 0.5) == (targets[..., 0] >= 0.5)).float().mean() * 100.0
    return loss, {
        "probe/loss_bce": float(loss.detach().cpu()),
        "probe/bit_acc_pct": float(bit_acc.cpu()),
        "probe/sign_bit_acc_pct": float(sign_acc.cpu()),
    }
