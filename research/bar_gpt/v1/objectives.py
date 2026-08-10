from __future__ import annotations

import math

import torch

from dataclasses import dataclass
from research.bar_gpt.v1.config import TrainConfig
from research.bar_gpt.v1.data import BarGPTBatch
from research.bar_gpt.v1.model import BarGPTOutput
from research.bar_gpt.v1.targets import AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT, CONTINUOUS_TARGET_COUNT


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
    if not torch.any(mask):
        return torch.nan_to_num(loss).sum() * 0.0
    valid_weights = weights[mask]
    return (loss[mask] * valid_weights).sum() / valid_weights.sum()


def _mixed_point_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    sample_weights: torch.Tensor,
    condition_positive_weight: float,
    continuous_target_count: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    continuous_error = torch.nn.functional.huber_loss(
        prediction[..., :continuous_target_count],
        target[..., :continuous_target_count].to(prediction.dtype),
        reduction="none",
    )
    continuous = _weighted_mean(continuous_error, mask[..., :continuous_target_count], sample_weights)
    positive_weights = prediction.new_ones(prediction.shape[-1] - continuous_target_count)
    positive_weights[-4:] = float(condition_positive_weight)
    availability_error = torch.nn.functional.binary_cross_entropy_with_logits(
        prediction[..., continuous_target_count:],
        target[..., continuous_target_count:].to(prediction.dtype),
        reduction="none",
        pos_weight=positive_weights,
    )
    availability = _weighted_mean(availability_error, mask[..., continuous_target_count:], sample_weights)
    return continuous, availability


def _direction_loss(
    logits: torch.Tensor,
    endpoint_target: torch.Tensor,
    endpoint_mask: torch.Tensor,
    sample_weights: torch.Tensor,
    neutral_bps: float,
) -> torch.Tensor:
    """Binary up/down loss outside a configurable neutral return band."""
    if logits.shape != endpoint_target.shape or endpoint_target.shape != endpoint_mask.shape:
        raise ValueError("direction logits, endpoint targets, and masks must have identical shapes")
    transformed_threshold = math.asinh(float(neutral_bps) / 100.0)
    directional_mask = endpoint_mask & (endpoint_target.abs() > transformed_threshold)
    labels = endpoint_target > transformed_threshold
    loss = torch.nn.functional.binary_cross_entropy_with_logits(
        logits,
        labels.to(logits.dtype),
        reduction="none",
    )
    return _weighted_mean(loss, directional_mask, sample_weights)


def compute_loss(output: BarGPTOutput, batch: BarGPTBatch, config: TrainConfig, quantiles: tuple[float, ...]) -> BarGPTLoss:
    if batch.horizon_targets is None or batch.horizon_mask is None:
        raise ValueError("physical horizon targets must be materialized on the training device before loss computation")
    ar_continuous: list[torch.Tensor] = []
    ar_availability: list[torch.Tensor] = []
    ar_direction_losses: list[torch.Tensor] = []
    latent_losses: list[torch.Tensor] = []
    metrics: dict[str, torch.Tensor] = {}
    for name, prediction in output.autoregressive.items():
        target = batch.autoregressive_targets[name][:, : prediction.shape[1]]
        mask = batch.autoregressive_mask[name][:, : prediction.shape[1]]
        continuous, availability = _mixed_point_loss(
            prediction,
            target,
            mask,
            batch.sample_weights,
            config.condition_positive_weight,
            AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT,
        )
        ar_continuous.append(continuous)
        ar_availability.append(availability)
        direction = _direction_loss(
            output.autoregressive_direction_logits[name],
            target[..., 0],
            mask[..., 0],
            batch.sample_weights,
            config.direction_neutral_bps,
        )
        ar_direction_losses.append(direction)
        metrics[f"train/loss_ar_{name}"] = (
            continuous
            + config.availability_weight * availability
            + config.direction_weight * direction
        ).detach()
        latent_prediction = output.latent_predictions[name]
        latent_target = output.scale_embeddings[name][:, 1 : 1 + latent_prediction.shape[1]].detach()
        latent_mask = mask.any(dim=-1)
        latent_error = 1.0 - torch.nn.functional.cosine_similarity(latent_prediction, latent_target, dim=-1)
        latent = _weighted_mean(latent_error, latent_mask, batch.sample_weights)
        latent_losses.append(latent)
    zero = output.embeddings.sum() * 0.0
    ar_cont = torch.stack(ar_continuous).mean() if ar_continuous else zero
    ar_avail = torch.stack(ar_availability).mean() if ar_availability else zero
    ar_direction = torch.stack(ar_direction_losses).mean() if ar_direction_losses else zero
    latent_loss = torch.stack(latent_losses).mean() if latent_losses else zero
    horizon_cont = zero
    horizon_avail = zero
    horizon_direction = zero
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
        positive_weights = output.horizon_availability_logits.new_ones(
            output.horizon_availability_logits.shape[-1]
        )
        positive_weights[-4:] = float(config.condition_positive_weight)
        bce = torch.nn.functional.binary_cross_entropy_with_logits(
            output.horizon_availability_logits,
            target.to(output.horizon_availability_logits.dtype),
            reduction="none",
            pos_weight=positive_weights,
        )
        horizon_avail = _weighted_mean(bce, mask, batch.sample_weights)
    if output.horizon_direction_logits is not None:
        endpoint_target = batch.horizon_targets[..., :3]
        endpoint_mask = batch.horizon_mask[..., :3] & batch.origin_mask[:, :, None, None]
        horizon_direction = _direction_loss(
            output.horizon_direction_logits,
            endpoint_target,
            endpoint_mask,
            batch.sample_weights,
            config.direction_neutral_bps,
        )
    ar_loss = ar_cont + config.availability_weight * ar_avail + config.direction_weight * ar_direction
    horizon_loss = horizon_cont + config.availability_weight * horizon_avail + config.direction_weight * horizon_direction
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
            "train/loss_ar_direction": ar_direction.detach(),
            "train/loss_horizon_quantile": horizon_cont.detach(),
            "train/loss_horizon_availability": horizon_avail.detach(),
            "train/loss_horizon_direction": horizon_direction.detach(),
            "train/loss_latent_prediction": latent_loss.detach(),
        }
    )
    return BarGPTLoss(loss=total, metrics=metrics)
