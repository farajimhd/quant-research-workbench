from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v20.data import EpisodeBatch
from research.news_reaction_model.v20.losses import (
    bucketize_torch,
    signed_opportunity_torch,
)
from research.news_reaction_model.v20.model import ReturnDistributionOutput
from research.news_reaction_model.v20.targets import (
    DIRECTION_NAMES,
    RETURN_BUCKET_COUNT,
    RETURN_BUCKET_NAMES,
    TrainingStatistics,
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
class DistributionAccumulator:
    statistics: TrainingStatistics
    direction_confusion: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 3), dtype=np.int64)
    )
    bucket_confusion: np.ndarray = field(
        default_factory=lambda: np.zeros(
            (RETURN_BUCKET_COUNT, RETURN_BUCKET_COUNT), dtype=np.int64
        )
    )
    signed_abs_error: float = 0.0
    signed_sq_error: float = 0.0
    baseline_abs_error: float = 0.0
    negative_log_likelihood: float = 0.0
    brier: float = 0.0
    count: int = 0
    confidences: list[np.ndarray] = field(default_factory=list)
    correctness: list[np.ndarray] = field(default_factory=list)
    router_sum: np.ndarray | None = None

    @torch.no_grad()
    def add(self, output: ReturnDistributionOutput, batch: EpisodeBatch) -> None:
        mask = batch.target_mask.bool()
        if not bool(mask.any()):
            return
        direction_true = batch.direction[mask].detach().cpu().numpy()
        direction_pred = (
            output.direction_probabilities[mask].argmax(-1).detach().cpu().numpy()
        )
        np.add.at(self.direction_confusion, (direction_true, direction_pred), 1)

        signed_tensor = signed_opportunity_torch(batch)[mask].float()
        buckets_tensor = bucketize_torch(signed_tensor)
        bucket_true = buckets_tensor.detach().cpu().numpy()
        bucket_pred = (
            output.return_probabilities[mask].argmax(-1).detach().cpu().numpy()
        )
        np.add.at(self.bucket_confusion, (bucket_true, bucket_pred), 1)

        signed = signed_tensor.detach().cpu().numpy()
        expected = output.expected_return[mask].float().detach().cpu().numpy()
        error = expected - signed
        self.signed_abs_error += float(np.abs(error).sum())
        self.signed_sq_error += float(np.square(error).sum())
        self.baseline_abs_error += float(
            np.abs(signed - self.statistics.signed_return_median).sum()
        )
        probability = output.return_probabilities[mask].float()
        self.negative_log_likelihood += float(
            -torch.log(
                probability.gather(1, buckets_tensor.unsqueeze(1)).clamp_min(1e-12)
            ).sum()
            .detach()
            .cpu()
        )
        direction_probability = output.direction_probabilities[mask].float()
        one_hot = torch.nn.functional.one_hot(
            batch.direction[mask], num_classes=3
        ).float()
        self.brier += float(
            torch.square(direction_probability - one_hot).sum(dim=1).sum().cpu()
        )
        confidence, prediction = direction_probability.max(dim=-1)
        self.confidences.append(confidence.detach().cpu().numpy())
        self.correctness.append(
            (prediction == batch.direction[mask]).float().detach().cpu().numpy()
        )
        router = output.router_probabilities[mask].float().sum(dim=0).cpu().numpy()
        self.router_sum = router if self.router_sum is None else self.router_sum + router
        self.count += int(mask.sum().detach().cpu())

    def compute(self, prefix: str) -> dict[str, float]:
        total = max(self.count, 1)
        result: dict[str, float] = {
            f"{prefix}/samples": float(self.count),
            f"{prefix}/direction/accuracy": float(
                np.trace(self.direction_confusion) / total
            ),
            f"{prefix}/direction/macro_f1": macro_f1(self.direction_confusion),
            f"{prefix}/direction/balanced_accuracy": balanced_accuracy(
                self.direction_confusion
            ),
        }
        for index, name in enumerate(DIRECTION_NAMES):
            support = int(self.direction_confusion[index].sum())
            predicted = int(self.direction_confusion[:, index].sum())
            true_positive = int(self.direction_confusion[index, index])
            precision = true_positive / max(predicted, 1)
            recall = true_positive / max(support, 1)
            f1 = 2 * precision * recall / max(precision + recall, 1e-12)
            base = f"{prefix}/direction/class/{name}"
            result[f"{base}/support"] = float(support)
            result[f"{base}/predicted"] = float(predicted)
            result[f"{base}/precision"] = float(precision)
            result[f"{base}/recall"] = float(recall)
            result[f"{base}/f1"] = float(f1)

        bucket_total = max(int(self.bucket_confusion.sum()), 1)
        row, column = np.indices(self.bucket_confusion.shape)
        result[f"{prefix}/return_bucket/accuracy"] = float(
            np.trace(self.bucket_confusion) / bucket_total
        )
        result[f"{prefix}/return_bucket/within_one_accuracy"] = float(
            self.bucket_confusion[np.abs(row - column) <= 1].sum() / bucket_total
        )
        for index, name in enumerate(RETURN_BUCKET_NAMES):
            result[f"{prefix}/return_bucket/{name}/support"] = float(
                self.bucket_confusion[index].sum()
            )

        mae = self.signed_abs_error / total
        baseline_mae = self.baseline_abs_error / total
        mae_skill = 1.0 - mae / max(baseline_mae, 1e-12)
        log_loss = self.negative_log_likelihood / total
        log_loss_skill = 1.0 - log_loss / max(
            self.statistics.bucket_prior_log_loss, 1e-12
        )
        result.update(
            {
                f"{prefix}/signed_return/mae_pct": float(mae),
                f"{prefix}/signed_return/rmse_pct": float(
                    np.sqrt(self.signed_sq_error / total)
                ),
                f"{prefix}/signed_return/train_median_baseline_mae_pct": float(
                    baseline_mae
                ),
                f"{prefix}/signed_return/mae_skill": float(mae_skill),
                f"{prefix}/return_distribution/log_loss": float(log_loss),
                f"{prefix}/return_distribution/log_loss_skill": float(log_loss_skill),
                f"{prefix}/direction/brier": float(self.brier / total),
            }
        )
        if self.confidences:
            confidence = np.concatenate(self.confidences)
            correct = np.concatenate(self.correctness)
            ece = 0.0
            for lower in np.linspace(0.0, 0.9, 10):
                selected = (confidence >= lower) & (confidence < lower + 0.1)
                if selected.any():
                    ece += float(selected.mean()) * abs(
                        float(correct[selected].mean())
                        - float(confidence[selected].mean())
                    )
            result[f"{prefix}/direction/ece"] = ece
        if self.router_sum is not None:
            utilization = self.router_sum / max(float(self.router_sum.sum()), 1e-12)
            for index, value in enumerate(utilization):
                result[f"{prefix}/router/expert_{index}_probability"] = float(value)
        clipped_skill = float(np.clip(mae_skill, -1.0, 1.0))
        clipped_log_skill = float(np.clip(log_loss_skill, -1.0, 1.0))
        result[f"{prefix}/joint_score"] = float(
            np.mean(
                (
                    result[f"{prefix}/direction/macro_f1"],
                    clipped_skill,
                    clipped_log_skill,
                )
            )
        )
        return result
