from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import torch

from research.news_reaction_model.v10 import HORIZONS


class OpportunityClass(IntEnum):
    NO_MEANINGFUL_OPPORTUNITY = 0
    UPSIDE_DOMINANT = 1
    DOWNSIDE_DOMINANT = 2


OPPORTUNITY_CLASS_NAMES = (
    "no_meaningful_opportunity",
    "upside_dominant",
    "downside_dominant",
)
OPPORTUNITY_CLASSES = len(OPPORTUNITY_CLASS_NAMES)


@dataclass(frozen=True, slots=True)
class OpportunitySpec:
    horizon: str
    minimum_span_pct: float

    def classify(self, high_return: float, low_return: float) -> int:
        if not np.isfinite(high_return) or not np.isfinite(low_return):
            return -1
        upside_pct = max(0.0, float(high_return) * 100.0)
        downside_pct = max(0.0, -float(low_return) * 100.0)
        if upside_pct + downside_pct <= self.minimum_span_pct:
            return int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY)
        if upside_pct > downside_pct:
            return int(OpportunityClass.UPSIDE_DOMINANT)
        if downside_pct > upside_pct:
            return int(OpportunityClass.DOWNSIDE_DOMINANT)
        # An exact non-zero tie has no defensible directional winner and remains
        # abstained. Discrete market prices make this a real, testable boundary.
        return int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY)


# These are the unchanged V8 flat-span widths. Reusing them makes V10 a
# learning-task ablation instead of introducing a second threshold experiment.
_MINIMUM_SPAN_BY_HORIZON = {
    "1m": 0.10,
    "5m": 0.20,
    "10m": 0.20,
    "30m": 0.50,
    "1h": 0.50,
    "2h": 1.00,
    "3h": 1.00,
    "premarket_close": 1.00,
    "regular_close": 1.00,
    "extended_close": 1.00,
}

OPPORTUNITY_SPECS = {
    horizon: OpportunitySpec(horizon, _MINIMUM_SPAN_BY_HORIZON[horizon])
    for horizon in HORIZONS
}


def opportunity_targets(
    return_targets: torch.Tensor,
    label_mask: torch.Tensor,
) -> dict[str, torch.Tensor]:
    """Build one three-class opportunity target for every valid article/horizon."""
    result: dict[str, torch.Tensor] = {}
    for horizon_index, horizon in enumerate(HORIZONS):
        spec = OPPORTUNITY_SPECS[horizon]
        high = return_targets[:, horizon_index, 1]
        low = return_targets[:, horizon_index, 2]
        upside_pct = torch.clamp(high * 100.0, min=0.0)
        downside_pct = torch.clamp(-low * 100.0, min=0.0)
        span_pct = upside_pct + downside_pct

        # Ties start as no-opportunity and are only replaced when one absolute
        # excursion is strictly larger. This makes the target action-complete:
        # abstain, long, or short, with no separate path-risk class.
        target = torch.full_like(
            high,
            int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
            dtype=torch.long,
        )
        target = torch.where(
            upside_pct > downside_pct,
            torch.full_like(target, int(OpportunityClass.UPSIDE_DOMINANT)),
            target,
        )
        target = torch.where(
            downside_pct > upside_pct,
            torch.full_like(target, int(OpportunityClass.DOWNSIDE_DOMINANT)),
            target,
        )
        target = torch.where(
            span_pct <= float(spec.minimum_span_pct),
            torch.full_like(target, int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY)),
            target,
        )
        valid = (
            label_mask[:, horizon_index]
            & torch.isfinite(high)
            & torch.isfinite(low)
            & (high >= -1.0)
            & (low >= -1.0)
        )
        result[horizon] = torch.where(valid, target, torch.full_like(target, -1))
    return result


def opportunity_contract() -> dict[str, object]:
    return {
        "classes": list(OPPORTUNITY_CLASS_NAMES),
        "rules": {
            horizon: {
                "minimum_span_pct": spec.minimum_span_pct,
            }
            for horizon, spec in OPPORTUNITY_SPECS.items()
        },
        "no_meaningful": "max(high, 0) + max(-low, 0) <= minimum_span_pct",
        "direction": (
            "for every meaningful move, the larger absolute excursion determines "
            "upside or downside; an exact tie abstains"
        ),
    }
