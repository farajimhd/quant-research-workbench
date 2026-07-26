from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v18.model import EpisodeResponseOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def compute_loss(
    output: EpisodeResponseOutput,
    batch: EpisodeBatch,
    *,
    regression_weight: float = 1.0,
) -> LossResult:
    mask = batch.target_mask.bool()
    if not bool(mask.any()):
        zero = output.article_embedding.sum() * 0
        return LossResult(zero, {"train/loss": 0.0, "train/labels": 0.0})
    direction = F.cross_entropy(output.direction_logits[mask].float(), batch.direction[mask])
    path = F.cross_entropy(output.path_logits[mask].float(), batch.path[mask])
    flow = F.cross_entropy(output.flow_logits[mask].float(), batch.flow[mask])
    regression = F.smooth_l1_loss(
        output.regression[mask].float(), batch.regression_targets[mask].float(), beta=1.0
    )
    loss = torch.stack((direction, path, flow, regression_weight * regression)).mean()
    return LossResult(
        loss,
        {
            "train/loss": float(loss.detach().cpu()),
            "train/direction_loss": float(direction.detach().cpu()),
            "train/path_loss": float(path.detach().cpu()),
            "train/flow_loss": float(flow.detach().cpu()),
            "train/regression_loss": float(regression.detach().cpu()),
            "train/labels": float(mask.sum().detach().cpu()),
        },
    )
