from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn.functional as F

from research.news_reaction_model.v10.model import NewsReactionOpportunityOutput
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
        for horizon, stat in sorted(self.stats.items()):
            count = max(stat.count, 1)
            result[f"{prefix}/{horizon}/samples"] = float(stat.count)
            result[f"{prefix}/{horizon}/accuracy"] = stat.correct / count
            result[f"{prefix}/{horizon}/macro_f1"] = macro_f1(stat.confusion)
            result[f"{prefix}/{horizon}/balanced_accuracy"] = balanced_accuracy(stat.confusion)
            result[f"{prefix}/{horizon}/log_loss"] = stat.log_loss / count
            result[f"{prefix}/{horizon}/mean_confidence"] = stat.confidence_sum / count
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
        for class_index, class_name in enumerate(OPPORTUNITY_CLASS_NAMES):
            actual_count = int(total.confusion[class_index, :].sum())
            predicted_count = int(total.confusion[:, class_index].sum())
            result[f"{prefix}/{class_name}/support"] = float(actual_count)
            result[f"{prefix}/{class_name}/recall"] = (
                float(total.confusion[class_index, class_index]) / max(actual_count, 1)
            )
            result[f"{prefix}/{class_name}/predicted_share"] = predicted_count / denominator
        return result
