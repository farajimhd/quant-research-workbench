from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class MetricAccumulator:
    horizon: int
    target_columns: tuple[str, ...]
    direction_threshold_bps: float = 0.0

    def __post_init__(self) -> None:
        target_count = len(self.target_columns)
        self.count = 0
        self.abs_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.sq_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.pred_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.actual_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.pred_sq_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.actual_sq_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.cross_sum = np.zeros((self.horizon, target_count), dtype=np.float64)
        self.dir_correct = np.zeros(self.horizon, dtype=np.float64)
        self.dir_total = np.zeros(self.horizon, dtype=np.float64)
        self.close_abs_naive_sum = np.zeros(self.horizon, dtype=np.float64)
        self.close_abs_last_move_sum = np.zeros(self.horizon, dtype=np.float64)
        self.close_abs_mean_reversion_sum = np.zeros(self.horizon, dtype=np.float64)
        self.last_move_dir_correct = np.zeros(self.horizon, dtype=np.float64)
        self.mean_reversion_dir_correct = np.zeros(self.horizon, dtype=np.float64)
        self.baseline_count = 0

    def update(
        self,
        prediction: np.ndarray,
        target: np.ndarray,
        last_close_return_bps: np.ndarray | None = None,
    ) -> None:
        if prediction.size == 0:
            return
        prediction = np.asarray(prediction, dtype=np.float64)
        target = np.asarray(target, dtype=np.float64)
        error = prediction - target
        self.count += int(prediction.shape[0])
        self.abs_sum += np.abs(error).sum(axis=0)
        self.sq_sum += np.square(error).sum(axis=0)
        self.pred_sum += prediction.sum(axis=0)
        self.actual_sum += target.sum(axis=0)
        self.pred_sq_sum += np.square(prediction).sum(axis=0)
        self.actual_sq_sum += np.square(target).sum(axis=0)
        self.cross_sum += (prediction * target).sum(axis=0)

        close_index = self.target_columns.index("close")
        pred_dir = prediction[:, :, close_index] > self.direction_threshold_bps
        actual_dir = target[:, :, close_index] > self.direction_threshold_bps
        self.dir_correct += (pred_dir == actual_dir).sum(axis=0)
        self.dir_total += prediction.shape[0]
        self.close_abs_naive_sum += np.abs(target[:, :, close_index]).sum(axis=0)
        if last_close_return_bps is not None:
            self.baseline_count += int(prediction.shape[0])
            last_move = np.asarray(last_close_return_bps, dtype=np.float64).reshape(-1, 1)
            horizon_scale = np.arange(1, self.horizon + 1, dtype=np.float64).reshape(1, -1)
            continuation = last_move * horizon_scale
            mean_reversion = -last_move * horizon_scale
            close_target = target[:, :, close_index]
            self.close_abs_last_move_sum += np.abs(continuation - close_target).sum(axis=0)
            self.close_abs_mean_reversion_sum += np.abs(mean_reversion - close_target).sum(axis=0)
            self.last_move_dir_correct += ((continuation > self.direction_threshold_bps) == actual_dir).sum(axis=0)
            self.mean_reversion_dir_correct += ((mean_reversion > self.direction_threshold_bps) == actual_dir).sum(axis=0)

    def compute(self, prefix: str = "") -> dict[str, Any]:
        if self.count <= 0:
            return {f"{prefix}windows": 0}
        denominator = float(self.count)
        result: dict[str, Any] = {f"{prefix}windows": self.count}
        close_index = self.target_columns.index("close")
        for horizon_idx in range(self.horizon):
            horizon = horizon_idx + 1
            for target_idx, name in enumerate(self.target_columns):
                mae = self.abs_sum[horizon_idx, target_idx] / denominator
                rmse = math.sqrt(self.sq_sum[horizon_idx, target_idx] / denominator)
                result[f"{prefix}h{horizon}_{name}_mae_bps"] = mae
                result[f"{prefix}h{horizon}_{name}_rmse_bps"] = rmse
                result[f"{prefix}h{horizon}_{name}_corr"] = _corr(
                    n=denominator,
                    x_sum=self.pred_sum[horizon_idx, target_idx],
                    y_sum=self.actual_sum[horizon_idx, target_idx],
                    x_sq_sum=self.pred_sq_sum[horizon_idx, target_idx],
                    y_sq_sum=self.actual_sq_sum[horizon_idx, target_idx],
                    cross_sum=self.cross_sum[horizon_idx, target_idx],
                )
            dir_total = max(1.0, self.dir_total[horizon_idx])
            naive_mae = self.close_abs_naive_sum[horizon_idx] / denominator
            baseline_denominator = float(self.baseline_count)
            has_baselines = baseline_denominator > 0.0
            last_move_mae = (
                self.close_abs_last_move_sum[horizon_idx] / baseline_denominator if has_baselines else math.nan
            )
            mean_reversion_mae = (
                self.close_abs_mean_reversion_sum[horizon_idx] / baseline_denominator if has_baselines else math.nan
            )
            model_mae = self.abs_sum[horizon_idx, close_index] / denominator
            result[f"{prefix}h{horizon}_close_dir_acc_pct"] = 100.0 * self.dir_correct[horizon_idx] / dir_total
            result[f"{prefix}h{horizon}_close_naive_mae_bps"] = naive_mae
            result[f"{prefix}h{horizon}_close_edge_vs_naive_bps"] = naive_mae - model_mae
            result[f"{prefix}h{horizon}_close_last_move_naive_mae_bps"] = last_move_mae
            result[f"{prefix}h{horizon}_close_edge_vs_last_move_naive_bps"] = last_move_mae - model_mae
            result[f"{prefix}h{horizon}_close_last_move_dir_acc_pct"] = (
                100.0 * self.last_move_dir_correct[horizon_idx] / baseline_denominator if has_baselines else math.nan
            )
            result[f"{prefix}h{horizon}_close_mean_reversion_naive_mae_bps"] = mean_reversion_mae
            result[f"{prefix}h{horizon}_close_edge_vs_mean_reversion_naive_bps"] = mean_reversion_mae - model_mae
            result[f"{prefix}h{horizon}_close_mean_reversion_dir_acc_pct"] = (
                100.0 * self.mean_reversion_dir_correct[horizon_idx] / baseline_denominator
                if has_baselines
                else math.nan
            )
        return result


def _corr(
    *,
    n: float,
    x_sum: float,
    y_sum: float,
    x_sq_sum: float,
    y_sq_sum: float,
    cross_sum: float,
) -> float:
    x_mean = x_sum / n
    y_mean = y_sum / n
    cov = cross_sum / n - x_mean * y_mean
    x_var = max(0.0, x_sq_sum / n - x_mean * x_mean)
    y_var = max(0.0, y_sq_sum / n - y_mean * y_mean)
    denom = math.sqrt(x_var * y_var)
    return float(cov / denom) if denom > 0 else 0.0


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
