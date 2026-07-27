from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v20.data import EpisodeBatch
from research.news_reaction_model.v20.model import ReturnDistributionOutput
from research.news_reaction_model.v20.targets import (
    DOWN_RETURN_EDGES,
    FLAT_BUCKET_INDEX,
    TrainingStatistics,
    UP_RETURN_EDGES,
)


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    components: dict[str, torch.Tensor]
    metrics: dict[str, float]


def signed_opportunity_torch(batch: EpisodeBatch) -> torch.Tensor:
    return torch.where(
        batch.direction == 1,
        batch.regression_targets[:, 0].clamp_min(0),
        torch.where(
            batch.direction == 2,
            batch.regression_targets[:, 1].clamp_max(0),
            torch.zeros_like(batch.regression_targets[:, 0]),
        ),
    )


def bucketize_torch(signed_return: torch.Tensor) -> torch.Tensor:
    result = torch.full_like(
        signed_return, FLAT_BUCKET_INDEX, dtype=torch.long
    )
    negative = signed_return < 0
    positive = signed_return > 0
    if bool(negative.any()):
        boundaries = signed_return.new_tensor(DOWN_RETURN_EDGES[1:-1])
        result[negative] = torch.bucketize(
            signed_return[negative], boundaries, right=True
        )
    if bool(positive.any()):
        boundaries = signed_return.new_tensor(UP_RETURN_EDGES[1:-1])
        result[positive] = (
            FLAT_BUCKET_INDEX
            + 1
            + torch.bucketize(signed_return[positive], boundaries, right=True)
        )
    return result


def compute_loss(
    output: ReturnDistributionOutput,
    batch: EpisodeBatch,
    training_statistics: TrainingStatistics,
    *,
    cross_entropy_weight: float = 1.0,
    ordinal_crps_weight: float = 0.5,
    direction_weight: float = 0.5,
    expected_return_weight: float = 0.1,
    router_balance_weight: float = 0.01,
) -> LossResult:
    mask = batch.target_mask.bool()
    if not bool(mask.any()):
        zero = output.article_embedding.sum() * 0
        return LossResult(
            zero,
            {"distribution": zero},
            {"train/loss": 0.0, "train/labels": 0.0},
        )
    signed = signed_opportunity_torch(batch)[mask].float()
    buckets = bucketize_torch(signed)
    logits = output.return_logits[mask].float()
    probabilities = output.return_probabilities[mask].float()
    class_weights = torch.tensor(
        training_statistics.bucket_weights,
        device=logits.device,
        dtype=torch.float32,
    )
    cross_entropy = F.cross_entropy(logits, buckets, weight=class_weights)

    predicted_cdf = probabilities.cumsum(dim=-1)[:, :-1]
    thresholds = torch.arange(
        predicted_cdf.shape[-1], device=logits.device
    ).unsqueeze(0)
    target_cdf = (thresholds >= buckets.unsqueeze(1)).float()
    ordinal_crps = torch.square(predicted_cdf - target_cdf).mean()

    direction_probabilities = output.direction_probabilities[mask].float()
    direction_nll = F.nll_loss(
        torch.log(direction_probabilities.clamp_min(1e-12)),
        batch.direction[mask],
    )
    expected_return = F.smooth_l1_loss(
        output.expected_return[mask].float()
        / training_statistics.signed_return_scale,
        signed / training_statistics.signed_return_scale,
        beta=1.0,
    )
    router = output.router_probabilities[mask].float()
    expert_count = router.shape[-1]
    importance = router.mean(dim=0)
    router_balance = expert_count * torch.square(
        importance - (1.0 / expert_count)
    ).sum()

    components = {
        "cross_entropy": cross_entropy_weight * cross_entropy,
        "ordinal_crps": ordinal_crps_weight * ordinal_crps,
        "direction": direction_weight * direction_nll,
        "expected_return": expected_return_weight * expected_return,
        "router_balance": router_balance_weight * router_balance,
    }
    loss = torch.stack(tuple(components.values())).sum()
    return LossResult(
        loss,
        components,
        {
            "train/loss": float(loss.detach().cpu()),
            "train/cross_entropy_loss": float(cross_entropy.detach().cpu()),
            "train/ordinal_crps_loss": float(ordinal_crps.detach().cpu()),
            "train/direction_loss": float(direction_nll.detach().cpu()),
            "train/expected_return_loss": float(expected_return.detach().cpu()),
            "train/router_balance_loss": float(router_balance.detach().cpu()),
            "train/labels": float(mask.sum().detach().cpu()),
        },
    )


__all__ = [
    "LossResult",
    "bucketize_torch",
    "compute_loss",
    "signed_opportunity_torch",
]
