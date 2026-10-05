from dataclasses import replace

import pytest

from src.trading_runtime.early_original_risk_failure import (
    EarlyOriginalRiskPolicy, early_original_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure


POLICY = EarlyOriginalRiskPolicy("ah-first-minute-quarter-original-risk@1", None, (1, 4))


def observation(**changes):
    return replace(FollowThroughFailureInput(
        43_230_000, 43_211_100, 10.01, 9.89, 43_230_000, 99_800,
        True, .01, .02, 9.98, 9.99, 100_000, 100, False,
    ), **changes)


def test_candidate_confirms_ah_quarter_loss_before_parent_half_risk():
    value = observation()
    assert zero_regime_risk_failure(value) is None
    witness = early_original_risk_failure(value, policy=POLICY)
    assert witness is not None
    assert witness.completed_close_int == 99_800
    assert witness.reference_ask == 10.01
    assert witness.initial_stop == 9.89


@pytest.mark.parametrize("close,bid,qualifies", [
    (99_800, 9.98, True), (99_801, 9.98, False), (99_800, 9.9801, False),
])
def test_literal_quarter_boundary_requires_both_completed_price_and_bid(close, bid, qualifies):
    value = observation(completed_five_second_close_int=close, bid=bid)
    assert (early_original_risk_failure(value, policy=POLICY) is not None) == qualifies


@pytest.mark.parametrize("changes", [
    {"pending_exit": True}, {"position_quantity": 0}, {"price_valid": False},
    {"quote_age_us": 1_000_001}, {"macd_line": None}, {"macd_line": .02},
    {"completed_five_second_boundary_ms": 43_225_000},
    {"completed_five_second_boundary_ms": 43_230_000.0},
    {"completed_five_second_close_int": None},
    {"first_held_boundary_ms": 43_228_000},
    {"boundary_ms": 43_230_100},
    {"first_held_boundary_ms": 31_100, "boundary_ms": 50_000,
     "completed_five_second_boundary_ms": 50_000},
    {"first_held_boundary_ms": 20_000_000},
])
def test_missing_stale_noncompleted_and_unselected_session_fail_closed(changes):
    assert early_original_risk_failure(observation(**changes), policy=POLICY) is None


def test_eligibility_ends_after_exact_first_minute_without_time_exit():
    at = 43_265_000
    value = observation(boundary_ms=at, completed_five_second_boundary_ms=at,
                        first_held_boundary_ms=at-60_000)
    assert early_original_risk_failure(value, policy=POLICY)
    assert early_original_risk_failure(replace(value, first_held_boundary_ms=at-60_100), policy=POLICY) is None
    assert early_original_risk_failure(observation(completed_five_second_close_int=100_000, bid=10.0), policy=POLICY) is None


@pytest.mark.parametrize("fraction", [(True, 4), (0, 4), (4, 4), (1, 10_001), [1, 4]])
def test_policy_rejects_ambiguous_fractions(fraction):
    with pytest.raises(ValueError):
        EarlyOriginalRiskPolicy("invalid", None, fraction)


def test_malformed_original_position_authority_raises():
    with pytest.raises(ValueError):
        early_original_risk_failure(observation(initial_stop=10.02), policy=POLICY)


def test_policy_is_generic_and_separately_selects_premarket():
    value = observation(first_held_boundary_ms=31_100, boundary_ms=50_000,
                        completed_five_second_boundary_ms=50_000)
    other = EarlyOriginalRiskPolicy("unrelated-profile-rule", (1, 4), None)
    assert early_original_risk_failure(value, policy=other) is not None
