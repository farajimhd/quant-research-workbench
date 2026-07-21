from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch


@dataclass(slots=True)
class ClassificationAccumulator:
    confusion: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=np.int64))
    log_loss_sum: float = 0.0
    brier_sum: float = 0.0
    count: int = 0
    absolute_return_error: float = 0.0
    confidence_count: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.int64))
    confidence_sum: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.float64))
    confidence_correct: np.ndarray = field(default_factory=lambda: np.zeros(10, dtype=np.int64))

    @torch.no_grad()
    def add(self, logits: torch.Tensor, targets: torch.Tensor, forecasts: torch.Tensor, returns: torch.Tensor, mask: torch.Tensor) -> None:
        valid = mask.bool() & (targets >= 0)
        if not bool(valid.any()):
            return
        probs = torch.softmax(logits[valid].float(), dim=-1).clamp_min(1e-9)
        actual = targets[valid].long()
        predicted = probs.argmax(dim=-1)
        confidence = probs.max(dim=-1).values
        for a, p in zip(actual.cpu().tolist(), predicted.cpu().tolist()):
            self.confusion[int(a), int(p)] += 1
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
        self.absolute_return_error += float((forecasts[valid] - returns[valid]).abs().mean(dim=1).sum().cpu())
        self.count += int(actual.numel())

    def compute(self, prefix: str = "val") -> dict[str, float]:
        total = max(self.count, 1)
        recalls, f1s = [], []
        for label in range(3):
            tp = float(self.confusion[label, label])
            fp = float(self.confusion[:, label].sum() - tp)
            fn = float(self.confusion[label, :].sum() - tp)
            precision = tp / max(tp + fp, 1.0)
            recall = tp / max(tp + fn, 1.0)
            recalls.append(recall)
            f1s.append(2 * precision * recall / max(precision + recall, 1e-12))
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
            f"{prefix}/return_mae": self.absolute_return_error / total,
            f"{prefix}/negative_recall": recalls[0],
            f"{prefix}/neutral_recall": recalls[1],
            f"{prefix}/positive_recall": recalls[2],
        }
