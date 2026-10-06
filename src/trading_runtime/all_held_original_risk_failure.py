"""Closed all-held extended-session failure extension, without order authority.

Only the separate declared policy selects this rule. Inherited exits retain
priority; the legacy first-minute original-risk policy is unchanged.
"""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from .strategy_followthrough_failure import (
    FollowThroughFailure, FollowThroughFailureInput, followthrough_failure,
)


ALL_HELD_ORIGINAL_RISK_RULE = 'held-extended-session-quarter-original-risk-failure@1'
ALL_HELD_ORIGINAL_RISK_INPUT = 'declared-all-held-original-risk-source@1'


@dataclass(frozen=True, slots=True)
class AllHeldOriginalRiskPolicy:
    """Exact quarter-risk PM/AH alternative, with no elapsed holding cap."""
    policy_id: str = ALL_HELD_ORIGINAL_RISK_RULE
    premarket_fraction: tuple[int, int] = (1, 4)
    afterhours_fraction: tuple[int, int] = (1, 4)

    def __post_init__(self):
        if (type(self.policy_id) is not str or self.policy_id != ALL_HELD_ORIGINAL_RISK_RULE
                or any(type(value) is not tuple or len(value) != 2
                       or any(type(part) is not int for part in value) or value != (1, 4)
                       for value in (self.premarket_fraction,self.afterhours_fraction))):
            raise ValueError('All-held original-risk policy requires its exact closed quarter-risk declaration')

    def payload(self):
        return dict(policy_id=self.policy_id,premarket_fraction=self.premarket_fraction,
            afterhours_fraction=self.afterhours_fraction,eligibility='held_in_original_extended_session',
            age_origin='native_first_completed_held_bucket',
            threshold='original_reference_ask - fraction * original_risk',
            price='completed_5s_close_and_fresh_bid_at_or_below_threshold',
            momentum='completed_5s_macd_line_strictly_below_signal',
            source='wholly_post_held_completed_5s_at_exact_decision_boundary',
            quote_max_age_us=1_000_000,pending_exit='no_duplicate_exit',
            missing='no_synthetic_observations',priority='inherited_exits_first')


def all_held_original_risk_failure(
    value: FollowThroughFailureInput, *, policy: AllHeldOriginalRiskPolicy,
) -> FollowThroughFailure | None:
    """Require causal completed evidence and adverse price/momentum, never time alone."""
    if type(policy) is not AllHeldOriginalRiskPolicy:
        raise ValueError('All-held failure requires its exact typed policy')
    policy.__post_init__()
    # Preserve the existing malformed-position validator; its different price
    # predicate is not evidence that the separate extension is eligible.
    followthrough_failure(value)
    pm = 0 < value.first_held_boundary_ms < 19_800_000 and value.boundary_ms <= 19_800_000
    ah = 43_200_000 <= value.first_held_boundary_ms < 57_600_000 and value.boundary_ms <= 57_600_000
    if (not (pm or ah) or value.position_quantity == 0 or value.pending_exit
            or value.boundary_ms % 5_000
            or type(value.completed_five_second_boundary_ms) is not int
            or value.completed_five_second_boundary_ms != value.boundary_ms
            or value.boundary_ms - 5_000 < value.first_held_boundary_ms
            or not value.price_valid
            or type(value.completed_five_second_close_int) is not int
            or value.completed_five_second_close_int <= 0
            or any(type(part) not in (int,float) or not isfinite(part)
                   for part in (value.macd_line,value.macd_signal,value.bid,value.ask))
            or not 0 < value.bid <= value.ask
            or type(value.quote_age_us) is not int or not 0 <= value.quote_age_us <= 1_000_000
            or value.macd_line >= value.macd_signal):
        return None
    numerator,denominator = policy.premarket_fraction if pm else policy.afterhours_fraction
    reference = Fraction(str(value.reference_ask))
    risk = reference - Fraction(str(value.initial_stop))
    threshold_scaled = reference * denominator - risk * numerator
    if (value.completed_five_second_close_int * denominator > threshold_scaled * 10_000
            or Fraction(str(value.bid)) * denominator > threshold_scaled):
        return None
    return FollowThroughFailure(value.boundary_ms,value.first_held_boundary_ms,
        value.reference_ask,value.initial_stop,value.completed_five_second_close_int,
        float(value.macd_line),float(value.macd_signal),float(value.bid),float(value.ask),value.quote_age_us)
