from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v19.data import EpisodeBatch
from research.news_reaction_model.v19.model import EpisodeResponseOutput
from research.news_reaction_model.v19.targets import TrainingStatistics


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    components: dict[str, torch.Tensor]
    metrics: dict[str, float]


def compute_loss(
    output: EpisodeResponseOutput,
    batch: EpisodeBatch,
    training_statistics: TrainingStatistics,
    *,
    tasks: set[str] | None = None,
    regression_weight: float = 1.0,
) -> LossResult:
    selected = tasks or {"direction", "path", "flow", "regression"}
    mask = batch.target_mask.bool()
    if not bool(mask.any()):
        zero = output.article_embedding.sum() * 0
        return LossResult(
            zero,
            {task: zero for task in selected},
            {"train/loss": 0.0, "train/labels": 0.0},
        )
    device = output.article_embedding.device
    direction_weight = torch.tensor(
        training_statistics.direction_weights, device=device, dtype=torch.float32
    )
    path_weight = torch.tensor(
        training_statistics.path_weights, device=device, dtype=torch.float32
    )
    flow_weight = torch.tensor(
        training_statistics.flow_weights, device=device, dtype=torch.float32
    )
    direction = F.cross_entropy(
        output.direction_logits[mask].float(),
        batch.direction[mask],
        weight=direction_weight,
    )
    path = F.cross_entropy(
        output.path_logits[mask].float(),
        batch.path[mask],
        weight=path_weight,
    )
    flow = F.cross_entropy(
        output.flow_logits[mask].float(),
        batch.flow[mask],
        weight=flow_weight,
    )

    targets = batch.regression_targets[mask].float()
    terminal = targets[:, 2]
    target_components = torch.stack(
        (
            terminal,
            torch.clamp(targets[:, 0] - terminal, min=0.0),
            torch.clamp(terminal - targets[:, 1], min=0.0),
        ),
        dim=1,
    )
    normalized_targets = target_components / output.regression_scales[mask].float()
    regression = F.smooth_l1_loss(
        output.regression_components[mask].float(),
        normalized_targets.float(),
        beta=1.0,
    )
    components = {
        "direction": direction,
        "path": path,
        "flow": flow,
        "regression": regression_weight * regression,
    }
    loss = torch.stack([components[task] for task in sorted(selected)]).mean()
    metrics = {
        "train/loss": float(loss.detach().cpu()),
        "train/direction_loss": float(direction.detach().cpu()),
        "train/path_loss": float(path.detach().cpu()),
        "train/flow_loss": float(flow.detach().cpu()),
        "train/regression_loss": float(regression.detach().cpu()),
        "train/labels": float(mask.sum().detach().cpu()),
    }
    return LossResult(loss, components, metrics)
