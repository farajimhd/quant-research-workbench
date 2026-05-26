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
    confidence_thresholds: tuple[float, ...] = (0.5, 0.7, 0.9)
    confidence_bucket_edges: tuple[float, ...] = tuple(i / 10.0 for i in range(11))

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
        self.expected_abs_sum = np.zeros(self.horizon, dtype=np.float64)
        self.expected_sq_sum = np.zeros(self.horizon, dtype=np.float64)
        self.expected_pred_sum = np.zeros(self.horizon, dtype=np.float64)
        self.expected_pred_sq_sum = np.zeros(self.horizon, dtype=np.float64)
        self.expected_cross_sum = np.zeros(self.horizon, dtype=np.float64)
        self.expected_dir_correct = np.zeros(self.horizon, dtype=np.float64)
        self.confidence_sum = np.zeros(self.horizon, dtype=np.float64)
        self.magnitude_std_sum = np.zeros(self.horizon, dtype=np.float64)
        self.p_up_sum = np.zeros(self.horizon, dtype=np.float64)
        self.sign_confidence_sum = np.zeros(self.horizon, dtype=np.float64)
        threshold_count = len(self.confidence_thresholds)
        self.threshold_count = np.zeros((threshold_count, self.horizon), dtype=np.float64)
        self.threshold_abs_sum = np.zeros((threshold_count, self.horizon), dtype=np.float64)
        self.threshold_dir_correct = np.zeros((threshold_count, self.horizon), dtype=np.float64)
        bucket_count = max(0, len(self.confidence_bucket_edges) - 1)
        self.bucket_count = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_abs_sum = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_dir_correct = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_confidence_sum = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_abs_expected_sum = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_abs_actual_sum = np.zeros((bucket_count, self.horizon), dtype=np.float64)
        self.bucket_magnitude_std_sum = np.zeros((bucket_count, self.horizon), dtype=np.float64)

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

    def update_confidence(
        self,
        *,
        expected_signed_bps: np.ndarray,
        target: np.ndarray,
        confidence: np.ndarray,
        magnitude_std_bps: np.ndarray,
        p_up: np.ndarray,
        sign_confidence: np.ndarray,
    ) -> None:
        if expected_signed_bps.size == 0:
            return
        close_index = self.target_columns.index("close")
        expected_close = np.asarray(expected_signed_bps, dtype=np.float64)[:, :, close_index]
        target_close = np.asarray(target, dtype=np.float64)[:, :, close_index]
        confidence_close = np.asarray(confidence, dtype=np.float64)[:, :, close_index]
        magnitude_std_close = np.asarray(magnitude_std_bps, dtype=np.float64)[:, :, close_index]
        p_up_close = np.asarray(p_up, dtype=np.float64)[:, :, close_index]
        sign_confidence_close = np.asarray(sign_confidence, dtype=np.float64)[:, :, close_index]

        error = expected_close - target_close
        actual_dir = target_close > self.direction_threshold_bps
        expected_dir = expected_close > self.direction_threshold_bps

        self.expected_abs_sum += np.abs(error).sum(axis=0)
        self.expected_sq_sum += np.square(error).sum(axis=0)
        self.expected_pred_sum += expected_close.sum(axis=0)
        self.expected_pred_sq_sum += np.square(expected_close).sum(axis=0)
        self.expected_cross_sum += (expected_close * target_close).sum(axis=0)
        self.expected_dir_correct += (expected_dir == actual_dir).sum(axis=0)
        self.confidence_sum += confidence_close.sum(axis=0)
        self.magnitude_std_sum += magnitude_std_close.sum(axis=0)
        self.p_up_sum += p_up_close.sum(axis=0)
        self.sign_confidence_sum += sign_confidence_close.sum(axis=0)

        for threshold_idx, threshold in enumerate(self.confidence_thresholds):
            mask = confidence_close >= threshold
            self.threshold_count[threshold_idx] += mask.sum(axis=0)
            self.threshold_abs_sum[threshold_idx] += (np.abs(error) * mask).sum(axis=0)
            self.threshold_dir_correct[threshold_idx] += ((expected_dir == actual_dir) * mask).sum(axis=0)

        for bucket_idx, (low, high) in enumerate(zip(self.confidence_bucket_edges[:-1], self.confidence_bucket_edges[1:])):
            if bucket_idx == len(self.confidence_bucket_edges) - 2:
                mask = (confidence_close >= low) & (confidence_close <= high)
            else:
                mask = (confidence_close >= low) & (confidence_close < high)
            self.bucket_count[bucket_idx] += mask.sum(axis=0)
            self.bucket_abs_sum[bucket_idx] += (np.abs(error) * mask).sum(axis=0)
            self.bucket_dir_correct[bucket_idx] += ((expected_dir == actual_dir) * mask).sum(axis=0)
            self.bucket_confidence_sum[bucket_idx] += (confidence_close * mask).sum(axis=0)
            self.bucket_abs_expected_sum[bucket_idx] += (np.abs(expected_close) * mask).sum(axis=0)
            self.bucket_abs_actual_sum[bucket_idx] += (np.abs(target_close) * mask).sum(axis=0)
            self.bucket_magnitude_std_sum[bucket_idx] += (magnitude_std_close * mask).sum(axis=0)

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
            result[f"{prefix}h{horizon}_close_hard_decoded_mae_bps"] = model_mae
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
            self._add_confidence_metrics(result, prefix, horizon_idx, horizon, denominator)
        return result

    def _add_confidence_metrics(
        self,
        result: dict[str, Any],
        prefix: str,
        horizon_idx: int,
        horizon: int,
        denominator: float,
    ) -> None:
        expected_mae = self.expected_abs_sum[horizon_idx] / denominator
        result[f"{prefix}h{horizon}_close_expected_signed_mae_bps"] = expected_mae
        result[f"{prefix}h{horizon}_close_expected_signed_rmse_bps"] = math.sqrt(
            self.expected_sq_sum[horizon_idx] / denominator
        )
        result[f"{prefix}h{horizon}_close_expected_signed_corr"] = _corr(
            n=denominator,
            x_sum=self.expected_pred_sum[horizon_idx],
            y_sum=self.actual_sum[horizon_idx, self.target_columns.index("close")],
            x_sq_sum=self.expected_pred_sq_sum[horizon_idx],
            y_sq_sum=self.actual_sq_sum[horizon_idx, self.target_columns.index("close")],
            cross_sum=self.expected_cross_sum[horizon_idx],
        )
        result[f"{prefix}h{horizon}_close_expected_dir_acc_pct"] = (
            100.0 * self.expected_dir_correct[horizon_idx] / denominator
        )
        result[f"{prefix}h{horizon}_close_mean_confidence"] = self.confidence_sum[horizon_idx] / denominator
        result[f"{prefix}h{horizon}_close_mean_magnitude_std_bps"] = (
            self.magnitude_std_sum[horizon_idx] / denominator
        )
        result[f"{prefix}h{horizon}_close_mean_p_up"] = self.p_up_sum[horizon_idx] / denominator
        result[f"{prefix}h{horizon}_close_mean_sign_confidence"] = (
            self.sign_confidence_sum[horizon_idx] / denominator
        )
        for threshold_idx, threshold in enumerate(self.confidence_thresholds):
            count = self.threshold_count[threshold_idx, horizon_idx]
            key = _confidence_threshold_key(threshold)
            result[f"{prefix}h{horizon}_close_coverage_at_conf_{key}_pct"] = 100.0 * count / denominator
            if count > 0:
                result[f"{prefix}h{horizon}_close_mae_at_conf_{key}_bps"] = (
                    self.threshold_abs_sum[threshold_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_dir_acc_at_conf_{key}_pct"] = (
                    100.0 * self.threshold_dir_correct[threshold_idx, horizon_idx] / count
                )
            else:
                result[f"{prefix}h{horizon}_close_mae_at_conf_{key}_bps"] = math.nan
                result[f"{prefix}h{horizon}_close_dir_acc_at_conf_{key}_pct"] = math.nan
        for bucket_idx, (low, high) in enumerate(zip(self.confidence_bucket_edges[:-1], self.confidence_bucket_edges[1:])):
            count = self.bucket_count[bucket_idx, horizon_idx]
            key = _confidence_bucket_key(low, high)
            result[f"{prefix}h{horizon}_close_conf_bucket_{key}_count"] = count
            result[f"{prefix}h{horizon}_close_conf_bucket_{key}_coverage_pct"] = 100.0 * count / denominator
            if count > 0:
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mae_bps"] = (
                    self.bucket_abs_sum[bucket_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_dir_acc_pct"] = (
                    100.0 * self.bucket_dir_correct[bucket_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_confidence"] = (
                    self.bucket_confidence_sum[bucket_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_abs_expected_bps"] = (
                    self.bucket_abs_expected_sum[bucket_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_abs_actual_bps"] = (
                    self.bucket_abs_actual_sum[bucket_idx, horizon_idx] / count
                )
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_magnitude_std_bps"] = (
                    self.bucket_magnitude_std_sum[bucket_idx, horizon_idx] / count
                )
            else:
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mae_bps"] = math.nan
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_dir_acc_pct"] = math.nan
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_confidence"] = math.nan
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_abs_expected_bps"] = math.nan
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_abs_actual_bps"] = math.nan
                result[f"{prefix}h{horizon}_close_conf_bucket_{key}_mean_magnitude_std_bps"] = math.nan


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


def _confidence_threshold_key(value: float) -> str:
    return str(value).replace(".", "_")


def _confidence_bucket_key(low: float, high: float) -> str:
    return f"{int(round(low * 100)):02d}_{int(round(high * 100)):02d}"


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
