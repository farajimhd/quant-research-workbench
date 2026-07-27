from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from research.news_reaction_model.v21.data import EpisodeBatch
from research.news_reaction_model.v21.losses import (
    magnitude_bucketize_torch,
    signed_bucketize_torch,
    signed_opportunity_torch,
)
from research.news_reaction_model.v21.model import HierarchicalReturnOutput
from research.news_reaction_model.v21.targets import (
    DIRECTION_NAMES,
    MAGNITUDE_BUCKET_COUNT,
    MAGNITUDE_BUCKET_NAMES,
    RETURN_BUCKET_COUNT,
    RETURN_BUCKET_NAMES,
    SIDE_COUNT,
    SIDE_NAMES,
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
class HierarchicalAccumulator:
    statistics: TrainingStatistics
    direction_confusion: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 3), dtype=np.int64)
    )
    magnitude_confusion: np.ndarray = field(
        default_factory=lambda: np.zeros(
            (SIDE_COUNT, MAGNITUDE_BUCKET_COUNT, MAGNITUDE_BUCKET_COUNT),
            dtype=np.int64,
        )
    )
    return_confusion: np.ndarray = field(
        default_factory=lambda: np.zeros(
            (RETURN_BUCKET_COUNT, RETURN_BUCKET_COUNT), dtype=np.int64
        )
    )
    direction_nll: float = 0.0
    joint_nll: float = 0.0
    brier: float = 0.0
    signed_abs_error: float = 0.0
    signed_sq_error: float = 0.0
    signed_baseline_abs_error: float = 0.0
    magnitude_nll: np.ndarray = field(
        default_factory=lambda: np.zeros(SIDE_COUNT, dtype=np.float64)
    )
    magnitude_abs_error: np.ndarray = field(
        default_factory=lambda: np.zeros(SIDE_COUNT, dtype=np.float64)
    )
    magnitude_sq_error: np.ndarray = field(
        default_factory=lambda: np.zeros(SIDE_COUNT, dtype=np.float64)
    )
    magnitude_baseline_abs_error: np.ndarray = field(
        default_factory=lambda: np.zeros(SIDE_COUNT, dtype=np.float64)
    )
    magnitude_count: np.ndarray = field(
        default_factory=lambda: np.zeros(SIDE_COUNT, dtype=np.int64)
    )
    count: int = 0
    confidences: list[np.ndarray] = field(default_factory=list)
    correctness: list[np.ndarray] = field(default_factory=list)
    router_sum: np.ndarray | None = None

    @torch.no_grad()
    def add(self, output: HierarchicalReturnOutput, batch: EpisodeBatch) -> None:
        mask = batch.target_mask.bool()
        if not bool(mask.any()):
            return
        direction_true_t = batch.direction[mask].long()
        direction_prob = output.direction_probabilities[mask].float()
        direction_pred_t = direction_prob.argmax(dim=-1)
        direction_true = direction_true_t.cpu().numpy()
        direction_pred = direction_pred_t.cpu().numpy()
        np.add.at(self.direction_confusion, (direction_true, direction_pred), 1)
        true_direction_probability = direction_prob.gather(
            1, direction_true_t.unsqueeze(1)
        ).squeeze(1)
        self.direction_nll += float(
            -torch.log(true_direction_probability.clamp_min(1e-12)).sum().cpu()
        )
        one_hot = torch.nn.functional.one_hot(
            direction_true_t, num_classes=3
        ).float()
        self.brier += float(
            torch.square(direction_prob - one_hot).sum(dim=1).sum().cpu()
        )

        signed_t = signed_opportunity_torch(batch)[mask].float()
        signed = signed_t.cpu().numpy()
        expected = output.expected_return[mask].float().cpu().numpy()
        signed_error = expected - signed
        self.signed_abs_error += float(np.abs(signed_error).sum())
        self.signed_sq_error += float(np.square(signed_error).sum())
        self.signed_baseline_abs_error += float(
            np.abs(signed - self.statistics.signed_return_median).sum()
        )
        signed_bucket_true_t = signed_bucketize_torch(signed_t)
        signed_bucket_pred_t = output.joint_return_probabilities[mask].argmax(-1)
        np.add.at(
            self.return_confusion,
            (
                signed_bucket_true_t.cpu().numpy(),
                signed_bucket_pred_t.cpu().numpy(),
            ),
            1,
        )

        joint_probability = true_direction_probability.clone()
        for direction_index, side_index in ((1, 0), (2, 1)):
            selected = direction_true_t == direction_index
            if not bool(selected.any()):
                continue
            true_magnitude = signed_t[selected].abs()
            true_bucket_t = magnitude_bucketize_torch(true_magnitude)
            magnitude_probability = output.magnitude_probabilities[mask][
                selected, side_index
            ].float()
            true_probability = magnitude_probability.gather(
                1, true_bucket_t.unsqueeze(1)
            ).squeeze(1)
            joint_probability[selected] *= true_probability
            self.magnitude_nll[side_index] += float(
                -torch.log(true_probability.clamp_min(1e-12)).sum().cpu()
            )
            predicted_bucket = magnitude_probability.argmax(-1).cpu().numpy()
            np.add.at(
                self.magnitude_confusion[side_index],
                (true_bucket_t.cpu().numpy(), predicted_bucket),
                1,
            )
            predicted_magnitude = (
                output.expected_up_return[mask][selected]
                if side_index == 0
                else -output.expected_down_return[mask][selected]
            ).float()
            error = (
                predicted_magnitude.cpu().numpy()
                - true_magnitude.cpu().numpy()
            )
            self.magnitude_abs_error[side_index] += float(np.abs(error).sum())
            self.magnitude_sq_error[side_index] += float(np.square(error).sum())
            self.magnitude_baseline_abs_error[side_index] += float(
                np.abs(
                    true_magnitude.cpu().numpy()
                    - self.statistics.magnitude_median[side_index]
                ).sum()
            )
            self.magnitude_count[side_index] += int(selected.sum().cpu())
        self.joint_nll += float(
            -torch.log(joint_probability.clamp_min(1e-12)).sum().cpu()
        )
        confidence, prediction = direction_prob.max(dim=-1)
        self.confidences.append(confidence.cpu().numpy())
        self.correctness.append((prediction == direction_true_t).float().cpu().numpy())
        router = output.router_probabilities[mask].float().sum(dim=0).cpu().numpy()
        self.router_sum = router if self.router_sum is None else self.router_sum + router
        self.count += int(mask.sum().cpu())

    def compute(self, prefix: str) -> dict[str, float]:
        total = max(self.count, 1)
        direction_log_loss = self.direction_nll / total
        direction_log_skill = 1.0 - direction_log_loss / max(
            self.statistics.direction_prior_log_loss, 1e-12
        )
        joint_log_loss = self.joint_nll / total
        joint_log_skill = 1.0 - joint_log_loss / max(
            self.statistics.joint_prior_log_loss, 1e-12
        )
        result: dict[str, float] = {
            f"{prefix}/samples": float(self.count),
            f"{prefix}/direction/accuracy": float(
                np.trace(self.direction_confusion) / total
            ),
            f"{prefix}/direction/macro_f1": macro_f1(self.direction_confusion),
            f"{prefix}/direction/balanced_accuracy": balanced_accuracy(
                self.direction_confusion
            ),
            f"{prefix}/direction/log_loss": float(direction_log_loss),
            f"{prefix}/direction/log_loss_skill": float(direction_log_skill),
            f"{prefix}/direction/brier": float(self.brier / total),
            f"{prefix}/joint_distribution/log_loss": float(joint_log_loss),
            f"{prefix}/joint_distribution/log_loss_skill": float(joint_log_skill),
        }
        for index, name in enumerate(DIRECTION_NAMES):
            support = int(self.direction_confusion[index].sum())
            predicted = int(self.direction_confusion[:, index].sum())
            true_positive = int(self.direction_confusion[index, index])
            precision = true_positive / max(predicted, 1)
            recall = true_positive / max(support, 1)
            f1 = 2 * precision * recall / max(precision + recall, 1e-12)
            base = f"{prefix}/direction/class/{name}"
            result.update(
                {
                    f"{base}/support": float(support),
                    f"{base}/predicted": float(predicted),
                    f"{base}/precision": float(precision),
                    f"{base}/recall": float(recall),
                    f"{base}/f1": float(f1),
                }
            )

        magnitude_mae_skills: list[float] = []
        magnitude_log_skills: list[float] = []
        for side_index, side_name in enumerate(SIDE_NAMES):
            count = max(int(self.magnitude_count[side_index]), 1)
            confusion = self.magnitude_confusion[side_index]
            rows, columns = np.indices(confusion.shape)
            accuracy = float(np.trace(confusion) / max(int(confusion.sum()), 1))
            within_one = float(
                confusion[np.abs(rows - columns) <= 1].sum()
                / max(int(confusion.sum()), 1)
            )
            mae = self.magnitude_abs_error[side_index] / count
            baseline_mae = self.magnitude_baseline_abs_error[side_index] / count
            mae_skill = 1.0 - mae / max(baseline_mae, 1e-12)
            log_loss = self.magnitude_nll[side_index] / count
            log_skill = 1.0 - log_loss / max(
                self.statistics.magnitude_prior_log_loss[side_index], 1e-12
            )
            magnitude_mae_skills.append(mae_skill)
            magnitude_log_skills.append(log_skill)
            base = f"{prefix}/magnitude/{side_name}"
            result.update(
                {
                    f"{base}/samples": float(self.magnitude_count[side_index]),
                    f"{base}/bucket_accuracy": accuracy,
                    f"{base}/within_one_accuracy": within_one,
                    f"{base}/mae_pct": float(mae),
                    f"{base}/rmse_pct": float(
                        np.sqrt(self.magnitude_sq_error[side_index] / count)
                    ),
                    f"{base}/median_baseline_mae_pct": float(baseline_mae),
                    f"{base}/mae_skill": float(mae_skill),
                    f"{base}/log_loss": float(log_loss),
                    f"{base}/log_loss_skill": float(log_skill),
                }
            )
            for bucket_index, bucket_name in enumerate(MAGNITUDE_BUCKET_NAMES):
                result[f"{base}/bucket/{bucket_name}/support"] = float(
                    confusion[bucket_index].sum()
                )

        return_total = max(int(self.return_confusion.sum()), 1)
        result[f"{prefix}/signed_bucket/accuracy"] = float(
            np.trace(self.return_confusion) / return_total
        )
        for bucket_index, bucket_name in enumerate(RETURN_BUCKET_NAMES):
            result[f"{prefix}/signed_bucket/{bucket_name}/support"] = float(
                self.return_confusion[bucket_index].sum()
            )
        signed_mae = self.signed_abs_error / total
        signed_baseline = self.signed_baseline_abs_error / total
        result.update(
            {
                f"{prefix}/signed_return/mae_pct": float(signed_mae),
                f"{prefix}/signed_return/rmse_pct": float(
                    np.sqrt(self.signed_sq_error / total)
                ),
                f"{prefix}/signed_return/median_baseline_mae_pct": float(
                    signed_baseline
                ),
                f"{prefix}/signed_return/mae_skill": float(
                    1.0 - signed_mae / max(signed_baseline, 1e-12)
                ),
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
        result[f"{prefix}/joint_score"] = float(
            np.mean(
                (
                    result[f"{prefix}/direction/macro_f1"],
                    float(np.clip(direction_log_skill, -1.0, 1.0)),
                    float(np.clip(joint_log_skill, -1.0, 1.0)),
                    float(np.clip(np.mean(magnitude_mae_skills), -1.0, 1.0)),
                    float(np.clip(np.mean(magnitude_log_skills), -1.0, 1.0)),
                )
            )
        )
        return result


__all__ = ["HierarchicalAccumulator", "balanced_accuracy", "macro_f1"]
