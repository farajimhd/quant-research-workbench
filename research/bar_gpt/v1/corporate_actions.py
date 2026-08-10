from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import torch

from research.bar_gpt.v1.schema import FEATURE_INDEX


@dataclass(frozen=True, slots=True)
class SplitAction:
    """A mechanical share-unit change effective at the first modeled session bar."""

    effective_at_us: int
    share_factor: float
    execution_date: str
    source_ticker: str

    def __post_init__(self) -> None:
        if self.effective_at_us <= 0:
            raise ValueError("split effective timestamp must be positive")
        if not self.share_factor > 0:
            raise ValueError("split share factor must be positive")


PRICE_COLUMNS = frozenset(
    {
        *(f"{family}_{field}" for family in ("trade", "bid", "ask") for field in ("open", "high", "low", "close")),
        *(f"{family}_{field}" for family in ("spread", "midpoint", "microprice") for field in ("open", "high", "low", "close", "sum")),
    }
)
PRICE_SQUARED_COLUMNS = frozenset(f"{family}_squared_sum" for family in ("spread", "midpoint", "microprice"))
SIZE_COLUMNS = frozenset(
    f"{family}_{field}"
    for family in ("trade", "bid", "ask")
    for field in ("size_sum", "size_open", "size_high", "size_low", "size_close")
)
SIZE_COLUMNS = frozenset((*SIZE_COLUMNS, "trade_price_eligible_size_sum"))
SIZE_SQUARED_COLUMNS = frozenset(f"{family}_size_squared_sum" for family in ("trade", "bid", "ask"))


def cumulative_share_factors(
    timestamps_us: torch.Tensor,
    actions: Sequence[SplitAction],
) -> torch.Tensor:
    """Return C(t), the cumulative executed share multiplier at every timestamp."""
    if timestamps_us.ndim != 1:
        raise ValueError("timestamps must be one-dimensional")
    result = torch.ones(timestamps_us.shape, dtype=torch.float64, device=timestamps_us.device)
    if not actions or timestamps_us.numel() == 0:
        return result
    ordered = sorted(actions, key=lambda item: item.effective_at_us)
    effective = torch.as_tensor(
        [item.effective_at_us for item in ordered], dtype=timestamps_us.dtype, device=timestamps_us.device
    )
    multipliers = torch.as_tensor(
        [item.share_factor for item in ordered], dtype=torch.float64, device=timestamps_us.device
    ).cumprod(0)
    positions = torch.searchsorted(effective, timestamps_us.contiguous(), right=True) - 1
    valid = positions >= 0
    result[valid] = multipliers[positions[valid]]
    return result


def normalize_features_to_anchor(
    features: torch.Tensor,
    row_timestamps_us: torch.Tensor,
    *,
    anchor_us: int,
    actions: Sequence[SplitAction],
) -> torch.Tensor:
    """Express raw prices and sizes in the share basis known at ``anchor_us``.

    Prices use ``raw_price * C(row) / C(anchor)``. Sizes use the reciprocal
    ratio. Counts, availability, queue geometry, and price-size notional are
    invariant. No split effective after the anchor changes an input row.
    """
    if features.ndim != 2 or row_timestamps_us.ndim != 1 or features.shape[0] != row_timestamps_us.shape[0]:
        raise ValueError("features and timestamps must align as [T,F] and [T]")
    if not actions or features.shape[0] == 0:
        return features
    row_factor = cumulative_share_factors(row_timestamps_us, actions).to(features.dtype)
    anchor_timestamp = torch.as_tensor([anchor_us], dtype=row_timestamps_us.dtype, device=row_timestamps_us.device)
    anchor_factor = cumulative_share_factors(anchor_timestamp, actions)[0].to(features.dtype)
    price_ratio = row_factor / anchor_factor
    size_ratio = anchor_factor / row_factor
    output = features.clone()
    for name in PRICE_COLUMNS:
        output[:, FEATURE_INDEX[name]] *= price_ratio
    for name in PRICE_SQUARED_COLUMNS:
        output[:, FEATURE_INDEX[name]] *= price_ratio.square()
    for name in SIZE_COLUMNS:
        output[:, FEATURE_INDEX[name]] *= size_ratio
    for name in SIZE_SQUARED_COLUMNS:
        output[:, FEATURE_INDEX[name]] *= size_ratio.square()
    return output


def split_execution_dates(actions: Sequence[SplitAction]) -> frozenset[str]:
    return frozenset(item.execution_date for item in actions)
