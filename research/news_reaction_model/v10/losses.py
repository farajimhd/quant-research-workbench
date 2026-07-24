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
    horizon_loss_sums: dict[str, float]
    horizon_counts: dict[str, int]
    horizon_correct: dict[str, int]


def compute_loss(
    output: NewsReactionOpportunityOutput,
    batch: NewsReactionBatch,
) -> LossResult:
    targets = opportunity_targets(batch.return_targets, batch.label_mask)
    zero = output.article_embedding.sum() * 0.0
    horizon_means: list[torch.Tensor] = []
    horizon_loss_sums: dict[str, float] = {}
    horizon_counts: dict[str, int] = {}
    horizon_correct: dict[str, int] = {}
    for horizon, actual in targets.items():
        valid = actual >= 0
        if not bool(valid.any()):
            continue
        logits = output.logits[horizon][valid].float()
        actual = actual[valid]
        loss_sum = F.cross_entropy(logits, actual, reduction="sum")
        count = int(actual.numel())
        horizon_means.append(loss_sum / count)
        predicted = logits.argmax(dim=-1)
        horizon_loss_sums[horizon] = float(loss_sum.detach().cpu())
        horizon_counts[horizon] = count
        horizon_correct[horizon] = int((predicted == actual).sum().detach().cpu())
    if not horizon_means:
        return LossResult(
            zero,
            {
                "train/loss": 0.0,
                "train/macro_horizon_log_loss": 0.0,
                "train/micro_log_loss": 0.0,
                "train/accuracy": 0.0,
                "train/valid_labels": 0.0,
            },
            {},
            {},
            {},
        )
    loss = torch.stack(horizon_means).mean()
    valid_count = sum(horizon_counts.values())
    exact_correct = sum(horizon_correct.values())
    micro_loss = sum(horizon_loss_sums.values()) / valid_count
    return LossResult(
        loss,
        {
            "train/loss": float(loss.detach().cpu()),
            "train/macro_horizon_log_loss": float(loss.detach().cpu()),
            "train/micro_log_loss": micro_loss,
            "train/accuracy": exact_correct / valid_count,
            "train/valid_labels": float(valid_count),
        },
        horizon_loss_sums,
        horizon_counts,
        horizon_correct,
    )
