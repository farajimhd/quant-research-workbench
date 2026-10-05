"""Declared early failure extension; no release, storage or order authority.

The caller evaluates its inherited exits first. This additional rule consumes
the same completed producer/held-position contract and returns the same scalar
witness. Existing numbered strategies do not import or select this rule.
"""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from .strategy_followthrough_failure import (
    FollowThroughFailure, FollowThroughFailureInput, followthrough_failure,
)


@dataclass(frozen=True, slots=True)
class EarlyOriginalRiskPolicy:
    """Exact original-risk fractions, separately declared for PM and AH."""

    policy_id: str
    premarket_fraction: tuple[int, int] | None
    afterhours_fraction: tuple[int, int] | None
    eligibility_ms: int = 60_000

    def __post_init__(self):
        if (type(self.policy_id) is not str or not self.policy_id.strip()
                or self.policy_id != self.policy_id.strip()
                or type(self.eligibility_ms) is not int
                or not 5_000 <= self.eligibility_ms <= 60_000
                or self.eligibility_ms % 100):
            raise ValueError("Early failure needs an explicit bounded policy")
        if self.premarket_fraction is None and self.afterhours_fraction is None:
            raise ValueError("Early failure must declare an eligible session")
        for fraction in (self.premarket_fraction, self.afterhours_fraction):
            if fraction is not None and (
                type(fraction) is not tuple or len(fraction) != 2
                or any(type(v) is not int for v in fraction)
                or not 0 < fraction[0] < fraction[1] <= 10_000
            ):
                raise ValueError("Original-risk fraction needs exact positive integers")

    def payload(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "premarket_fraction": self.premarket_fraction,
            "afterhours_fraction": self.afterhours_fraction,
            "eligibility_ms": self.eligibility_ms,
            "age_origin": "native_first_completed_held_bucket",
            "threshold": "original_reference_ask - fraction * original_risk",
            "price": "completed_5s_close_and_fresh_bid_at_or_below_threshold",
            "momentum": "completed_5s_macd_line_strictly_below_signal",
            "source": "wholly_post_held_completed_5s_at_exact_decision_boundary",
            "quote_max_age_us": 1_000_000,
            "pending_exit": "no_duplicate_exit",
            "missing": "no_synthetic_observations",
            "priority": "inherited_exits_first",
        }


def early_original_risk_failure(
    value: FollowThroughFailureInput, *, policy: EarlyOriginalRiskPolicy,
) -> FollowThroughFailure | None:
    """Confirm early loss and negative histogram; elapsed time alone never exits."""
    if type(policy) is not EarlyOriginalRiskPolicy:
        raise ValueError("Early failure requires its declared policy")
    # Reuse the original malformed-position validation, without treating its
    # different price predicate's None result as evidence of eligibility.
    followthrough_failure(value)
    pm = (0 < value.first_held_boundary_ms < 19_800_000
          and value.boundary_ms <= 19_800_000)
    ah = (43_200_000 <= value.first_held_boundary_ms < 57_600_000
          and value.boundary_ms <= 57_600_000)
    fraction = policy.premarket_fraction if pm else policy.afterhours_fraction if ah else None
    if (fraction is None or value.position_quantity == 0 or value.pending_exit
            or value.boundary_ms % 5_000
            or value.boundary_ms - value.first_held_boundary_ms > policy.eligibility_ms
            or type(value.completed_five_second_boundary_ms) is not int
            or value.completed_five_second_boundary_ms != value.boundary_ms
            or value.boundary_ms - 5_000 < value.first_held_boundary_ms
            or not value.price_valid
            or type(value.completed_five_second_close_int) is not int
            or value.completed_five_second_close_int <= 0
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (value.macd_line, value.macd_signal, value.bid, value.ask))
            or not 0 < value.bid <= value.ask
            or type(value.quote_age_us) is not int
            or not 0 <= value.quote_age_us <= 1_000_000
            or value.macd_line >= value.macd_signal):
        return None
    reference = Fraction(str(value.reference_ask))
    risk = reference - Fraction(str(value.initial_stop))
    # Cross-multiply integers to avoid division rounding at the exact boundary.
    numerator, denominator = fraction
    threshold_scaled = reference * denominator - risk * numerator
    if (value.completed_five_second_close_int * denominator > threshold_scaled * 10_000
            or Fraction(str(value.bid)) * denominator > threshold_scaled):
        return None
    return FollowThroughFailure(
        value.boundary_ms, value.first_held_boundary_ms,
        value.reference_ask, value.initial_stop, value.completed_five_second_close_int,
        float(value.macd_line), float(value.macd_signal), float(value.bid), float(value.ask),
        value.quote_age_us,
    )
