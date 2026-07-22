from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v3.model import NewsReactionOutput


@dataclass(slots=True)
class HierarchicalAccumulator:
    confusion: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=np.int64))
    actionable_confusion: np.ndarray = field(default_factory=lambda: np.zeros((2, 2), dtype=np.int64))
    direction_correct: int = 0
    direction_count: int = 0
    log_loss_sum: float = 0.0
    brier_sum: float = 0.0
    count: int = 0
    magnitude_absolute_error: float = 0.0
    magnitude_count: int = 0
    confidence_count: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.int64))
    confidence_sum: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.float64))
    confidence_correct: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.int64))

    @torch.no_grad()
    def add(
        self,
        output: NewsReactionOutput,
        targets: torch.Tensor,
        returns: torch.Tensor,
        mask: torch.Tensor,
    ) -> None:
        valid = mask.bool() & (targets >= 0)
        if not bool(valid.any()):
            return
        probs = output.class_probabilities()[valid].clamp_min(1e-9)
        actual = targets[valid].long()
        predicted = (output.positions()[valid] + 1).long()
        confidence = probs.gather(1, predicted.unsqueeze(1)).squeeze(1)
        for actual_class, predicted_class in zip(actual.cpu().tolist(), predicted.cpu().tolist()):
            self.confusion[int(actual_class), int(predicted_class)] += 1

        actionable_actual = (targets[valid] != 1).long()
        actionable_predicted = output.actionable_logits[valid].argmax(dim=-1)
        for actual_class, predicted_class in zip(
            actionable_actual.cpu().tolist(), actionable_predicted.cpu().tolist(),
        ):
            self.actionable_confusion[int(actual_class), int(predicted_class)] += 1

        active = valid & (targets != 1)
        if bool(active.any()):
            direction_actual = (targets[active] == 2).long()
            direction_predicted = output.direction_logits[active].argmax(dim=-1)
            self.direction_correct += int((direction_actual == direction_predicted).sum().cpu())
            self.direction_count += int(direction_actual.numel())
            magnitude_error = (output.magnitude_forecasts[active] - returns[active].abs()).abs()
            self.magnitude_absolute_error += float(magnitude_error.sum().cpu())
            self.magnitude_count += int(magnitude_error.numel())

        bins = torch.clamp((confidence * 10).long(), max=9).cpu().numpy()
        confidence_np = confidence.cpu().numpy()
        correct_np = (predicted == actual).cpu().numpy()
        for bin_index in range(10):
            selected = bins == bin_index
            self.confidence_count[bin_index] += int(selected.sum())
            self.confidence_sum[bin_index] += float(confidence_np[selected].sum())
            self.confidence_correct[bin_index] += int(correct_np[selected].sum())
        self.log_loss_sum += float(-torch.log(probs.gather(1, actual.unsqueeze(1)).squeeze(1)).sum().cpu())
        one_hot = torch.nn.functional.one_hot(actual, num_classes=3).float()
        self.brier_sum += float(((probs - one_hot) ** 2).mean(dim=1).sum().cpu())
        self.count += int(actual.numel())

    def compute(self, prefix: str = "val") -> dict[str, float]:
        total = max(self.count, 1)
        recalls, f1s = [], []
        for label in range(3):
            true_positive = float(self.confusion[label, label])
            false_positive = float(self.confusion[:, label].sum() - true_positive)
            false_negative = float(self.confusion[label, :].sum() - true_positive)
            precision = true_positive / max(true_positive + false_positive, 1.0)
            recall = true_positive / max(true_positive + false_negative, 1.0)
            recalls.append(recall)
            f1s.append(2 * precision * recall / max(precision + recall, 1e-12))
        actionable_total = max(int(self.actionable_confusion.sum()), 1)
        ece = 0.0
        for bin_index, count in enumerate(self.confidence_count):
            if count:
                mean_confidence = self.confidence_sum[bin_index] / count
                mean_accuracy = self.confidence_correct[bin_index] / count
                ece += (count / total) * abs(mean_confidence - mean_accuracy)
        return {
            f"{prefix}/samples": float(self.count),
            f"{prefix}/accuracy": float(np.trace(self.confusion) / total),
            f"{prefix}/balanced_accuracy": float(np.mean(recalls)),
            f"{prefix}/macro_f1": float(np.mean(f1s)),
            f"{prefix}/log_loss": self.log_loss_sum / total,
            f"{prefix}/brier": self.brier_sum / total,
            f"{prefix}/ece_10_bin": ece,
            f"{prefix}/uniform_log_loss": float(np.log(3.0)),
            f"{prefix}/uniform_brier": 2.0 / 9.0,
            f"{prefix}/log_loss_improvement_vs_uniform": float(np.log(3.0)) - self.log_loss_sum / total,
            f"{prefix}/negative_recall": recalls[0],
            f"{prefix}/neutral_recall": recalls[1],
            f"{prefix}/positive_recall": recalls[2],
            f"{prefix}/actionable_accuracy": float(np.trace(self.actionable_confusion) / actionable_total),
            f"{prefix}/direction_accuracy": self.direction_correct / max(self.direction_count, 1),
            f"{prefix}/magnitude_mae": self.magnitude_absolute_error / max(self.magnitude_count, 1),
        }
