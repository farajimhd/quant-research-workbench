"""Strategy 29 candidate: retain early failure, then test half original risk.

This pure rule consumes the existing completed producer observation contract.
Registration, immutable publication and native financial replay remain separate
gates. It has no order, portfolio, clock, indicator or storage authority.
"""
from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS
from .strategy_followthrough_failure import (
    FollowThroughFailure, FollowThroughFailureInput, followthrough_failure,
)
from .strategy_premarket_quarter_risk_failure import premarket_quarter_risk_failure

POLICY_ID = "strategy-twenty-nine-persistent-original-risk-failure-v1"


def persistent_risk_policy_payload() -> dict:
    """Expose both age regimes and unchanged causal eligibility requirements."""
    return {
        "policy_id": POLICY_ID,
        "first_minute": "unchanged_parent28_quarter_original_risk_PM_half_original_risk_AH",
        "first_minute_ms": EARLY_FAILURE_WINDOW_MS,
        "later_threshold": "(original_reference_ask + original_initial_stop) / 2",
        "later_eligibility": "all_later_completed_5s_boundaries_while_held",
        "age_origin": "first_completed_broker_bucket_containing_held_quantity",
        "conditions": "completed_5s_bucket_wholly_after_first_held; negative_producer_macd; valid_close_and_fresh_bid_at_or_below_threshold",
        "quote_max_age_us": 1_000_000,
        "pending_exit": "no_duplicate_exit",
        "missing": "no_synthetic_observations",
        "time_alone": "never_exits",
    }


def persistent_risk_failure(value: FollowThroughFailureInput) -> FollowThroughFailure | None:
    """Keep parent eligibility through 60s; half risk remains eligible later.

    Calling the parent first preserves exact malformed-input rejection and
    the first-minute boundary semantics. Both branches use the same typed
    witness, completed 5s MACD, original proposal risk and fresh current bid.
    No accumulated observations, additional queries or synthetic bars exist.
    """
    early = premarket_quarter_risk_failure(value)
    if early is not None:
        return early
    if value.boundary_ms - value.first_held_boundary_ms <= EARLY_FAILURE_WINDOW_MS:
        return None
    return followthrough_failure(value)
