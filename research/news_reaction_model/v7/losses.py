from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v7.data import NewsReactionBatch
from research.news_reaction_model.v7.model import NewsReactionRangeOutput
from research.news_reaction_model.v7.ranges import TARGET_NAMES, range_targets


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def ordinal_cdf_loss(logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    probabilities = torch.softmax(logits.float(), dim=-1)
    predicted_cdf = probabilities.cumsum(dim=-1)[..., :-1]
    thresholds = torch.arange(logits.shape[-1] - 1, device=logits.device)
    target_cdf = (targets.unsqueeze(-1) <= thresholds).float()
    return (predicted_cdf - target_cdf).abs().mean()


def compute_loss(
    output: NewsReactionRangeOutput,
    batch: NewsReactionBatch,
    *,
    ordinal_weight: float = 0.25,
) -> LossResult:
    targets = range_targets(batch.return_targets, batch.label_mask)
    losses: list[torch.Tensor] = []
    cross_entropies: list[torch.Tensor] = []
    ordinals: list[torch.Tensor] = []
    exact_correct = within_one_correct = valid_count = 0
    for horizon, horizon_targets in targets.items():
        for target_index, target_name in enumerate(TARGET_NAMES):
            actual = horizon_targets[:, target_index]
            valid = actual >= 0
            if not bool(valid.any()):
                continue
            logits = output.logits[horizon][target_name][valid]
            actual = actual[valid]
            cross_entropy = F.cross_entropy(logits.float(), actual)
            ordinal = ordinal_cdf_loss(logits, actual)
            losses.append(cross_entropy + float(ordinal_weight) * ordinal)
            cross_entropies.append(cross_entropy)
            ordinals.append(ordinal)
            predicted = logits.argmax(dim=-1)
            exact_correct += int((predicted == actual).sum().detach().cpu())
            within_one_correct += int(((predicted - actual).abs() <= 1).sum().detach().cpu())
            valid_count += int(actual.numel())
    if not losses:
        zero = output.article_embedding.sum() * 0.0
        return LossResult(zero, {"train/loss": 0.0, "train/valid_labels": 0.0})
    loss = torch.stack(losses).mean()
    return LossResult(loss, {
        "train/loss": float(loss.detach().cpu()),
        "train/cross_entropy": float(torch.stack(cross_entropies).mean().detach().cpu()),
        "train/ordinal_loss": float(torch.stack(ordinals).mean().detach().cpu()),
        "train/accuracy": exact_correct / max(valid_count, 1),
        "train/within_one_bin_accuracy": within_one_correct / max(valid_count, 1),
        "train/valid_labels": float(valid_count),
    })

