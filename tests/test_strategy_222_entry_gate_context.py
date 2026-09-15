from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_entry_gate_context import frame_context


def context(opened=10., close=10., vwap=10., stamp='2026-08-21T13:31:07Z'):
    return frame_context(dict(open=opened, close=close, bar_end=stamp),
        dict(execution_vwap=vwap, bar_end=stamp),
        at=datetime.fromisoformat('2026-08-21T13:31:07+00:00').timestamp(),
        price=close, minimum_body_bps=5.)


def test_doji_and_vwap_equality_do_not_pass():
    result = context()
    assert not result['body_passed'] and not result['vwap_passed']
    assert result['body_bps'] == 0
    assert context(close=10.01)['body_passed']
    assert context(close=10.01)['vwap_passed']


@pytest.mark.parametrize('vwap', [None, float('nan'), 0., -1.])
def test_unavailable_vwap_is_explicit(vwap):
    result = context(vwap=vwap)
    assert not result['vwap_available'] and not result['vwap_passed']
    assert result['price_vs_vwap_pct'] is None


@pytest.mark.parametrize('stamp', ['2026-08-21T13:31:08Z', '2026-08-21T13:31:06Z', '2026-08-21T13:31:07'])
def test_rejects_future_stale_or_naive_frames(stamp):
    with pytest.raises(ValueError, match='exact completed'):
        context(stamp=stamp)


def test_rejects_frame_from_different_decision():
    with pytest.raises(ValueError, match='differs'):
        frame_context(dict(open=10., close=10., bar_end='2026-08-21T13:31:07Z'),
            dict(execution_vwap=9., bar_end='2026-08-21T13:31:07Z'),
            at=datetime.fromisoformat('2026-08-21T13:31:07+00:00').timestamp(),
            price=10.1, minimum_body_bps=5.)
