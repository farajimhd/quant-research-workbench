"""Same-bar supervision: equal price/activity families plus coordinate accuracy."""

import torch
from functools import lru_cache
from torch.nn import functional as F
from .transforms import coordinates


@lru_cache(maxsize=16)
def loss_factors(price_factor, device, dtype):
    """Create an immutable device constant during warmup, outside graph capture."""
    return torch.tensor(
        [price_factor if j % 6 < 4 else 1.0 for j in range(18)], device=device, dtype=dtype
    )


def objective(model, out, values, valid):
    """Mask SOURCE-invalid fields; teach the predicted reference and deviations.

    Log-price residuals use the previous Phase0 factor1000; log1p activity residuals
    use factor1. This is a baseline objective, not a claim of optimal loss scaling.
    No raw target or true reference is supplied to the decoder. Empty channels do
    not dilute the family average. An entirely missing bar has zero market loss.
    """
    code, mask = coordinates(values[..., :18], valid[..., :18])
    source_valid = torch.stack([mask[..., 5] if j == 5 else valid[..., j] for j in range(18)], -1)
    predicted = torch.stack([out["code"][..., 5] if j == 5 else out["logs"][..., j] for j in range(18)], -1)
    supervised = torch.stack([code[..., 5] if j == 5 else values[..., j] for j in range(18)], -1)
    # Mask BEFORE arithmetic so invalid NaN/Inf placeholders cannot poison grads.
    target = torch.where(source_valid, supervised, predicted.detach())
    factors = loss_factors(model.config.price_factor, values.device, values.dtype)
    error = (predicted - target) * factors
    element = F.smooth_l1_loss(error, torch.zeros_like(error), reduction="none")
    # Explicit flattening also supports a single unbatched (27,) input.
    counts = source_valid.reshape(-1, 18).sum(0)
    channel = torch.where(source_valid, element, 0).reshape(-1, 18).sum(0) / counts.clamp_min(1)

    def family(columns):
        present = counts.reshape(3, 6)[:, columns] > 0
        selected = channel.reshape(3, 6)[:, columns]
        return (selected * present).sum() / present.sum().clamp_min(1)

    price = family(slice(0, 4))
    activity = family(slice(4, 6))
    target_code = torch.where(mask, code, model.mean[:18])
    coordinate_error = F.smooth_l1_loss(
        out["normalized"], (target_code - model.mean[:18]) / model.scale[:18], reduction="none"
    )
    coordinate = torch.where(mask, coordinate_error, 0).sum() / mask.sum().clamp_min(1)
    total = 0.5 * (price + activity) + 0.1 * coordinate
    return total, dict(price=price, activity=activity, coordinate=coordinate)
