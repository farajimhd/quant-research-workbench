"""Research predicate boundaries; not native publication or portfolio replay."""
from dataclasses import replace

import pytest

from src.trading_runtime.strategy_confirmed_ah_risk_failure import (
    ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure


def observed_case():
    # WAFU Aug10 AH: committed original ask/stop and first-held authority;
    # completed producer candles and 48us-old native quote at 16:08:20 NY.
    five = FollowThroughFailureInput(
        43_700_000, 43_647_500, 2.08, 1.81, 43_700_000, 19_700, True,
        0.01971676900139796, 0.028269653367226016,
        1.96, 1.97, 48, 1.0, False,
    )
    return ConfirmedAhRiskFailureInput(
        five, 43_700_000, True, 0.05043722423951946, 0.05238791450068928,
    )


def test_observed_failure_retains_both_producer_timeframes_and_original_risk():
    x = observed_case()
    assert zero_regime_risk_failure(x.five_second) is None
    witness = confirmed_ah_risk_failure(x)
    assert witness.five_second.reference_ask == 2.08
    assert witness.five_second.initial_stop == 1.81
    assert witness.five_second.first_held_boundary_ms == 43_647_500
    assert witness.completed_ten_second_boundary_ms == 43_700_000
    assert witness.ten_second_macd_line == x.ten_second_macd_line


@pytest.mark.parametrize('changes', [
    {'completed_ten_second_boundary_ms': None},
    {'completed_ten_second_boundary_ms': 43_710_000},
    {'completed_ten_second_boundary_ms': 43_690_000},
    {'completed_ten_second_boundary_ms': True},
    {'ten_second_price_valid': False},
    {'ten_second_macd_line': None},
    {'ten_second_macd_line': float('nan')},
    {'ten_second_macd_line': 0.06},
])
def test_missing_forming_stale_or_nonbearish_confirmation_rejects(changes):
    assert confirmed_ah_risk_failure(replace(observed_case(), **changes)) is None


@pytest.mark.parametrize('changes', [
    {'pending_exit': True}, {'position_quantity': 0}, {'quote_age_us': 1_000_001},
    {'bid': 2.02, 'ask': 2.03}, {'completed_five_second_close_int': 20_126},
    {'macd_line': 0.03}, {'first_held_boundary_ms': 43_639_900},
    {'first_held_boundary_ms': 43_695_000}, {'price_valid': False},
])
def test_position_quote_price_and_whole_held_authority_rejects(changes):
    x = observed_case()
    assert confirmed_ah_risk_failure(replace(x, five_second=replace(x.five_second, **changes))) is None


def test_premarket_excluded_and_inclusive_minute_with_prior_ten_candle():
    x = observed_case()
    pm = replace(x.five_second, boundary_ms=18_700_000,
                 completed_five_second_boundary_ms=18_700_000,
                 first_held_boundary_ms=18_647_500)
    assert confirmed_ah_risk_failure(replace(x, five_second=pm,
                                           completed_ten_second_boundary_ms=18_700_000)) is None
    edge = replace(x.five_second, first_held_boundary_ms=43_640_000)
    assert confirmed_ah_risk_failure(replace(x, five_second=edge)) is not None
    assert confirmed_ah_risk_failure(replace(x, completed_ten_second_boundary_ms=43_700_000,
        five_second=replace(edge, boundary_ms=43_705_000,
                            completed_five_second_boundary_ms=43_705_000))) is None


def test_original_position_authority_and_exact_types_fail_closed():
    with pytest.raises(ValueError):
        confirmed_ah_risk_failure(replace(observed_case(), ten_second_price_valid=1))
    x = observed_case()
    with pytest.raises(ValueError):
        confirmed_ah_risk_failure(replace(x, five_second=replace(x.five_second, initial_stop=2.09)))
