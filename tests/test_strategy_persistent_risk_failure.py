"""Causal boundary and recovery controls for the unregistered 29 candidate."""
from dataclasses import replace
import pytest

from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_premarket_quarter_risk_failure import premarket_quarter_risk_failure
from src.trading_runtime.strategy_persistent_risk_failure import (
    persistent_risk_failure, persistent_risk_policy_payload,
)


def observation(**changes):
    value = FollowThroughFailureInput(
        boundary_ms=165_000, first_held_boundary_ms=100_000,
        reference_ask=10.0, initial_stop=8.0,
        completed_five_second_boundary_ms=165_000,
        completed_five_second_close_int=90_000, price_valid=True,
        macd_line=-0.2, macd_signal=-0.1,
        bid=9.0, ask=9.01, quote_age_us=100_000,
        position_quantity=100.0, pending_exit=False,
    )
    return replace(value, **changes)


def test_late_failure_requires_price_momentum_and_fresh_quote():
    value = observation()
    assert premarket_quarter_risk_failure(value) is None
    witness = persistent_risk_failure(value)
    assert witness is not None
    assert witness.reference_ask == 10.0 and witness.initial_stop == 8.0
    assert witness.completed_close_int == 90_000 and witness.bid == 9.0


@pytest.mark.parametrize('changes', [
    {'completed_five_second_close_int': 90_001},
    {'bid': 9.0001}, {'macd_line': -0.1}, {'macd_line': None},
    {'quote_age_us': 1_000_001}, {'quote_age_us': -1},
    {'bid': None}, {'ask': 8.9}, {'price_valid': False},
    {'completed_five_second_boundary_ms': 160_000},
    {'pending_exit': True}, {'position_quantity': 0},
    {'boundary_ms': 165_100},
])
def test_elapsed_time_does_not_create_an_exit(changes):
    assert persistent_risk_failure(observation(**changes)) is None


@pytest.mark.parametrize('held', [100_000, 43_200_000])
@pytest.mark.parametrize('age', [5_000, 55_000, 60_000])
@pytest.mark.parametrize('close', [89_000, 94_000, 96_000])
def test_first_minute_exactly_preserves_parent(held, age, close):
    boundary = held + age
    value = observation(boundary_ms=boundary, first_held_boundary_ms=held,
                        completed_five_second_boundary_ms=boundary,
                        completed_five_second_close_int=close, bid=close / 10_000)
    assert persistent_risk_failure(value) == premarket_quarter_risk_failure(value)


def test_late_regime_uses_half_risk_instead_of_extending_quarter_risk():
    assert persistent_risk_failure(observation(completed_five_second_close_int=94_000, bid=9.4)) is None
    assert persistent_risk_failure(observation()) is not None


def test_afterhours_late_failure_has_same_original_risk_authority():
    value = observation(boundary_ms=43_365_000, first_held_boundary_ms=43_300_000,
                        completed_five_second_boundary_ms=43_365_000)
    assert persistent_risk_failure(value) is not None


def test_profitable_yj_liquidity_fade_does_not_satisfy_original_loss_rule():
    value = observation(reference_ask=3.73, initial_stop=3.25,
                        completed_five_second_close_int=56_500, bid=5.65, ask=5.66,
                        macd_line=0.09431566, macd_signal=0.13031417)
    assert persistent_risk_failure(value) is None


def test_current_five_second_bucket_must_be_wholly_after_first_held():
    value = observation(first_held_boundary_ms=163_000)
    assert persistent_risk_failure(value) is None


@pytest.mark.parametrize('value', [None, {}, observation(reference_ask=8), observation(first_held_boundary_ms=0)])
def test_malformed_position_authority_still_fails_closed(value):
    with pytest.raises(ValueError):
        persistent_risk_failure(value)


def test_payload_declares_candidate_age_and_missing_evidence_semantics():
    policy = persistent_risk_policy_payload()
    assert policy['first_minute_ms'] == 60_000
    assert policy['time_alone'] == 'never_exits'
    assert policy['missing'] == 'no_synthetic_observations'
