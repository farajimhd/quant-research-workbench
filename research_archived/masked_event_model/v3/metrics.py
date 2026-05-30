from __future__ import annotations

import numpy as np

from research.masked_event_model.v3.targets import decode_binary_magnitude_logits_to_bps


def forecast_metrics(logits: np.ndarray, target_bps: np.ndarray, *, prefix: str) -> dict[str, float]:
    prediction_bps = decode_binary_magnitude_logits_to_bps(logits).reshape(target_bps.shape)
    target = np.asarray(target_bps, dtype=np.float64)
    diff = prediction_bps - target
    naive = np.zeros_like(target)
    metrics = {
        f"{prefix}/overall_mae_bps": float(np.mean(np.abs(diff))),
        f"{prefix}/overall_rmse_bps": float(np.sqrt(np.mean(np.square(diff)))),
        f"{prefix}/overall_dir_acc_pct": float(np.mean(np.sign(prediction_bps) == np.sign(target)) * 100.0),
        f"{prefix}/overall_edge_vs_naive_bps": float(np.mean(np.abs(naive - target)) - np.mean(np.abs(diff))),
    }
    for index in range(target.shape[1]):
        horizon = index + 1
        pred_h = prediction_bps[:, index]
        target_h = target[:, index]
        diff_h = pred_h - target_h
        metrics[f"{prefix}/h{horizon}_mae_bps"] = float(np.mean(np.abs(diff_h)))
        metrics[f"{prefix}/h{horizon}_rmse_bps"] = float(np.sqrt(np.mean(np.square(diff_h))))
        metrics[f"{prefix}/h{horizon}_dir_acc_pct"] = float(np.mean(np.sign(pred_h) == np.sign(target_h)) * 100.0)
        metrics[f"{prefix}/h{horizon}_edge_vs_naive_bps"] = float(np.mean(np.abs(target_h)) - np.mean(np.abs(diff_h)))
    return metrics
