from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v10.data import NewsReactionBatch
from research.news_reaction_model.v10.model import NewsReactionOpportunityOutput
from research.news_reaction_model.v10.opportunity import opportunity_targets


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def compute_loss(
    output: NewsReactionOpportunityOutput,
    batch: NewsReactionBatch,
) -> LossResult:
    targets = opportunity_targets(batch.return_targets, batch.label_mask)
    loss_sum = output.article_embedding.sum() * 0.0
    valid_count = exact_correct = 0
    for horizon, actual in targets.items():
        valid = actual >= 0
        if not bool(valid.any()):
            continue
        logits = output.logits[horizon][valid].float()
        actual = actual[valid]
        loss_sum = loss_sum + F.cross_entropy(logits, actual, reduction="sum")
        predicted = logits.argmax(dim=-1)
        exact_correct += int((predicted == actual).sum().detach().cpu())
        valid_count += int(actual.numel())
    if valid_count == 0:
        return LossResult(loss_sum, {"train/loss": 0.0, "train/valid_labels": 0.0})
    loss = loss_sum / valid_count
    return LossResult(
        loss,
        {
            "train/loss": float(loss.detach().cpu()),
            "train/cross_entropy": float(loss.detach().cpu()),
            "train/accuracy": exact_correct / valid_count,
            "train/valid_labels": float(valid_count),
        },
    )
