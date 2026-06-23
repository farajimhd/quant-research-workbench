from __future__ import annotations

import torch
import torch.nn.functional as F


def masked_return_loss(
    prediction_norm: torch.Tensor,
    target_norm: torch.Tensor,
    valid_mask: torch.Tensor,
    *,
    loss_name: str = "mse",
    huber_beta: float = 1.0,
) -> torch.Tensor:
    valid = valid_mask.to(dtype=torch.bool)
    if not bool(valid.any()):
        return prediction_norm.sum() * 0.0
    pred = prediction_norm[valid]
    target = target_norm[valid]
    if loss_name == "huber":
        return F.smooth_l1_loss(pred, target, beta=float(huber_beta), reduction="mean")
    if loss_name == "mse":
        return F.mse_loss(pred, target, reduction="mean")
    raise ValueError(f"Unsupported return loss: {loss_name!r}")


@torch.no_grad()
def return_metrics(
    prediction_norm: torch.Tensor,
    target_norm: torch.Tensor,
    valid_mask: torch.Tensor,
    *,
    return_bps_scale: float,
    horizon_names: tuple[str, ...],
    prefix: str,
) -> dict[str, float]:
    valid = valid_mask.to(dtype=torch.bool)
    metrics: dict[str, float] = {}
    if not bool(valid.any()):
        metrics[f"{prefix}/valid_fraction"] = 0.0
        return metrics
    pred_bps = prediction_norm.float() * float(return_bps_scale)
    target_bps = target_norm.float() * float(return_bps_scale)
    error = pred_bps - target_bps
    metrics[f"{prefix}/valid_fraction"] = float(valid.float().mean().item())
    metrics[f"{prefix}/mae_bps"] = float(error[valid].abs().mean().item())
    metrics[f"{prefix}/rmse_bps"] = float(torch.sqrt(torch.mean(error[valid].pow(2))).item())
    metrics[f"{prefix}/sign_accuracy"] = float((torch.sign(pred_bps[valid]) == torch.sign(target_bps[valid])).float().mean().item())
    for idx, name in enumerate(horizon_names):
        mask = valid[:, idx]
        if not bool(mask.any()):
            continue
        horizon_error = error[:, idx][mask]
        horizon_pred = pred_bps[:, idx][mask]
        horizon_target = target_bps[:, idx][mask]
        metrics[f"{prefix}/{name}_mae_bps"] = float(horizon_error.abs().mean().item())
        metrics[f"{prefix}/{name}_rmse_bps"] = float(torch.sqrt(torch.mean(horizon_error.pow(2))).item())
        metrics[f"{prefix}/{name}_sign_accuracy"] = float((torch.sign(horizon_pred) == torch.sign(horizon_target)).float().mean().item())
        if horizon_pred.numel() > 2:
            pred_centered = horizon_pred - horizon_pred.mean()
            target_centered = horizon_target - horizon_target.mean()
            denom = pred_centered.norm() * target_centered.norm()
            if float(denom.item()) > 0.0:
                metrics[f"{prefix}/{name}_pearson"] = float((pred_centered * target_centered).sum().div(denom).item())
    return metrics

