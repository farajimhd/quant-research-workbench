from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v1.data import NewsReactionBatch
from research.news_reaction_model.v1.model import NewsReactionOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def compute_loss(output: NewsReactionOutput, batch: NewsReactionBatch, *, return_weight: float = 0.25) -> LossResult:
    mask = batch.label_mask.bool() & (batch.class_targets >= 0)
    if not bool(mask.any()):
        zero = output.class_logits.sum() * 0.0
        return LossResult(zero, {"train/loss": 0.0, "train/valid_labels": 0.0})
    class_loss = F.cross_entropy(output.class_logits[mask], batch.class_targets[mask], reduction="mean")
    return_loss = F.huber_loss(output.return_forecasts[mask], batch.return_targets[mask], reduction="mean", delta=0.01)
    loss = class_loss + float(return_weight) * return_loss
    with torch.no_grad():
        predicted = output.class_logits[mask].argmax(dim=-1)
        accuracy = (predicted == batch.class_targets[mask]).float().mean()
        mae = (output.return_forecasts[mask] - batch.return_targets[mask]).abs().mean()
    return LossResult(loss, {
        "train/loss": float(loss.detach().cpu()),
        "train/loss_class": float(class_loss.detach().cpu()),
        "train/loss_return": float(return_loss.detach().cpu()),
        "train/accuracy": float(accuracy.detach().cpu()),
        "train/return_mae": float(mae.detach().cpu()),
        "train/valid_labels": float(mask.sum().detach().cpu()),
    })
