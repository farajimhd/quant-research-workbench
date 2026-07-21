from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v2.data import NewsReactionBatch
from research.news_reaction_model.v2.model import NewsReactionOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def compute_loss(output: NewsReactionOutput, batch: NewsReactionBatch) -> LossResult:
    mask = batch.label_mask.bool()
    if not bool(mask.any()):
        zero = output.return_forecasts.sum() * 0.0
        return LossResult(zero, {"train/loss": 0.0, "train/mse": 0.0, "train/valid_labels": 0.0})
    forecasts = output.return_forecasts[mask].float()
    targets = batch.return_targets[mask].float()
    loss = F.mse_loss(forecasts, targets, reduction="mean")
    with torch.no_grad():
        absolute_error = (forecasts - targets).abs()
    return LossResult(loss, {
        "train/loss": float(loss.detach().cpu()),
        "train/mse": float(loss.detach().cpu()),
        "train/rmse": float(torch.sqrt(loss.detach()).cpu()),
        "train/mae": float(absolute_error.mean().detach().cpu()),
        "train/target_mae": float(absolute_error[:, 0].mean().detach().cpu()),
        "train/high_mae": float(absolute_error[:, 1].mean().detach().cpu()),
        "train/low_mae": float(absolute_error[:, 2].mean().detach().cpu()),
        "train/valid_labels": float(mask.sum().detach().cpu()),
    })
