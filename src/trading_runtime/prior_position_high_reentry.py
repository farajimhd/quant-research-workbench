"""Declared causal prior-held-high confirmation for every reacquisition."""
from dataclasses import dataclass

PRIOR_POSITION_HIGH_REENTRY_RULE = "every-reentry-prior-held-high-cross@1"
PRIOR_POSITION_HIGH_REENTRY_INPUT = "certified-previous-completed-100ms-close-and-prior-held-high@1"

@dataclass(frozen=True)
class PriorPositionHighReentryPolicy:
    def payload(self):
        return {"schema": "prior-position-high-reentry-policy-v1", "every_reentry": True,
                "previous_close_at_or_below_prior_high": True,
                "current_close_strictly_above_prior_high": True}
