from __future__ import annotations

from dataclasses import dataclass, field

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
