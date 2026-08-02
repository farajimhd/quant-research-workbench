from __future__ import annotations

import torch

from dataclasses import dataclass
from research.bar_gpt.v1.config import TrainConfig
from research.bar_gpt.v1.data import BarGPTBatch
from research.bar_gpt.v1.model import BarGPTOutput
from research.bar_gpt.v1.targets import CONTINUOUS_TARGET_COUNT


def masked_quantile_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    quantiles: tuple[float, ...],
) -> torch.Tensor:
    """Pinball loss for prediction [B,N,H,K,Q] and target/mask [B,N,H,K]."""
    if prediction.shape[:-1] != target.shape or target.shape != mask.shape:
        raise ValueError("prediction, target, and mask shapes do not satisfy the quantile contract")
    q = torch.as_tensor(quantiles, device=prediction.device, dtype=prediction.dtype)
    error = target.unsqueeze(-1).to(prediction.dtype) - prediction
    loss = torch.maximum(q * error, (q - 1.0) * error)
    valid = mask.unsqueeze(-1).expand_as(loss)
    if not torch.any(valid):
        return prediction.sum() * 0.0
    return loss[valid].mean()


def masked_huber_loss(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, delta: float = 1.0) -> torch.Tensor:
    if prediction.shape != target.shape or target.shape != mask.shape:
        raise ValueError("prediction, target, and mask must have identical shapes")
    if not torch.any(mask):
        return prediction.sum() * 0.0
    return torch.nn.functional.huber_loss(prediction[mask], target[mask].to(prediction.dtype), delta=delta)


@dataclass(slots=True)
class BarGPTLoss:
    loss: torch.Tensor
    metrics: dict[str, torch.Tensor]


def _weighted_mean(loss: torch.Tensor, mask: torch.Tensor, sample_weights: torch.Tensor) -> torch.Tensor:
    weights = sample_weights.view(sample_weights.shape[0], *([1] * (loss.ndim - 1))).expand_as(loss)
    valid_weights = torch.where(mask, weights, torch.zeros_like(weights))
    denominator = valid_weights.sum()
    if not bool(denominator > 0):
        return loss.sum() * 0.0
    return (loss * valid_weights).sum() / denominator


def _mixed_point_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    sample_weights: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    continuous_error = torch.nn.functional.huber_loss(
        prediction[..., :CONTINUOUS_TARGET_COUNT],
        target[..., :CONTINUOUS_TARGET_COUNT].to(prediction.dtype),
        reduction="none",
    )
    continuous = _weighted_mean(continuous_error, mask[..., :CONTINUOUS_TARGET_COUNT], sample_weights)
    availability_error = torch.nn.functional.binary_cross_entropy_with_logits(
        prediction[..., CONTINUOUS_TARGET_COUNT:],
        target[..., CONTINUOUS_TARGET_COUNT:].to(prediction.dtype),
        reduction="none",
    )
    availability = _weighted_mean(availability_error, mask[..., CONTINUOUS_TARGET_COUNT:], sample_weights)
    return continuous, availability


def compute_loss(output: BarGPTOutput, batch: BarGPTBatch, config: TrainConfig, quantiles: tuple[float, ...]) -> BarGPTLoss:
    if batch.horizon_targets is None or batch.horizon_mask is None:
        raise ValueError("physical horizon targets must be materialized on the training device before loss computation")
    ar_continuous: list[torch.Tensor] = []
    ar_availability: list[torch.Tensor] = []
    latent_losses: list[torch.Tensor] = []
    metrics: dict[str, torch.Tensor] = {}
    for name, prediction in output.autoregressive.items():
        target = batch.autoregressive_targets[name][:, : prediction.shape[1]]
        mask = batch.autoregressive_mask[name][:, : prediction.shape[1]]
        continuous, availability = _mixed_point_loss(prediction, target, mask, batch.sample_weights)
        ar_continuous.append(continuous)
        ar_availability.append(availability)
        metrics[f"train/loss_ar_{name}"] = (continuous + config.availability_weight * availability).detach()
        latent_prediction = output.latent_predictions[name]
        latent_target = output.scale_embeddings[name][:, 1 : 1 + latent_prediction.shape[1]].detach()
        latent_mask = mask.any(dim=-1)
        latent_error = 1.0 - torch.nn.functional.cosine_similarity(latent_prediction, latent_target, dim=-1)
        latent = _weighted_mean(latent_error, latent_mask, batch.sample_weights)
        latent_losses.append(latent)
    zero = output.embeddings.sum() * 0.0
    ar_cont = torch.stack(ar_continuous).mean() if ar_continuous else zero
    ar_avail = torch.stack(ar_availability).mean() if ar_availability else zero
    latent_loss = torch.stack(latent_losses).mean() if latent_losses else zero
    horizon_cont = zero
    horizon_avail = zero
    if output.horizon_quantiles is not None:
        target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
        mask = batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT] & batch.origin_mask[:, :, None, None]
        q = torch.as_tensor(quantiles, device=output.horizon_quantiles.device, dtype=output.horizon_quantiles.dtype)
        error = target.unsqueeze(-1).to(output.horizon_quantiles.dtype) - output.horizon_quantiles
        pinball = torch.maximum(q * error, (q - 1.0) * error)
        horizon_cont = _weighted_mean(pinball, mask.unsqueeze(-1).expand_as(pinball), batch.sample_weights)
    if output.horizon_availability_logits is not None:
        target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
        mask = batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:] & batch.origin_mask[:, :, None, None]
        bce = torch.nn.functional.binary_cross_entropy_with_logits(
            output.horizon_availability_logits,
            target.to(output.horizon_availability_logits.dtype),
            reduction="none",
        )
        horizon_avail = _weighted_mean(bce, mask, batch.sample_weights)
    ar_loss = ar_cont + config.availability_weight * ar_avail
    horizon_loss = horizon_cont + config.availability_weight * horizon_avail
    total = (
        config.autoregressive_weight * ar_loss
        + config.horizon_weight * horizon_loss
        + config.latent_prediction_weight * latent_loss
    )
    metrics.update(
        {
            "train/loss": total.detach(),
            "train/loss_autoregressive": ar_loss.detach(),
            "train/loss_horizon": horizon_loss.detach(),
            "train/loss_ar_continuous": ar_cont.detach(),
            "train/loss_ar_availability": ar_avail.detach(),
            "train/loss_horizon_quantile": horizon_cont.detach(),
            "train/loss_horizon_availability": horizon_avail.detach(),
            "train/loss_latent_prediction": latent_loss.detach(),
        }
    )
    return BarGPTLoss(loss=total, metrics=metrics)
