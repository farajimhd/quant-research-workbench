from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v3.data import NewsReactionBatch
from research.news_reaction_model.v3.model import NewsReactionOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def hierarchical_targets(batch: NewsReactionBatch) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    actionable = (batch.class_targets != 1).long()
    direction = (batch.class_targets == 2).long()
    magnitudes = batch.return_targets.abs()
    return actionable, direction, magnitudes


def compute_loss(
    output: NewsReactionOutput,
    batch: NewsReactionBatch,
    *,
    direction_weight: float = 1.0,
    magnitude_weight: float = 0.25,
) -> LossResult:
    valid = batch.label_mask.bool() & (batch.class_targets >= 0)
    if not bool(valid.any()):
        zero = output.actionable_logits.sum() * 0.0
        return LossResult(zero, {"train/loss": 0.0, "train/valid_labels": 0.0})
    actionable_target, direction_target, magnitude_target = hierarchical_targets(batch)
    actionable_loss = F.cross_entropy(output.actionable_logits[valid], actionable_target[valid])
    active = valid & actionable_target.bool()
    if bool(active.any()):
        direction_loss = F.cross_entropy(output.direction_logits[active], direction_target[active])
        magnitude_loss = F.huber_loss(
            output.magnitude_forecasts[active], magnitude_target[active], reduction="mean", delta=0.01,
        )
    else:
        direction_loss = output.direction_logits.sum() * 0.0
        magnitude_loss = output.magnitude_forecasts.sum() * 0.0
    loss = actionable_loss + float(direction_weight) * direction_loss + float(magnitude_weight) * magnitude_loss
    with torch.no_grad():
        predicted = output.positions()
        actual = torch.where(actionable_target.bool(), torch.where(direction_target.bool(), 1, -1), 0)
        three_class_accuracy = (predicted[valid] == actual[valid]).float().mean()
        actionable_accuracy = (output.actionable_logits[valid].argmax(dim=-1) == actionable_target[valid]).float().mean()
        direction_accuracy = (
            (output.direction_logits[active].argmax(dim=-1) == direction_target[active]).float().mean()
            if bool(active.any()) else torch.zeros((), device=loss.device)
        )
        magnitude_mae = (
            (output.magnitude_forecasts[active] - magnitude_target[active]).abs().mean()
            if bool(active.any()) else torch.zeros((), device=loss.device)
        )
    return LossResult(loss, {
        "train/loss": float(loss.detach().cpu()),
        "train/loss_actionable": float(actionable_loss.detach().cpu()),
        "train/loss_direction": float(direction_loss.detach().cpu()),
        "train/loss_magnitude": float(magnitude_loss.detach().cpu()),
        "train/accuracy": float(three_class_accuracy.detach().cpu()),
        "train/actionable_accuracy": float(actionable_accuracy.detach().cpu()),
        "train/direction_accuracy": float(direction_accuracy.detach().cpu()),
        "train/magnitude_mae": float(magnitude_mae.detach().cpu()),
        "train/valid_labels": float(valid.sum().detach().cpu()),
        "train/actionable_labels": float(active.sum().detach().cpu()),
    })
