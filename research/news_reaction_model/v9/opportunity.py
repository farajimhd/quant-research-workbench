from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import torch

from research.news_reaction_model.v9 import HORIZONS


class OpportunityClass(IntEnum):
    NO_MEANINGFUL_OPPORTUNITY = 0
    UPSIDE_DOMINANT = 1
    DOWNSIDE_DOMINANT = 2
    TWO_SIDED_AMBIGUOUS = 3


OPPORTUNITY_CLASS_NAMES = (
    "no_meaningful_opportunity",
    "upside_dominant",
    "downside_dominant",
    "two_sided_ambiguous",
)
OPPORTUNITY_CLASSES = len(OPPORTUNITY_CLASS_NAMES)


@dataclass(frozen=True, slots=True)
class OpportunitySpec:
    horizon: str
    minimum_span_pct: float
    dominance_ratio: float = 1.25

    def classify(self, high_return: float, low_return: float) -> int:
        if not np.isfinite(high_return) or not np.isfinite(low_return):
            return -1
        upside_pct = max(0.0, float(high_return) * 100.0)
        downside_pct = max(0.0, -float(low_return) * 100.0)
        if upside_pct + downside_pct <= self.minimum_span_pct:
            return int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY)
        dominant = max(upside_pct, downside_pct)
        weaker = min(upside_pct, downside_pct)
        if weaker > 0.0 and dominant < self.dominance_ratio * weaker:
            return int(OpportunityClass.TWO_SIDED_AMBIGUOUS)
        if upside_pct > downside_pct:
            return int(OpportunityClass.UPSIDE_DOMINANT)
        if downside_pct > upside_pct:
            return int(OpportunityClass.DOWNSIDE_DOMINANT)
        return int(OpportunityClass.TWO_SIDED_AMBIGUOUS)


# These are the unchanged V8 flat-span widths. Reusing them makes V9 a
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
    """Build one four-class opportunity target for every valid article/horizon."""
    result: dict[str, torch.Tensor] = {}
    for horizon_index, horizon in enumerate(HORIZONS):
        spec = OPPORTUNITY_SPECS[horizon]
        high = return_targets[:, horizon_index, 1]
        low = return_targets[:, horizon_index, 2]
        upside_pct = torch.clamp(high * 100.0, min=0.0)
        downside_pct = torch.clamp(-low * 100.0, min=0.0)
        span_pct = upside_pct + downside_pct
        dominant = torch.maximum(upside_pct, downside_pct)
        weaker = torch.minimum(upside_pct, downside_pct)

        target = torch.full_like(high, int(OpportunityClass.TWO_SIDED_AMBIGUOUS), dtype=torch.long)
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
        ambiguous = (weaker > 0.0) & (dominant < float(spec.dominance_ratio) * weaker)
        target = torch.where(
            ambiguous,
            torch.full_like(target, int(OpportunityClass.TWO_SIDED_AMBIGUOUS)),
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
                "dominance_ratio": spec.dominance_ratio,
            }
            for horizon, spec in OPPORTUNITY_SPECS.items()
        },
        "no_meaningful": "max(high, 0) + max(-low, 0) <= minimum_span_pct",
        "two_sided_ambiguous": "both sides move and dominant excursion < dominance_ratio * weaker excursion",
        "direction": "otherwise the excursion with the larger absolute magnitude",
    }
