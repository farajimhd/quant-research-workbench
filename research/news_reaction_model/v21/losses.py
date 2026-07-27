from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v21.data import EpisodeBatch
from research.news_reaction_model.v21.model import HierarchicalReturnOutput
from research.news_reaction_model.v21.targets import (
    FLAT_BUCKET_INDEX,
    MAGNITUDE_BUCKET_COUNT,
    MAGNITUDE_EDGES,
    TrainingStatistics,
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


def magnitude_bucketize_torch(magnitude: torch.Tensor) -> torch.Tensor:
    if bool((magnitude < 0).any()):
        raise ValueError("Magnitude targets must be non-negative.")
    boundaries = magnitude.new_tensor(MAGNITUDE_EDGES[1:-1])
    return torch.bucketize(magnitude, boundaries, right=True)


def signed_bucketize_torch(signed_return: torch.Tensor) -> torch.Tensor:
    magnitude_bucket = magnitude_bucketize_torch(signed_return.abs())
    return torch.where(
        signed_return < 0,
        MAGNITUDE_BUCKET_COUNT - 1 - magnitude_bucket,
        torch.where(
            signed_return > 0,
            FLAT_BUCKET_INDEX + 1 + magnitude_bucket,
            torch.full_like(magnitude_bucket, FLAT_BUCKET_INDEX),
        ),
    )


def _ordinal_crps(
    probabilities: torch.Tensor,
    buckets: torch.Tensor,
) -> torch.Tensor:
    predicted_cdf = probabilities.cumsum(dim=-1)[:, :-1]
    thresholds = torch.arange(
        predicted_cdf.shape[-1], device=probabilities.device
    ).unsqueeze(0)
    target_cdf = (thresholds >= buckets.unsqueeze(1)).float()
    return torch.square(predicted_cdf - target_cdf).mean()


def compute_loss(
    output: HierarchicalReturnOutput,
    batch: EpisodeBatch,
    training_statistics: TrainingStatistics,
    *,
    direction_weight: float = 1.0,
    magnitude_cross_entropy_weight: float = 0.75,
    magnitude_ordinal_weight: float = 0.35,
    expected_magnitude_weight: float = 0.10,
    router_balance_weight: float = 0.01,
) -> LossResult:
    mask = batch.target_mask.bool()
    if not bool(mask.any()):
        zero = output.article_embedding.sum() * 0
        return LossResult(
            zero,
            {"direction": zero},
            {"train/loss": 0.0, "train/labels": 0.0},
        )
    direction = batch.direction[mask].long()
    signed = signed_opportunity_torch(batch)[mask].float()
    direction_weights = torch.tensor(
        training_statistics.direction_weights,
        device=direction.device,
        dtype=torch.float32,
    )
    direction_ce = F.cross_entropy(
        output.direction_logits[mask].float(),
        direction,
        weight=direction_weights,
    )

    magnitude_cross_entropy = direction_ce.new_zeros(())
    magnitude_ordinal = direction_ce.new_zeros(())
    expected_magnitude = direction_ce.new_zeros(())
    directional_sides = 0
    for direction_index, side_index in ((1, 0), (2, 1)):
        selected = direction == direction_index
        if not bool(selected.any()):
            continue
        directional_sides += 1
        target_magnitude = signed[selected].abs()
        buckets = magnitude_bucketize_torch(target_magnitude)
        logits = output.magnitude_logits[mask][selected, side_index].float()
        probabilities = output.magnitude_probabilities[mask][
            selected, side_index
        ].float()
        weights = torch.tensor(
            training_statistics.magnitude_weights[side_index],
            device=logits.device,
            dtype=torch.float32,
        )
        magnitude_cross_entropy = magnitude_cross_entropy + F.cross_entropy(
            logits, buckets, weight=weights
        )
        magnitude_ordinal = magnitude_ordinal + _ordinal_crps(
            probabilities, buckets
        )
        predicted = (
            output.expected_up_return[mask][selected]
            if side_index == 0
            else -output.expected_down_return[mask][selected]
        ).float()
        scale = training_statistics.magnitude_scale[side_index]
        expected_magnitude = expected_magnitude + F.smooth_l1_loss(
            predicted / scale,
            target_magnitude / scale,
            beta=1.0,
        )
    divisor = max(directional_sides, 1)
    magnitude_cross_entropy = magnitude_cross_entropy / divisor
    magnitude_ordinal = magnitude_ordinal / divisor
    expected_magnitude = expected_magnitude / divisor

    router = output.router_probabilities[mask].float()
    expert_count = router.shape[-1]
    importance = router.mean(dim=0)
    router_balance = expert_count * torch.square(
        importance - (1.0 / expert_count)
    ).sum()
    components = {
        "direction": direction_weight * direction_ce,
        "magnitude_cross_entropy": (
            magnitude_cross_entropy_weight * magnitude_cross_entropy
        ),
        "magnitude_ordinal": magnitude_ordinal_weight * magnitude_ordinal,
        "expected_magnitude": expected_magnitude_weight * expected_magnitude,
        "router_balance": router_balance_weight * router_balance,
    }
    loss = torch.stack(tuple(components.values())).sum()
    return LossResult(
        loss=loss,
        components=components,
        metrics={
            "train/loss": float(loss.detach().cpu()),
            "train/direction_loss": float(direction_ce.detach().cpu()),
            "train/magnitude_cross_entropy_loss": float(
                magnitude_cross_entropy.detach().cpu()
            ),
            "train/magnitude_ordinal_loss": float(
                magnitude_ordinal.detach().cpu()
            ),
            "train/expected_magnitude_loss": float(
                expected_magnitude.detach().cpu()
            ),
            "train/router_balance_loss": float(router_balance.detach().cpu()),
            "train/labels": float(mask.sum().detach().cpu()),
        },
    )


__all__ = [
    "LossResult",
    "compute_loss",
    "magnitude_bucketize_torch",
    "signed_bucketize_torch",
    "signed_opportunity_torch",
]
