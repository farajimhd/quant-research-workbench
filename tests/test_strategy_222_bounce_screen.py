import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_bounce_screen import BounceWindow


def candle(at, open_, close, *, rejection=False, session='session', reset=False):
    return dict(at=at, session=session,
        candle=dict(time=at-1, end=at, open=open_, close=close,
            high=max(open_, close)+.01, low=min(open_, close)-.01),
        evidence=dict(through=at, observed_at=at, candle_seconds=1, reset=reset),
        labels=dict(interaction=['local:support_rejection'] if rejection else []))


def test_completed_rejection_followthrough_uses_only_observed_low():
    window = BounceWindow()
    assert window.observe(candle(1, 4., 3.9)) is None
    assert window.observe(candle(2, 3.9, 4., rejection=True)) is None
    result = window.observe(candle(3, 4., 4.1))
    assert result == dict(stop=3.89, two_green=True, rising_close=True,
        support_rejection=True, candle_ends=[1, 2, 3])
    frozen = copy.deepcopy(result)
    window.observe(candle(4, 4.1, 3.))
    assert result == frozen


@pytest.mark.parametrize('change', ['gap', 'session', 'reset'])
def test_missing_contiguous_history_is_not_imputed(change):
    window = BounceWindow()
    window.observe(candle(1, 4., 3.9))
    window.observe(candle(2, 3.9, 4., rejection=True))
    row = candle(4 if change == 'gap' else 3, 4., 4.1,
        session='next' if change == 'session' else 'session', reset=change == 'reset')
    assert window.observe(row) is None


def test_downward_resistance_reclaim_is_not_bullish_support_rejection():
    window = BounceWindow()
    window.observe(candle(1, 4., 3.9))
    row = candle(2, 3.9, 4.)
    row['labels']['interaction'] = ['local:resistance_reclaim']
    window.observe(row)
    assert window.observe(candle(3, 4., 4.1))['support_rejection'] is False


@pytest.mark.parametrize('change', ['duplicate', 'future', 'nan', 'geometry'])
def test_malformed_clock_or_bar_is_rejected(change):
    window = BounceWindow()
    window.observe(candle(1, 4., 3.9))
    row = candle(1 if change == 'duplicate' else 2, 3.9, 4.)
    if change == 'future': row['evidence']['observed_at'] = 1
    if change == 'nan': row['candle']['low'] = float('nan')
    if change == 'geometry': row['candle']['high'] = 3.
    with pytest.raises(ValueError): window.observe(row)
