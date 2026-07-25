from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v17.data import NewsResponseBatch
from research.news_reaction_model.v17.model import NewsResponseOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def _masked_mean_loss(
    logits: torch.Tensor,
    targets: torch.Tensor,
    mask: torch.Tensor,
) -> tuple[torch.Tensor, int, int]:
    if not bool(mask.any()):
        return logits.sum() * 0.0, 0, 0
    selected_logits = logits[mask].float()
    selected_targets = targets[mask]
    loss = F.cross_entropy(selected_logits, selected_targets)
    correct = int((selected_logits.argmax(dim=-1) == selected_targets).sum().detach().cpu())
    return loss, int(selected_targets.numel()), correct


def compute_loss(output: NewsResponseOutput, batch: NewsResponseBatch) -> LossResult:
    components: dict[str, tuple[torch.Tensor, int, int]] = {
        "direction": _masked_mean_loss(
            output.direction_logits, batch.direction, batch.window_mask
        ),
        "path": _masked_mean_loss(output.path_logits, batch.path, batch.window_mask),
        "flow": _masked_mean_loss(output.flow_logits, batch.flow, batch.window_mask),
        "persistence": _masked_mean_loss(
            output.persistence_logits,
            batch.persistence,
            batch.persistence_mask,
        ),
    }
    active = [value[0] for value in components.values() if value[1] > 0]
    loss = torch.stack(active).mean() if active else output.article_embedding.sum() * 0.0
    metrics = {"train/loss": float(loss.detach().cpu())}
    for name, (component_loss, count, correct) in components.items():
        metrics[f"train/{name}/loss"] = (
            float(component_loss.detach().cpu()) if count else 0.0
        )
        metrics[f"train/{name}/accuracy"] = correct / max(count, 1)
        metrics[f"train/{name}/labels"] = float(count)
    return LossResult(loss=loss, metrics=metrics)
