"""Strategy 30 candidate: retain early failure and confirm late MACD regime.

Uses the existing completed observation and witness contracts. This module
has no clock, market reader, indicator, portfolio, storage or order authority.
Immutable numbered release integration and publication are separate gates.
"""
from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS
from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput
from .strategy_persistent_risk_failure import persistent_risk_failure

POLICY_ID = 'strategy-thirty-zero-regime-original-risk-failure-v1'


def zero_regime_risk_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'first_minute': 'unchanged_parent29_quarter_original_risk_PM_half_original_risk_AH',
        'first_minute_ms': EARLY_FAILURE_WINDOW_MS,
        'later_threshold': '(original_reference_ask + original_initial_stop) / 2',
        'later_momentum': 'completed_5s_macd_line < completed_5s_macd_signal < 0',
        'later_eligibility': 'all_later_completed_5s_boundaries_while_held',
        'age_origin': 'first_completed_broker_bucket_containing_held_quantity',
        'conditions': 'completed_5s_bucket_wholly_after_first_held; valid_close_and_fresh_bid_at_or_below_threshold',
        'quote_max_age_us': 1_000_000,
        'pending_exit': 'no_duplicate_exit',
        'missing': 'no_synthetic_observations',
        'time_alone': 'never_exits',
    }


def zero_regime_risk_failure(value: FollowThroughFailureInput) -> FollowThroughFailure | None:
    """Parent first minute, then half original risk in a negative MACD regime.

    Parent validation owns original proposal risk, wholly completed bars,
    finite negative MACD histogram, fresh bid and pending-exit controls.
    The additional late condition uses the same producer signal: since the
    parent already requires line < signal, signal < 0 puts both below zero.
    No previous bars, derived observations or accumulated state are added.
    """
    witness = persistent_risk_failure(value)
    if witness is None:
        return None
    if witness.boundary_ms - witness.first_held_boundary_ms <= EARLY_FAILURE_WINDOW_MS:
        return witness
    return witness if witness.macd_signal < 0 else None
