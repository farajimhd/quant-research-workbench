from copy import deepcopy

import pytest

from src.market_engine.swing_pivot_witness import capture, event_times


def level():
    return dict(level_id=7, side='support', scale='local', price=3.88,
        pivot_at=1, confirmed_at=2, lower=3.87, upper=3.89)


def test_new_witness_preserves_anchor_and_prior_witness():
    anchor = level()
    frozen = deepcopy(anchor)
    first = capture(anchor, (3.87, 10, .04), 11)
    old = deepcopy(first)
    second = capture(anchor, (3.885, 15, .04), 16)
    assert anchor == frozen
    assert first == old
    assert first['confirmed_at'] == 11
    assert second['confirmed_at'] == 16
    assert second['level_id'] == anchor['level_id']


@pytest.mark.parametrize('reason', ['boundary_retest_confirmed', 'retest_contact', 'role_reversal'])
def test_touch_or_role_change_is_not_a_new_directional_pivot(reason):
    assert capture(level(), (3.87, 10, None), 11, reason) is None


@pytest.mark.parametrize('extreme,at', [((3.87, 11, .04), 11), ((3.87, 12, .04), 11),
    ((3.87, 10, 0), 11), ((float('nan'), 10, .04), 11), ((3.87, 10, .04), float('inf'))])
def test_noncausal_or_invalid_witness_is_rejected(extreme, at):
    with pytest.raises(ValueError):
        capture(level(), extreme, at)


def test_explicit_clock_conversion_preserves_raw_sequence_witness():
    witness = capture(level(), (3.87, 10, .04), 11)
    converted = event_times(witness, {10:1000., 11:1001.})
    assert converted['pivot_at'] == 1000.
    assert converted['confirmed_at'] == 1001.
    assert converted['clock'] == 'event_time'
    assert witness['clock'] == 'candle_sequence'
    assert witness['pivot_at'] == 10
    with pytest.raises(ValueError):
        event_times(converted, {1000.:2000., 1001.:2001.})


@pytest.mark.parametrize('times', [{}, {10:1000.}, {10:1001., 11:1000.}, {10:1000., 11:float('nan')}])
def test_clock_conversion_never_invents_missing_timestamps(times):
    with pytest.raises(ValueError):
        event_times(capture(level(), (3.87, 10, .04), 11), times)
