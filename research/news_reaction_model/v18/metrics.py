from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v16.metrics import balanced_accuracy, macro_f1
from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v18.model import EpisodeResponseOutput
from research.news_reaction_model.v18.targets import (
    DIRECTION_NAMES, FLOW_NAMES, PATH_NAMES, REGRESSION_NAMES,
)


@dataclass(slots=True)
class EpisodeAccumulator:
    confusion: dict[str, np.ndarray] = field(default_factory=dict)
    regression_abs_error: np.ndarray = field(default_factory=lambda: np.zeros(3))
    regression_sq_error: np.ndarray = field(default_factory=lambda: np.zeros(3))
    regression_count: int = 0

    @torch.no_grad()
    def add(self, output: EpisodeResponseOutput, batch: EpisodeBatch) -> None:
        mask = batch.target_mask.bool()
        if not bool(mask.any()):
            return
        for name, logits, targets, names in (
            ("direction", output.direction_logits, batch.direction, DIRECTION_NAMES),
            ("path", output.path_logits, batch.path, PATH_NAMES),
            ("flow", output.flow_logits, batch.flow, FLOW_NAMES),
        ):
            matrix = self.confusion.setdefault(
                name, np.zeros((len(names), len(names)), dtype=np.int64)
            )
            actual = targets[mask].detach().cpu().numpy()
            predicted = logits[mask].argmax(dim=-1).detach().cpu().numpy()
            np.add.at(matrix, (actual, predicted), 1)
        error = (
            output.regression[mask].float() - batch.regression_targets[mask].float()
        ).detach().cpu().numpy()
        self.regression_abs_error += np.abs(error).sum(axis=0)
        self.regression_sq_error += np.square(error).sum(axis=0)
        self.regression_count += int(error.shape[0])

    def compute(self, prefix: str) -> dict[str, float]:
        result: dict[str, float] = {}
        f1: list[float] = []
        for name, matrix in self.confusion.items():
            total = int(matrix.sum())
            result[f"{prefix}/{name}/accuracy"] = float(np.trace(matrix) / max(total, 1))
            result[f"{prefix}/{name}/macro_f1"] = macro_f1(matrix)
            result[f"{prefix}/{name}/balanced_accuracy"] = balanced_accuracy(matrix)
            result[f"{prefix}/{name}/samples"] = float(total)
            f1.append(result[f"{prefix}/{name}/macro_f1"])
        result[f"{prefix}/macro_head_f1"] = float(np.mean(f1)) if f1 else 0.0
        for index, name in enumerate(REGRESSION_NAMES):
            result[f"{prefix}/{name}/mae_pct"] = float(
                self.regression_abs_error[index] / max(self.regression_count, 1)
            )
            result[f"{prefix}/{name}/rmse_pct"] = float(
                np.sqrt(self.regression_sq_error[index] / max(self.regression_count, 1))
            )
        return result
