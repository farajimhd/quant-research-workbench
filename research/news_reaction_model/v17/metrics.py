from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v16.metrics import balanced_accuracy, macro_f1
from research.news_reaction_model.v17.data import NewsResponseBatch
from research.news_reaction_model.v17.model import NewsResponseOutput
from research.news_reaction_model.v17.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    PERSISTENCE_NAMES,
)


@dataclass(slots=True)
class _HeadStats:
    confusion: np.ndarray
    count: int = 0
    correct: int = 0


@dataclass(slots=True)
class ResponseAccumulator:
    stats: dict[str, _HeadStats] = field(default_factory=dict)

    @torch.no_grad()
    def add(self, output: NewsResponseOutput, batch: NewsResponseBatch) -> None:
        values = (
            ("direction", output.direction_logits, batch.direction, batch.window_mask, DIRECTION_NAMES),
            ("path", output.path_logits, batch.path, batch.window_mask, PATH_NAMES),
            ("flow", output.flow_logits, batch.flow, batch.window_mask, FLOW_NAMES),
            (
                "persistence",
                output.persistence_logits,
                batch.persistence,
                batch.persistence_mask,
                PERSISTENCE_NAMES,
            ),
        )
        for name, logits, target, mask, names in values:
            selected_target = target[mask].detach().cpu().numpy()
            selected_prediction = logits[mask].argmax(dim=-1).detach().cpu().numpy()
            stat = self.stats.setdefault(
                name,
                _HeadStats(np.zeros((len(names), len(names)), dtype=np.int64)),
            )
            np.add.at(stat.confusion, (selected_target, selected_prediction), 1)
            stat.count += int(selected_target.size)
            stat.correct += int(np.count_nonzero(selected_target == selected_prediction))

    def compute(self, prefix: str = "val") -> dict[str, float]:
        metrics: dict[str, float] = {}
        f1_values: list[float] = []
        for name, stat in self.stats.items():
            metrics[f"{prefix}/{name}/samples"] = float(stat.count)
            metrics[f"{prefix}/{name}/accuracy"] = stat.correct / max(stat.count, 1)
            metrics[f"{prefix}/{name}/macro_f1"] = macro_f1(stat.confusion)
            metrics[f"{prefix}/{name}/balanced_accuracy"] = balanced_accuracy(stat.confusion)
            f1_values.append(metrics[f"{prefix}/{name}/macro_f1"])
        metrics[f"{prefix}/macro_head_f1"] = float(np.mean(f1_values)) if f1_values else 0.0
        return metrics
