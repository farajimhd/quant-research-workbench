"""Strategy25 price/risk refinement over exact producer failure observations."""
from math import isfinite
from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput
from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS

POLICY_ID = 'strategy-twenty-five-premarket-quarter-original-risk-failure-v1'
PREMARKET_END_MS = 19_800_000


def quarter_risk_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'premarket_threshold': '(3 * original_reference_ask + original_initial_stop) / 4',
        'afterhours_threshold': 'unchanged_parent24_half_original_stop_distance',
        'scope': 'premarket_first_held_boundary_before_0930_et',
        'eligibility_ms': EARLY_FAILURE_WINDOW_MS,
        'age_origin': 'first_completed_broker_bucket_containing_held_quantity',
        'conditions': 'completed_5s_bucket_wholly_after_first_held; negative_producer_macd; valid_close_and_fresh_bid_at_or_below_threshold',
        'quote_max_age_us': 1_000_000,
        'missing': 'no_synthetic_observations',
    }


def premarket_quarter_risk_failure(value: FollowThroughFailureInput) -> FollowThroughFailure | None:
    """Quarter original risk in PM, inherited half risk in AH, first minute only.

    Uses the same completed 5s, negative MACD and fresh quote authority as
    Strategy24. Age starts at actual held quantity; time alone never exits.
    Current price fields and MACD remain producer facts, not recomputations.
    """
    if (type(value) is not FollowThroughFailureInput
            or type(value.boundary_ms) is not int
            or not 0 < value.boundary_ms <= 57_600_000
            or value.boundary_ms % 100
            or type(value.first_held_boundary_ms) is not int
            or not 0 < value.first_held_boundary_ms <= value.boundary_ms
            or value.first_held_boundary_ms % 100
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (value.reference_ask, value.initial_stop, value.position_quantity))
            or not 0 < value.initial_stop < value.reference_ask
            or value.position_quantity < 0
            or type(value.price_valid) is not bool
            or type(value.pending_exit) is not bool):
        raise ValueError('Follow-through rule needs exact causal position authority')
    if value.position_quantity == 0 or value.pending_exit or value.boundary_ms % 5000:
        return None
    if (value.completed_five_second_boundary_ms != value.boundary_ms
            or value.boundary_ms - 5000 < value.first_held_boundary_ms
            or not value.price_valid
            or type(value.completed_five_second_close_int) is not int
            or value.completed_five_second_close_int <= 0
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (value.macd_line, value.macd_signal, value.bid, value.ask))
            or not 0 < value.bid <= value.ask
            or type(value.quote_age_us) is not int
            or not 0 <= value.quote_age_us <= 1_000_000):
        return None
    if value.boundary_ms - value.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS:
        return None
    threshold = ((3 * value.reference_ask + value.initial_stop) / 4
                 if value.first_held_boundary_ms < PREMARKET_END_MS
                 else (value.reference_ask + value.initial_stop) / 2)
    if not isfinite(threshold):
        raise ValueError('Quarter-risk failure threshold overflows')
    if (value.completed_five_second_close_int > threshold * 10_000
            or value.bid > threshold
            or value.macd_line >= value.macd_signal):
        return None
    return FollowThroughFailure(value.boundary_ms, value.first_held_boundary_ms,
        value.reference_ask, value.initial_stop, value.completed_five_second_close_int,
        float(value.macd_line), float(value.macd_signal), float(value.bid), float(value.ask),
        value.quote_age_us)
