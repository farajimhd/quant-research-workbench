from __future__ import annotations

import torch


def masked_quantile_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    quantiles: tuple[float, ...],
) -> torch.Tensor:
    """Pinball loss for prediction [B,N,H,K,Q] and target/mask [B,N,H,K]."""
    if prediction.shape[:-1] != target.shape or target.shape != mask.shape:
        raise ValueError("prediction, target, and mask shapes do not satisfy the quantile contract")
    q = torch.as_tensor(quantiles, device=prediction.device, dtype=prediction.dtype)
    error = target.unsqueeze(-1).to(prediction.dtype) - prediction
    loss = torch.maximum(q * error, (q - 1.0) * error)
    valid = mask.unsqueeze(-1).expand_as(loss)
    if not torch.any(valid):
        return prediction.sum() * 0.0
    return loss[valid].mean()


def masked_huber_loss(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, delta: float = 1.0) -> torch.Tensor:
    if prediction.shape != target.shape or target.shape != mask.shape:
        raise ValueError("prediction, target, and mask must have identical shapes")
    if not torch.any(mask):
        return prediction.sum() * 0.0
    return torch.nn.functional.huber_loss(prediction[mask], target[mask].to(prediction.dtype), delta=delta)
