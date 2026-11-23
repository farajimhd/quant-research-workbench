"""Same-bar supervision: equal price/activity families plus coordinate accuracy."""

import torch
from torch.nn import functional as F
from .transforms import coordinates


def objective(model, out, values, valid):
    """Mask SOURCE-invalid fields; teach the predicted reference and deviations.

    Log-price residuals use the previous Phase0 factor1000; log1p activity residuals
    use factor1. This is a baseline objective, not a claim of optimal loss scaling.
    No raw target or true reference is supplied to the decoder. Empty channels do
    not dilute the family average. An entirely missing bar has zero market loss.
    """
    code, mask = coordinates(values[..., :18], valid[..., :18])
    source_valid = valid[..., :18]
    # Mask BEFORE arithmetic so invalid NaN/Inf placeholders cannot poison grads.
    target = torch.where(source_valid, values[..., :18], out["logs"].detach())
    factors = values.new_tensor(
        [model.config.price_factor if j % 6 < 4 else 1.0 for j in range(18)]
    )
    error = (out["logs"] - target) * factors
    element = F.smooth_l1_loss(error, torch.zeros_like(error), reduction="none")
    # Explicit flattening also supports a single unbatched (27,) input.
    counts = source_valid.reshape(-1, 18).sum(0)
    channel = torch.where(source_valid, element, 0).reshape(-1, 18).sum(0) / counts.clamp_min(1)

    def family(indices):
        present = counts[indices] > 0
        return (channel[indices] * present).sum() / present.sum().clamp_min(1)

    price = family([j for j in range(18) if j % 6 < 4])
    activity = family([j for j in range(18) if j % 6 >= 4])
    target_code = torch.where(mask, code, model.mean[:18])
    coordinate_error = F.smooth_l1_loss(
        out["normalized"], (target_code - model.mean[:18]) / model.scale[:18], reduction="none"
    )
    coordinate = torch.where(mask, coordinate_error, 0).sum() / mask.sum().clamp_min(1)
    total = 0.5 * (price + activity) + 0.1 * coordinate
    return total, dict(price=price, activity=activity, coordinate=coordinate)
