"""Eligibility clock boundaries and inherited causal failure safeguards."""
from dataclasses import replace

import pytest

from src.trading_runtime.strategy_early_followthrough_failure import early_followthrough_failure
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput, followthrough_failure


def evidence():
    return FollowThroughFailureInput(
        90_000, 30_000, 10., 9., 90_000, 94_500,
        True, .01, .02, 9.44, 9.46, 1_000_000, 100., False,
    )


def test_exact_first_minute_boundary_is_eligible():
    value = evidence()
    assert early_followthrough_failure(value) == followthrough_failure(value)
    assert early_followthrough_failure(value) is not None


def test_one_hundred_ms_beyond_window_does_not_exit_but_original_still_does():
    value = replace(evidence(), first_held_boundary_ms=29_900)
    assert followthrough_failure(value) is not None
    assert early_followthrough_failure(value) is None


def test_later_pullback_is_ineligible_and_old_release_keeps_its_rule():
    value = replace(evidence(), boundary_ms=285_000,
                    completed_five_second_boundary_ms=285_000)
    assert followthrough_failure(value) is not None
    assert early_followthrough_failure(value) is None


@pytest.mark.parametrize('updates', [
    {'bid': 9.51, 'ask': 9.52}, {'macd_line': .03},
    {'quote_age_us': 1_000_001}, {'position_quantity': 0.},
    {'pending_exit': True}, {'completed_five_second_boundary_ms': 95_000},
    {'first_held_boundary_ms': 85_100},
])
def test_elapsed_time_cannot_replace_price_momentum_or_whole_completed_bucket(updates):
    assert early_followthrough_failure(replace(evidence(), **updates)) is None


def test_out_of_window_corrupt_authority_is_not_silently_skipped():
    with pytest.raises(ValueError, match='causal position'):
        early_followthrough_failure(replace(evidence(), boundary_ms=True))


def test_future_observations_do_not_change_early_prefix():
    early = evidence()
    later = replace(early, boundary_ms=95_000, completed_five_second_boundary_ms=95_000,
                    completed_five_second_close_int=110_000, bid=11., ask=11.01)
    prefix = (early_followthrough_failure(early),)
    assert tuple(map(early_followthrough_failure, (early, later)))[:1] == prefix
