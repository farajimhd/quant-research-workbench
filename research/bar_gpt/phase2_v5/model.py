"""V29-only readout from a transferable residual forecast embedding."""
import torch
from torch import nn
from ..phase0_v7.transforms import coordinates
from ..phase0_v7.objectives import loss_factors

HORIZONS = (10, 6, 1, 1, 1)
NAMES = ("1s", "10s", "1m", "1h", "daily")
SLOTS = sum(HORIZONS)


class Residual(nn.Module):
    def __init__(self, width=128):
        super().__init__()
        self.branch = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 2 * width),
                                    nn.GELU(), nn.Linear(2 * width, width))

    def forward(self, x):
        return x + self.branch(x)


class Forecast(nn.Module):
    def __init__(self, frame_width=64, embedding=128):
        super().__init__()
        self.compress = nn.Linear(64 * 32, frame_width)
        self.context = nn.Sequential(nn.Linear(5 * frame_width, embedding), nn.GELU(),
                                     Residual(embedding), Residual(embedding), nn.LayerNorm(embedding))
        # One packed GEMM implements five independent timeframe readouts.
        self.heads = nn.Linear(embedding, SLOTS * 32)

    def encode(self, windows):
        return self.context(self.compress(windows.flatten(-2)).flatten(1))

    def forward(self, windows):
        return self.heads(self.encode(windows)).reshape(-1, SLOTS, 32)


def objective(decoder, out, logs, valid):
    """Phase0 V7/V29 coordinates and equal price/activity families per frame."""
    code, observed = coordinates(logs, valid)
    ratio = torch.arange(18, device=valid.device) == 5
    keep = torch.where(ratio, observed, valid)
    pred = torch.where(ratio, out["code"], out["logs"])
    target = torch.where(ratio, code, logs)
    factors = loss_factors(decoder.config.price_factor, logs.device, logs.dtype)
    native = torch.nn.functional.smooth_l1_loss((pred - target) * factors,
                                              torch.zeros_like(pred), reduction="none")
    normalized = (code - decoder.mean[:18]) / decoder.scale[:18]
    coord = torch.nn.functional.smooth_l1_loss(out["normalized"], normalized, reduction="none")
    losses = []
    available = []
    start = 0
    for width in HORIZONS:
        section = slice(start, start + width)
        counts = keep[:, section].sum((0, 1))
        summed = torch.where(keep[:, section], native[:, section], 0).sum((0, 1))
        cells = (summed / counts.clamp_min(1)).reshape(3, 6)
        active = counts.reshape(3, 6) > 0
        price = cells[:, :4].sum() / active[:, :4].sum().clamp_min(1)
        activity = cells[:, 4:].sum() / active[:, 4:].sum().clamp_min(1)
        mask = observed[:, section]
        shape = torch.where(mask, coord[:, section], 0).sum() / mask.sum().clamp_min(1)
        losses.append(.5 * (price + activity) + .1 * shape)
        available.append(active.any())
        start += width
    return torch.stack(losses).sum() / torch.stack(available).sum().clamp_min(1)
