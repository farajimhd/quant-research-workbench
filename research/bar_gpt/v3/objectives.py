from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.bar_gpt.v3.config import TrainConfig
from research.bar_gpt.v3.data import BarGPTBatch
from research.bar_gpt.v3.model import BarGPTOutput
from research.bar_gpt.v3.targets import (
    AUTOREGRESSIVE_BINARY_TARGET_NAMES,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES,
    BINARY_TARGET_NAMES,
    CONTINUOUS_TARGET_COUNT,
    CONTINUOUS_TARGET_NAMES,
    NEXT_EVENT_GAP_CLASS_COUNT,
)


@dataclass(slots=True)
class BarGPTLoss:
    loss: torch.Tensor
    metrics: dict[str, torch.Tensor]
    target_stats: dict[str, tuple[torch.Tensor, torch.Tensor]]


def masked_quantile_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    quantiles: tuple[float, ...],
) -> torch.Tensor:
    """Compatibility utility; v2 training uses per-target `_quantile_stats`."""
    if prediction.shape[:-1] != target.shape or target.shape != mask.shape:
        raise ValueError("quantile prediction, target, and mask do not align")
    q = torch.as_tensor(quantiles, device=prediction.device, dtype=prediction.dtype)
    safe_prediction = torch.where(mask.unsqueeze(-1), prediction, torch.zeros_like(prediction))
    safe_target = torch.where(mask, target, torch.zeros_like(target)).to(prediction.dtype)
    error = safe_target.unsqueeze(-1) - safe_prediction
    loss = torch.maximum(q * error, (q - 1.0) * error)
    valid = mask.unsqueeze(-1).expand_as(loss)
    return torch.where(valid, loss, torch.zeros_like(loss)).sum() / valid.sum().clamp_min(1)


def masked_huber_loss(
    prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, delta: float = 1.0
) -> torch.Tensor:
    if prediction.shape != target.shape or target.shape != mask.shape:
        raise ValueError("prediction, target, and mask must have identical shapes")
    safe_prediction = torch.where(mask, prediction, torch.zeros_like(prediction))
    safe_target = torch.where(mask, target, torch.zeros_like(target)).to(prediction.dtype)
    loss = F.huber_loss(safe_prediction, safe_target, delta=delta, reduction="none")
    return torch.where(mask, loss, torch.zeros_like(loss)).sum() / mask.sum().clamp_min(1)


def _target_stats(
    loss: torch.Tensor,
    mask: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return unweighted numerator/count vectors for the final target axis."""
    if loss.shape != mask.shape or loss.ndim < 2:
        raise ValueError("loss and mask must match and retain a final target axis")
    dimensions = tuple(range(loss.ndim - 1))
    numerator = torch.where(mask, loss, torch.zeros_like(loss)).sum(dimensions)
    denominator = mask.to(loss.dtype).sum(dimensions)
    return numerator, denominator


def _target_means(numerator: torch.Tensor, denominator: torch.Tensor) -> torch.Tensor:
    if numerator.shape != denominator.shape:
        raise ValueError("target numerator and denominator must align")
    return torch.where(
        denominator > 0,
        numerator / denominator.clamp_min(1e-12),
        numerator * 0.0,
    )


def _add_stats(
    current: tuple[torch.Tensor, torch.Tensor] | None,
    value: tuple[torch.Tensor, torch.Tensor],
) -> tuple[torch.Tensor, torch.Tensor]:
    return value if current is None else (current[0] + value[0], current[1] + value[1])


def _point_regression_stats(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    if prediction.shape != target.shape or target.shape != mask.shape:
        raise ValueError("point prediction, target, and mask must have identical shapes")
    safe_prediction = torch.where(mask, prediction, torch.zeros_like(prediction))
    safe_target = torch.where(mask, target, torch.zeros_like(target)).to(prediction.dtype)
    return _target_stats(F.huber_loss(safe_prediction, safe_target, reduction="none"), mask)


def _binary_stats(
    logits: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    if logits.shape != target.shape or target.shape != mask.shape:
        raise ValueError("binary logits, target, and mask must have identical shapes")
    safe_logits = torch.where(mask, logits, torch.zeros_like(logits))
    safe_target = torch.where(mask, target, torch.zeros_like(target)).to(logits.dtype)
    return _target_stats(
        F.binary_cross_entropy_with_logits(safe_logits, safe_target, reduction="none"),
        mask,
    )


def _gap_stats(
    logits: torch.Tensor,
    labels: torch.Tensor,
    mask: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    if logits.shape[:-1] != labels.shape or labels.shape != mask.shape:
        raise ValueError("next-event gap logits, labels, and mask do not align")
    if logits.shape[-1] != NEXT_EVENT_GAP_CLASS_COUNT:
        raise ValueError("next-event gap logits use the wrong class contract")
    safe_labels = torch.where(mask, labels, torch.zeros_like(labels))
    loss = F.cross_entropy(
        logits.reshape(-1, NEXT_EVENT_GAP_CLASS_COUNT),
        safe_labels.reshape(-1),
        reduction="none",
    ).view_as(labels)
    numerator = torch.where(mask, loss, torch.zeros_like(loss)).sum().reshape(1)
    denominator = mask.to(loss.dtype).sum().reshape(1)
    return numerator, denominator


def _quantile_stats(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    quantiles: tuple[float, ...],
) -> tuple[torch.Tensor, torch.Tensor]:
    if prediction.shape[:-1] != target.shape or target.shape != mask.shape:
        raise ValueError("quantile prediction, target, and mask do not align")
    q = torch.as_tensor(quantiles, device=prediction.device, dtype=prediction.dtype)
    safe_prediction = torch.where(mask.unsqueeze(-1), prediction, torch.zeros_like(prediction))
    safe_target = torch.where(mask, target, torch.zeros_like(target)).to(prediction.dtype)
    error = safe_target.unsqueeze(-1) - safe_prediction
    pinball = torch.maximum(q * error, (q - 1.0) * error)
    # Move target behind quantile so _target_stats retains the target axis.
    pinball = pinball.movedim(-2, -1)
    valid = mask.unsqueeze(-1).expand_as(prediction).movedim(-2, -1)
    return _target_stats(pinball, valid)


def _record_target_stats(
    stats: dict[str, tuple[torch.Tensor, torch.Tensor]],
    *,
    group: str,
    names: tuple[str, ...],
    means: torch.Tensor,
    support: torch.Tensor,
) -> None:
    if means.numel() != len(names):
        raise ValueError(f"metric target schema mismatch for {group}")
    for index, name in enumerate(names):
        stats[f"{group}_{name}"] = (means[index].detach(), support[index].detach())


def compute_loss(
    output: BarGPTOutput,
    batch: BarGPTBatch,
    config: TrainConfig,
    quantiles: tuple[float, ...],
    *,
    collect_target_stats: bool = True,
) -> BarGPTLoss:
    """Mean each retained v3 family, then sum families without coefficients."""
    if batch.horizon_targets is None or batch.horizon_mask is None:
        raise ValueError("physical horizon targets must be materialized before loss computation")

    ar_regression_stats: tuple[torch.Tensor, torch.Tensor] | None = None
    ar_binary_stats: tuple[torch.Tensor, torch.Tensor] | None = None
    ar_gap_stats: tuple[torch.Tensor, torch.Tensor] | None = None
    for view, prediction in output.autoregressive.items():
        target = batch.autoregressive_targets[view][:, : prediction.shape[1]]
        mask = batch.autoregressive_mask[view][:, : prediction.shape[1]]
        continuous_prediction = prediction[..., :AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT]
        continuous_target = target[..., :AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT]
        continuous_mask = mask[..., :AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT]
        ar_regression_stats = _add_stats(
            ar_regression_stats,
            _point_regression_stats(
                continuous_prediction, continuous_target, continuous_mask
            ),
        )
        ar_binary_stats = _add_stats(
            ar_binary_stats,
            _binary_stats(
                prediction[..., AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT:],
                target[..., AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT:],
                mask[..., AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT:],
            ),
        )
        ar_gap_stats = _add_stats(
            ar_gap_stats,
            _gap_stats(
                output.autoregressive_gap_logits[view],
                batch.autoregressive_gap_labels[view][
                    :, : prediction.shape[1]
                ],
                batch.autoregressive_gap_mask[view][
                    :, : prediction.shape[1]
                ],
            ),
        )

    if ar_regression_stats is None or ar_binary_stats is None or ar_gap_stats is None:
        zero = output.embeddings.sum() * 0.0
        raise ValueError(f"v3 requires every autoregressive objective; got zero={zero}")
    ar_regression = _target_means(*ar_regression_stats)
    ar_binary = _target_means(*ar_binary_stats)
    ar_gap = _target_means(*ar_gap_stats)

    continuous_target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
    continuous_mask = (
        batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT]
        & batch.origin_mask[:, :, None, None]
    )
    if output.horizon_quantiles is None:
        raise ValueError("v3 requires physical-horizon quantile predictions")
    horizon_regression_stats = _quantile_stats(
        output.horizon_quantiles,
        continuous_target,
        continuous_mask,
        quantiles,
    )
    horizon_regression = _target_means(*horizon_regression_stats)

    if output.horizon_availability_logits is None:
        raise ValueError("v3 requires physical-horizon categorical predictions")
    binary_target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
    binary_mask = (
        batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:]
        & batch.origin_mask[:, :, None, None]
    )
    horizon_binary_stats = _binary_stats(
        output.horizon_availability_logits, binary_target, binary_mask
    )
    horizon_binary = _target_means(*horizon_binary_stats)

    ar_loss = ar_regression.mean() + ar_binary.mean() + ar_gap.mean()
    horizon_loss = horizon_regression.mean() + horizon_binary.mean()
    total = ar_loss + horizon_loss
    # Only the seven losses needed for optimization observability remain on
    # the every-microbatch metric path. Per-target numerators and supports are
    # carried separately for scheduled evaluation, avoiding 130 dictionary
    # entries and detach operations in normal training telemetry.
    metrics: dict[str, torch.Tensor] = {
        "train/loss": total.detach(),
        "train/loss_ar_regression": ar_regression.mean().detach(),
        "train/loss_ar_categorical": ar_binary.mean().detach(),
        "train/loss_ar_time_to_event": ar_gap.mean().detach(),
        "train/loss_horizon_quantile": horizon_regression.mean().detach(),
        "train/loss_horizon_categorical": horizon_binary.mean().detach(),
    }
    target_stats: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    if collect_target_stats:
        # Validation needs per-target numerators/supports to combine batches
        # exactly. The optimizer path only consumes the already-normalized
        # scalar objectives above, so avoid constructing and detaching dozens
        # of Python dictionary entries on every training microbatch.
        for group, names, means, support in (
            (
                "ar_regression",
                AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES,
                ar_regression,
                ar_regression_stats[1],
            ),
            (
                "ar_categorical",
                AUTOREGRESSIVE_BINARY_TARGET_NAMES,
                ar_binary,
                ar_binary_stats[1],
            ),
            ("ar_time_to_event", ("gap_class",), ar_gap, ar_gap_stats[1]),
            (
                "horizon_regression",
                CONTINUOUS_TARGET_NAMES,
                horizon_regression,
                horizon_regression_stats[1],
            ),
            (
                "horizon_categorical",
                BINARY_TARGET_NAMES,
                horizon_binary,
                horizon_binary_stats[1],
            ),
        ):
            _record_target_stats(
                target_stats,
                group=group,
                names=names,
                means=means,
                support=support,
            )
    return BarGPTLoss(loss=total, metrics=metrics, target_stats=target_stats)
