from __future__ import annotations

from dataclasses import dataclass
from math import inf

import numpy as np
import torch

from research.news_reaction_model.v5 import HORIZONS


TARGET_NAMES = ("ending", "high", "low")

# Percentage boundaries are deliberately interpretable. Every horizon shares the
# economically important tails; only the resolution around zero changes.
_TAILS = (-50.0, -20.0, -10.0, -5.0, -2.0, -1.0)
_POSITIVE_TAILS = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
_INNER_BY_HORIZON: dict[str, tuple[float, ...]] = {
    "1m": (-0.5, -0.25, -0.10, -0.05, 0.05, 0.10, 0.25, 0.5),
    "5m": (-0.5, -0.25, -0.10, 0.10, 0.25, 0.5),
    "10m": (-0.5, -0.25, -0.10, 0.10, 0.25, 0.5),
    "30m": (-0.5, -0.25, 0.25, 0.5),
    "1h": (-0.5, -0.25, 0.25, 0.5),
    "2h": (-0.5, 0.5),
    "3h": (-0.5, 0.5),
    "premarket_close": (-0.5, 0.5),
    "regular_close": (-0.5, 0.5),
    "extended_close": (-0.5, 0.5),
}


@dataclass(frozen=True, slots=True)
class RangeSpec:
    horizon: str
    upper_bounds_pct: tuple[float, ...]
    minimum_span_pct: float

    @property
    def classes(self) -> int:
        return len(self.upper_bounds_pct) + 1

    @property
    def intervals_pct(self) -> tuple[tuple[float, float], ...]:
        lowers = (-100.0, *self.upper_bounds_pct)
        uppers = (*self.upper_bounds_pct, inf)
        return tuple(zip(lowers, uppers))

    def interval(self, class_index: int) -> tuple[float, float]:
        if class_index < 0 or class_index >= self.classes:
            raise IndexError(f"Range class {class_index} is outside [0, {self.classes}) for {self.horizon}")
        return self.intervals_pct[class_index]

    def class_for_return(self, value: float) -> int:
        if not np.isfinite(value) or value < -1.0:
            return -1
        return int(np.searchsorted(self.upper_bounds_pct, value * 100.0, side="right"))

    def classes_for_returns(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        result = np.searchsorted(self.upper_bounds_pct, values * 100.0, side="right").astype(np.int64)
        result[~np.isfinite(values) | (values < -1.0)] = -1
        return result

    def conservative_upside_pct(self, class_index: int) -> float:
        lower, _ = self.interval(class_index)
        return max(0.0, lower)

    def conservative_downside_pct(self, class_index: int) -> float:
        _, upper = self.interval(class_index)
        return max(0.0, -upper)


def _spec(horizon: str) -> RangeSpec:
    inner = _INNER_BY_HORIZON[horizon]
    boundaries = tuple(sorted(set((*_TAILS, *inner, *_POSITIVE_TAILS))))
    flat_width = next(value for value in boundaries if value > 0) * 2.0
    return RangeSpec(horizon=horizon, upper_bounds_pct=boundaries, minimum_span_pct=flat_width)


RANGE_SPECS: dict[str, RangeSpec] = {horizon: _spec(horizon) for horizon in HORIZONS}


def range_targets(return_targets: torch.Tensor, label_mask: torch.Tensor) -> dict[str, torch.Tensor]:
    """Convert raw ending/high/low return fractions into horizon-specific range classes."""
    result: dict[str, torch.Tensor] = {}
    for horizon_index, horizon in enumerate(HORIZONS):
        values = return_targets[:, horizon_index, :] * 100.0
        boundaries = torch.tensor(
            RANGE_SPECS[horizon].upper_bounds_pct,
            device=return_targets.device,
            dtype=return_targets.dtype,
        )
        classes = torch.bucketize(values.contiguous(), boundaries, right=True).long()
        valid = label_mask[:, horizon_index].unsqueeze(-1) & torch.isfinite(values) & (values >= -100.0)
        result[horizon] = torch.where(valid, classes, torch.full_like(classes, -1))
    return result


def describe_ranges() -> dict[str, list[dict[str, float | None]]]:
    description: dict[str, list[dict[str, float | None]]] = {}
    for horizon, spec in RANGE_SPECS.items():
        description[horizon] = [
            {
                "class": index,
                "lower_pct": lower,
                "upper_pct": None if upper == inf else upper,
            }
            for index, (lower, upper) in enumerate(spec.intervals_pct)
        ]
    return description
