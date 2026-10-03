"""Synthetic executable fixtures only; never historical profitability evidence."""

from dataclasses import replace
import torch

from .tape import SqueezeTape
from .timing import TIMING_CONTRACT, timing_fingerprint


def synthetic_tape(prices=None, *, seconds=90, listings=2, device="cpu"):
    if prices is None:
        prices = 10 + torch.arange(seconds, dtype=torch.float64)[:, None] * 0.002
        prices = prices.expand(-1, listings).clone()
    else:
        prices = torch.as_tensor(prices, dtype=torch.float64)
        if prices.ndim == 1:
            prices = prices[:, None]
        seconds, listings = prices.shape
    clocks = torch.arange(1, seconds + 1, dtype=torch.int64)
    names = tuple(f"T{i:04}" for i in range(listings))
    observed = torch.ones_like(prices, dtype=torch.bool)
    volume = torch.full_like(prices, 10000)
    line = torch.full((seconds, listings, 4), 0.1, dtype=torch.float64)
    levels = (
        10.5 + torch.arange(15, dtype=torch.float64)[None].expand(listings, -1) * 0.5
    )
    result = SqueezeTape(
        names,
        clocks,
        torch.full((listings,), 8, dtype=torch.int64),
        prices,
        observed,
        prices + 0.02,
        prices - 0.02,
        prices - 0.1,
        prices - 0.005,
        prices + 0.005,
        observed.clone(),
        volume,
        volume * prices,
        torch.full_like(prices, 100),
        prices.clone(),
        line,
        torch.zeros_like(line),
        observed.clone(),
        torch.ones((listings, 15), dtype=torch.int64),
        torch.full((listings, 15), seconds + 1, dtype=torch.int64),
        levels,
        torch.ones((listings, 15), dtype=torch.bool),
        {
            "synthetic": True,
            "fingerprint": "synthetic-fixture",
            "start_second": 1,
            "end_second": seconds,
            "timing_contract": dict(TIMING_CONTRACT),
            "timing_fingerprint": timing_fingerprint(),
        },
    )
    return result.to(device)
