"""Versioned model-domain policy, separate from immutable source validity.

Bounds apply to the split-adjusted values actually supplied by certified shards.
They are not an unadjusted-price trading-eligibility rule. Excluded bars keep
their positions and receive all-false model masks, never clipped prices.
"""
import math
import torch

MIN_PRICE = 0.05
MAX_PRICE = 100000.0
POLICY = "all-valid-OHLC-in-adjusted-range-0.05-100000-v1"


def reasons(values, valid, logarithmic=False):
    """Return per-bar low/high/no-price reasons; low and high may overlap."""
    price = torch.cat([values[..., b:b+4] for b in (0, 6, 12)], -1)
    mask = torch.cat([valid[..., b:b+4] for b in (0, 6, 12)], -1)
    lo, hi = (math.log(MIN_PRICE), math.log(MAX_PRICE)) if logarithmic else (MIN_PRICE, MAX_PRICE)
    low = (mask & (price < lo)).any(-1)
    high = (mask & (price > hi)).any(-1)
    missing = ~mask.any(-1)
    return low, high, missing


def model_validity(values, valid):
    """Hide every input field of rejected bars, including activity and time."""
    low, high, missing = reasons(values, valid, logarithmic=True)
    return valid & (~(low | high | missing))[..., None]
