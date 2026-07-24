from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn.functional as F

from research.news_reaction_model.v10.model import NewsReactionOpportunityOutput
from research.news_reaction_model.v10.losses import LossResult
from research.news_reaction_model.v10.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    OPPORTUNITY_CLASSES,
    opportunity_targets,
)


@dataclass(slots=True)
class _Stats:
    count: int = 0
    correct: int = 0
    log_loss: float = 0.0
    confidence_sum: float = 0.0
    confusion: np.ndarray = field(
        default_factory=lambda: np.zeros((OPPORTUNITY_CLASSES, OPPORTUNITY_CLASSES), dtype=np.int64)
    )


def macro_f1(confusion: np.ndarray) -> float:
    scores: list[float] = []
    for class_index in range(confusion.shape[0]):
        true_positive = float(confusion[class_index, class_index])
        false_positive = float(confusion[:, class_index].sum() - true_positive)
        false_negative = float(confusion[class_index, :].sum() - true_positive)
        denominator = 2.0 * true_positive + false_positive + false_negative
        scores.append(0.0 if denominator == 0.0 else 2.0 * true_positive / denominator)
    return float(np.mean(scores))


def balanced_accuracy(confusion: np.ndarray) -> float:
    recalls = []
    for class_index in range(confusion.shape[0]):
        denominator = float(confusion[class_index, :].sum())
        recalls.append(
            0.0 if denominator == 0.0 else float(confusion[class_index, class_index]) / denominator
        )
    return float(np.mean(recalls))


@dataclass(slots=True)
class OpportunityAccumulator:
    stats: dict[str, _Stats] = field(default_factory=lambda: defaultdict(_Stats))

    @torch.no_grad()
    def add(
        self,
        output: NewsReactionOpportunityOutput,
        returns: torch.Tensor,
        mask: torch.Tensor,
    ) -> None:
        targets = opportunity_targets(returns, mask)
        for horizon, actual in targets.items():
            valid = actual >= 0
            if not bool(valid.any()):
                continue
            logits = output.logits[horizon][valid].float()
            actual = actual[valid]
            probabilities = torch.softmax(logits, dim=-1)
            predicted = logits.argmax(dim=-1)
            confidence = probabilities.gather(1, predicted.unsqueeze(1)).squeeze(1)
            stat = self.stats[horizon]
            stat.count += int(actual.numel())
            stat.correct += int((predicted == actual).sum().cpu())
            stat.log_loss += float(F.cross_entropy(logits, actual, reduction="sum").cpu())
            stat.confidence_sum += float(confidence.sum().cpu())
            actual_cpu = actual.cpu().numpy()
            predicted_cpu = predicted.cpu().numpy()
            np.add.at(stat.confusion, (actual_cpu, predicted_cpu), 1)

    def compute(self, prefix: str = "val") -> dict[str, float]:
        result: dict[str, float] = {}
        total = _Stats()
        horizon_accuracy: list[float] = []
        horizon_macro_f1: list[float] = []
        horizon_balanced_accuracy: list[float] = []
        horizon_log_loss: list[float] = []
        for horizon, stat in sorted(self.stats.items()):
            count = max(stat.count, 1)
            accuracy = stat.correct / count
            horizon_f1 = macro_f1(stat.confusion)
            horizon_balanced = balanced_accuracy(stat.confusion)
            log_loss = stat.log_loss / count
            result[f"{prefix}/{horizon}/samples"] = float(stat.count)
            result[f"{prefix}/{horizon}/accuracy"] = accuracy
            result[f"{prefix}/{horizon}/macro_f1"] = horizon_f1
            result[f"{prefix}/{horizon}/balanced_accuracy"] = horizon_balanced
            result[f"{prefix}/{horizon}/log_loss"] = log_loss
            result[f"{prefix}/{horizon}/mean_confidence"] = stat.confidence_sum / count
            horizon_accuracy.append(accuracy)
            horizon_macro_f1.append(horizon_f1)
            horizon_balanced_accuracy.append(horizon_balanced)
            horizon_log_loss.append(log_loss)
            for class_index, class_name in enumerate(OPPORTUNITY_CLASS_NAMES):
                actual_count = int(stat.confusion[class_index, :].sum())
                predicted_count = int(stat.confusion[:, class_index].sum())
                result[f"{prefix}/{horizon}/{class_name}/support"] = float(actual_count)
                result[f"{prefix}/{horizon}/{class_name}/recall"] = (
                    float(stat.confusion[class_index, class_index]) / max(actual_count, 1)
                )
                result[f"{prefix}/{horizon}/{class_name}/predicted_share"] = (
                    predicted_count / count
                )
            total.count += stat.count
            total.correct += stat.correct
            total.log_loss += stat.log_loss
            total.confidence_sum += stat.confidence_sum
            total.confusion += stat.confusion
        denominator = max(total.count, 1)
        result[f"{prefix}/samples"] = float(total.count)
        result[f"{prefix}/accuracy"] = total.correct / denominator
        result[f"{prefix}/macro_f1"] = macro_f1(total.confusion)
        result[f"{prefix}/balanced_accuracy"] = balanced_accuracy(total.confusion)
        result[f"{prefix}/log_loss"] = total.log_loss / denominator
        result[f"{prefix}/mean_confidence"] = total.confidence_sum / denominator
        result[f"{prefix}/horizon_macro_accuracy"] = float(
            np.mean(horizon_accuracy)
        ) if horizon_accuracy else 0.0
        result[f"{prefix}/horizon_macro_f1"] = float(
            np.mean(horizon_macro_f1)
        ) if horizon_macro_f1 else 0.0
        result[f"{prefix}/horizon_macro_balanced_accuracy"] = float(
            np.mean(horizon_balanced_accuracy)
        ) if horizon_balanced_accuracy else 0.0
        result[f"{prefix}/horizon_macro_log_loss"] = float(
            np.mean(horizon_log_loss)
        ) if horizon_log_loss else 0.0
        for class_index, class_name in enumerate(OPPORTUNITY_CLASS_NAMES):
            actual_count = int(total.confusion[class_index, :].sum())
            predicted_count = int(total.confusion[:, class_index].sum())
            result[f"{prefix}/{class_name}/support"] = float(actual_count)
            result[f"{prefix}/{class_name}/recall"] = (
                float(total.confusion[class_index, class_index]) / max(actual_count, 1)
            )
            result[f"{prefix}/{class_name}/predicted_share"] = predicted_count / denominator
        return result


@dataclass(slots=True)
class TrainingLossAccumulator:
    loss_sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    correct: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, result: LossResult) -> None:
        for horizon, value in result.horizon_loss_sums.items():
            self.loss_sums[horizon] += float(value)
            self.counts[horizon] += int(result.horizon_counts[horizon])
            self.correct[horizon] += int(result.horizon_correct[horizon])

    def compute(self, prefix: str = "train") -> dict[str, float]:
        horizons = [horizon for horizon in self.counts if self.counts[horizon] > 0]
        if not horizons:
            return {
                f"{prefix}/loss": 0.0,
                f"{prefix}/macro_horizon_log_loss": 0.0,
                f"{prefix}/micro_log_loss": 0.0,
                f"{prefix}/accuracy": 0.0,
                f"{prefix}/valid_labels": 0.0,
            }
        horizon_losses = {
            horizon: self.loss_sums[horizon] / self.counts[horizon]
            for horizon in horizons
        }
        total_count = sum(self.counts[horizon] for horizon in horizons)
        total_loss = sum(self.loss_sums[horizon] for horizon in horizons)
        total_correct = sum(self.correct[horizon] for horizon in horizons)
        metrics = {
            f"{prefix}/loss": float(np.mean(list(horizon_losses.values()))),
            f"{prefix}/macro_horizon_log_loss": float(
                np.mean(list(horizon_losses.values()))
            ),
            f"{prefix}/micro_log_loss": total_loss / total_count,
            f"{prefix}/accuracy": total_correct / total_count,
            f"{prefix}/horizon_macro_accuracy": float(
                np.mean([
                    self.correct[horizon] / self.counts[horizon]
                    for horizon in horizons
                ])
            ),
            f"{prefix}/valid_labels": float(total_count),
        }
        for horizon in sorted(horizons):
            metrics[f"{prefix}/{horizon}/log_loss"] = horizon_losses[horizon]
            metrics[f"{prefix}/{horizon}/accuracy"] = (
                self.correct[horizon] / self.counts[horizon]
            )
            metrics[f"{prefix}/{horizon}/samples"] = float(self.counts[horizon])
        return metrics

    def state_dict(self) -> dict[str, dict[str, float | int]]:
        return {
            "loss_sums": dict(self.loss_sums),
            "counts": dict(self.counts),
            "correct": dict(self.correct),
        }

    def load_state_dict(
        self,
        state: dict[str, dict[str, float | int]] | None,
    ) -> None:
        if not state:
            return
        self.loss_sums = defaultdict(
            float,
            {key: float(value) for key, value in state.get("loss_sums", {}).items()},
        )
        self.counts = defaultdict(
            int,
            {key: int(value) for key, value in state.get("counts", {}).items()},
        )
        self.correct = defaultdict(
            int,
            {key: int(value) for key, value in state.get("correct", {}).items()},
        )
