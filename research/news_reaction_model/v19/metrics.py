from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v19.data import EpisodeBatch
from research.news_reaction_model.v19.model import EpisodeResponseOutput
from research.news_reaction_model.v19.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    REGRESSION_NAMES,
)


def balanced_accuracy(matrix: np.ndarray) -> float:
    support = matrix.sum(axis=1)
    recall = np.diag(matrix) / np.maximum(support, 1)
    return float(np.mean(recall))


def macro_f1(matrix: np.ndarray) -> float:
    support = matrix.sum(axis=1)
    predicted = matrix.sum(axis=0)
    true_positive = np.diag(matrix)
    precision = true_positive / np.maximum(predicted, 1)
    recall = true_positive / np.maximum(support, 1)
    scores = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    return float(np.mean(scores))


@dataclass(slots=True)
class EpisodeAccumulator:
    regression_training_median: tuple[float, float, float]
    confusion: dict[str, np.ndarray] = field(default_factory=dict)
    regression_abs_error: np.ndarray = field(default_factory=lambda: np.zeros(3))
    regression_sq_error: np.ndarray = field(default_factory=lambda: np.zeros(3))
    baseline_abs_error: np.ndarray = field(default_factory=lambda: np.zeros(3))
    regression_count: int = 0
    coherence_violations: int = 0

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
        prediction = output.regression[mask].float().detach().cpu().numpy()
        targets = batch.regression_targets[mask].float().detach().cpu().numpy()
        error = prediction - targets
        self.regression_abs_error += np.abs(error).sum(axis=0)
        self.regression_sq_error += np.square(error).sum(axis=0)
        baseline = np.asarray(self.regression_training_median, dtype=np.float64)
        self.baseline_abs_error += np.abs(targets - baseline).sum(axis=0)
        self.regression_count += int(error.shape[0])
        self.coherence_violations += int(
            np.sum(
                (prediction[:, 1] > prediction[:, 2])
                | (prediction[:, 2] > prediction[:, 0])
            )
        )

    def compute(self, prefix: str) -> dict[str, float]:
        result: dict[str, float] = {}
        head_f1: list[float] = []
        name_sets = {
            "direction": DIRECTION_NAMES,
            "path": PATH_NAMES,
            "flow": FLOW_NAMES,
        }
        for name, matrix in self.confusion.items():
            total = int(matrix.sum())
            result[f"{prefix}/{name}/accuracy"] = float(np.trace(matrix) / max(total, 1))
            result[f"{prefix}/{name}/macro_f1"] = macro_f1(matrix)
            result[f"{prefix}/{name}/balanced_accuracy"] = balanced_accuracy(matrix)
            result[f"{prefix}/{name}/samples"] = float(total)
            head_f1.append(result[f"{prefix}/{name}/macro_f1"])
            for index, class_name in enumerate(name_sets[name]):
                true_count = int(matrix[index].sum())
                predicted_count = int(matrix[:, index].sum())
                true_positive = int(matrix[index, index])
                precision = true_positive / max(predicted_count, 1)
                recall = true_positive / max(true_count, 1)
                f1 = 2 * precision * recall / max(precision + recall, 1e-12)
                base = f"{prefix}/{name}/class/{class_name}"
                result[f"{base}/support"] = float(true_count)
                result[f"{base}/predicted"] = float(predicted_count)
                result[f"{base}/precision"] = float(precision)
                result[f"{base}/recall"] = float(recall)
                result[f"{base}/f1"] = float(f1)
        result[f"{prefix}/macro_head_f1"] = (
            float(np.mean(head_f1)) if head_f1 else 0.0
        )
        regression_skills: list[float] = []
        for index, name in enumerate(REGRESSION_NAMES):
            mae = self.regression_abs_error[index] / max(self.regression_count, 1)
            baseline_mae = self.baseline_abs_error[index] / max(self.regression_count, 1)
            skill = 1.0 - mae / max(baseline_mae, 1e-12)
            result[f"{prefix}/{name}/mae_pct"] = float(mae)
            result[f"{prefix}/{name}/rmse_pct"] = float(
                np.sqrt(self.regression_sq_error[index] / max(self.regression_count, 1))
            )
            result[f"{prefix}/{name}/train_median_baseline_mae_pct"] = float(
                baseline_mae
            )
            result[f"{prefix}/{name}/mae_skill"] = float(skill)
            regression_skills.append(float(np.clip(skill, -1.0, 1.0)))
        regression_skill = float(np.mean(regression_skills)) if regression_skills else 0.0
        result[f"{prefix}/regression_mean_mae_skill"] = regression_skill
        result[f"{prefix}/joint_score"] = float(
            np.mean((*head_f1, regression_skill)) if head_f1 else regression_skill
        )
        result[f"{prefix}/regression_coherence_violations"] = float(
            self.coherence_violations
        )
        return result
