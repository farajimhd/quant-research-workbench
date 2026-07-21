from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import torch

from research.news_reaction_model.v2 import RETURN_TARGETS


@dataclass(slots=True)
class RegressionAccumulator:
    squared_error: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    absolute_error: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    zero_squared_error: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    zero_absolute_error: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    prediction_sum: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    target_sum: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    prediction_square_sum: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    target_square_sum: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    cross_sum: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    count: int = 0

    @torch.no_grad()
    def add(self, forecasts: torch.Tensor, returns: torch.Tensor, mask: torch.Tensor) -> None:
        valid = mask.bool()
        if not bool(valid.any()):
            return
        predicted = forecasts[valid].float().cpu().numpy().astype(np.float64, copy=False)
        actual = returns[valid].float().cpu().numpy().astype(np.float64, copy=False)
        error = predicted - actual
        self.squared_error += np.square(error).sum(axis=0)
        self.absolute_error += np.abs(error).sum(axis=0)
        self.zero_squared_error += np.square(actual).sum(axis=0)
        self.zero_absolute_error += np.abs(actual).sum(axis=0)
        self.prediction_sum += predicted.sum(axis=0)
        self.target_sum += actual.sum(axis=0)
        self.prediction_square_sum += np.square(predicted).sum(axis=0)
        self.target_square_sum += np.square(actual).sum(axis=0)
        self.cross_sum += (predicted * actual).sum(axis=0)
        self.count += int(actual.shape[0])

    def compute(self, prefix: str = "val") -> dict[str, float]:
        count = max(self.count, 1)
        mse_by_target = self.squared_error / count
        mae_by_target = self.absolute_error / count
        zero_mse_by_target = self.zero_squared_error / count
        zero_mae_by_target = self.zero_absolute_error / count
        result = {
            f"{prefix}/samples": float(self.count),
            f"{prefix}/mse": float(mse_by_target.mean()),
            f"{prefix}/rmse": float(np.sqrt(mse_by_target.mean())),
            f"{prefix}/mae": float(mae_by_target.mean()),
            f"{prefix}/zero_mse": float(zero_mse_by_target.mean()),
            f"{prefix}/zero_mae": float(zero_mae_by_target.mean()),
            f"{prefix}/mse_improvement_vs_zero": float(zero_mse_by_target.mean() - mse_by_target.mean()),
            f"{prefix}/mae_improvement_vs_zero": float(zero_mae_by_target.mean() - mae_by_target.mean()),
        }
        for index, target_name in enumerate(RETURN_TARGETS):
            prediction_mean = self.prediction_sum[index] / count
            target_mean = self.target_sum[index] / count
            covariance = self.cross_sum[index] / count - prediction_mean * target_mean
            prediction_variance = self.prediction_square_sum[index] / count - prediction_mean**2
            target_variance = self.target_square_sum[index] / count - target_mean**2
            denominator = np.sqrt(max(prediction_variance, 0.0) * max(target_variance, 0.0))
            correlation = covariance / denominator if denominator > 0 else 0.0
            result.update({
                f"{prefix}/{target_name}_mse": float(mse_by_target[index]),
                f"{prefix}/{target_name}_rmse": float(np.sqrt(mse_by_target[index])),
                f"{prefix}/{target_name}_mae": float(mae_by_target[index]),
                f"{prefix}/{target_name}_zero_mse": float(zero_mse_by_target[index]),
                f"{prefix}/{target_name}_zero_mae": float(zero_mae_by_target[index]),
                f"{prefix}/{target_name}_pearson": float(correlation),
            })
        return result


@dataclass(slots=True)
class PositionPnlAccumulator:
    """Retain bounded numeric evaluation rows for direction and event-level P&L."""

    predicted: list[np.ndarray] = field(default_factory=list)
    actual_abnormal: list[np.ndarray] = field(default_factory=list)
    actual_raw: list[np.ndarray] = field(default_factory=list)
    raw_high: list[np.ndarray] = field(default_factory=list)
    raw_low: list[np.ndarray] = field(default_factory=list)
    robust_scale: list[np.ndarray] = field(default_factory=list)

    def add(
        self,
        predicted: np.ndarray,
        actual_abnormal: np.ndarray,
        actual_raw: np.ndarray,
        raw_high: np.ndarray,
        raw_low: np.ndarray,
        robust_scale: np.ndarray,
    ) -> None:
        values = [np.asarray(value, dtype=np.float64).reshape(-1) for value in (
            predicted, actual_abnormal, actual_raw, raw_high, raw_low, robust_scale,
        )]
        lengths = {len(value) for value in values}
        if len(lengths) != 1:
            raise ValueError(f"Position/P&L arrays must have equal lengths, received {sorted(lengths)}")
        valid = (
            np.isfinite(values[0]) & np.isfinite(values[1]) & np.isfinite(values[2])
            & np.isfinite(values[5]) & (values[5] > 0)
        )
        if not valid.any():
            return
        targets = (
            self.predicted, self.actual_abnormal, self.actual_raw,
            self.raw_high, self.raw_low, self.robust_scale,
        )
        for target, value in zip(targets, values):
            target.append(value[valid])

    @property
    def count(self) -> int:
        return sum(len(value) for value in self.predicted)

    def compute(
        self,
        *,
        flat_z: float = 0.5,
        cost_bps: Iterable[float] = (0.0, 2.0, 5.0, 10.0),
        notional: float = 10_000.0,
    ) -> dict[str, object]:
        if flat_z < 0:
            raise ValueError("flat_z must be non-negative")
        predicted = _concat(self.predicted)
        actual_abnormal = _concat(self.actual_abnormal)
        actual_raw = _concat(self.actual_raw)
        raw_high = _concat(self.raw_high)
        raw_low = _concat(self.raw_low)
        scale = _concat(self.robust_scale)
        if not len(predicted):
            return {"samples": 0}

        threshold = flat_z * scale
        predicted_side = _side(predicted, threshold)
        actual_side = _side(actual_abnormal, threshold)
        active = predicted_side != 0
        nonflat_actual = actual_side != 0
        comparable = active & nonflat_actual
        side_indices = {-1: 0, 0: 1, 1: 2}
        confusion = np.zeros((3, 3), dtype=np.int64)
        for actual_value, predicted_value in zip(actual_side, predicted_side):
            confusion[side_indices[int(actual_value)], side_indices[int(predicted_value)]] += 1

        recalls, f1s, class_metrics = [], [], {}
        class_names = ("short", "flat", "long")
        for index in range(3):
            tp = float(confusion[index, index])
            actual_count = float(confusion[index].sum())
            predicted_count = float(confusion[:, index].sum())
            recall = tp / actual_count if actual_count else 0.0
            precision = tp / predicted_count if predicted_count else 0.0
            recalls.append(recall)
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            f1s.append(f1)
            class_metrics[class_names[index]] = {
                "support": int(actual_count),
                "predicted": int(predicted_count),
                "precision": float(precision),
                "recall": float(recall),
                "f1": float(f1),
            }

        gross_raw = predicted_side * actual_raw
        gross_abnormal = predicted_side * actual_abnormal
        active_count = int(active.sum())
        favorable = np.maximum(predicted_side * raw_high, predicted_side * raw_low)
        adverse = np.minimum(predicted_side * raw_high, predicted_side * raw_low)
        excursion_valid = active & np.isfinite(favorable) & np.isfinite(adverse)
        output: dict[str, object] = {
            "samples": int(len(predicted)),
            "flat_z": float(flat_z),
            "flat_band_mean_bps": float(threshold.mean() * 10_000.0),
            "positions": {
                "long": int((predicted_side == 1).sum()),
                "flat": int((predicted_side == 0).sum()),
                "short": int((predicted_side == -1).sum()),
                "coverage": float(active.mean()),
            },
            "classification": {
                "accuracy": float((predicted_side == actual_side).mean()),
                "balanced_accuracy": float(np.mean(recalls)),
                "macro_f1": float(np.mean(f1s)),
                "active_directional_accuracy": float((predicted_side[comparable] == actual_side[comparable]).mean()) if comparable.any() else 0.0,
                "active_directional_samples": int(comparable.sum()),
                "confusion_actual_rows_predicted_columns": confusion.tolist(),
                "class_order": list(class_names),
                "per_class": class_metrics,
            },
            "gross": {
                "raw_total_return": float(gross_raw.sum()),
                "raw_mean_per_event": float(gross_raw.mean()),
                "raw_mean_per_active": float(gross_raw[active].mean()) if active_count else 0.0,
                "raw_median_per_active": float(np.median(gross_raw[active])) if active_count else 0.0,
                "raw_win_rate": float((gross_raw[active] > 0).mean()) if active_count else 0.0,
                "raw_profit_factor": _profit_factor(gross_raw[active]),
                "abnormal_total_return": float(gross_abnormal.sum()),
                "abnormal_mean_per_active": float(gross_abnormal[active].mean()) if active_count else 0.0,
                "fixed_notional_raw_pnl": float(gross_raw.sum() * notional),
                "notional_per_event": float(notional),
                "break_even_round_trip_cost_bps": float(gross_raw[active].mean() * 10_000.0) if active_count else 0.0,
            },
            "excursion": {
                "samples": int(excursion_valid.sum()),
                "mean_favorable_return": float(favorable[excursion_valid].mean()) if excursion_valid.any() else 0.0,
                "mean_adverse_return": float(adverse[excursion_valid].mean()) if excursion_valid.any() else 0.0,
            },
            "cost_scenarios": {},
        }
        scenarios = output["cost_scenarios"]
        assert isinstance(scenarios, dict)
        for raw_cost in cost_bps:
            cost = float(raw_cost)
            net = gross_raw - active.astype(np.float64) * cost / 10_000.0
            scenarios[f"{cost:g}_bps"] = {
                "total_return": float(net.sum()),
                "mean_per_event": float(net.mean()),
                "mean_per_active": float(net[active].mean()) if active_count else 0.0,
                "median_per_active": float(np.median(net[active])) if active_count else 0.0,
                "win_rate": float((net[active] > 0).mean()) if active_count else 0.0,
                "profit_factor": _profit_factor(net[active]),
                "fixed_notional_pnl": float(net.sum() * notional),
            }
        return output


def _concat(values: list[np.ndarray]) -> np.ndarray:
    return np.concatenate(values) if values else np.empty(0, dtype=np.float64)


def _side(value: np.ndarray, threshold: np.ndarray) -> np.ndarray:
    return np.where(value > threshold, 1, np.where(value < -threshold, -1, 0)).astype(np.int8)


def _profit_factor(value: np.ndarray) -> float | None:
    positive = float(value[value > 0].sum())
    negative = float(-value[value < 0].sum())
    if negative == 0:
        return None
    return positive / negative
