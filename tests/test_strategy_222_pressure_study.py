from datetime import timedelta

import pytest

from scripts.strategy_222_pressure_study import PressureTimeline
from tests.test_market_pressure import quote, trade, NOW


def test_snapshot_excludes_timestamp_tied_trade():
    timeline = PressureTimeline(NOW, NOW + timedelta(milliseconds=400))
    timeline.observe(quote(0))
    timeline.observe(trade(200, 100.1))
    rows = timeline.finish()
    assert not rows[0]['ready']
    assert rows[1]['fast']['trades'] == 0
    assert rows[2]['fast']['trades'] == 1


def test_future_events_cannot_rewrite_previous_pressure():
    full = PressureTimeline(NOW, NOW + timedelta(milliseconds=600))
    prefix = PressureTimeline(NOW, NOW + timedelta(milliseconds=200))
    events = [quote(0), trade(50, 100.1), quote(100, bs=150), trade(150, 100.)]
    for event in events:
        full.observe(event)
        prefix.observe(event)
    full.observe(quote(400, bid=50., ask=50.1))
    full.observe(trade(450, 50.))
    assert full.finish()[:2] == prefix.finish()


def test_gaps_expire_quotes_instead_of_inventing_pressure():
    timeline = PressureTimeline(NOW, NOW + timedelta(seconds=2))
    timeline.observe(quote(0))
    timeline.observe(trade(50, 100.1))
    rows = timeline.finish()
    assert rows[1]['ready']
    assert not rows[-1]['ready'] and not rows[-1]['usable']


def test_reversed_events_and_invalid_clock_fail():
    timeline = PressureTimeline(NOW, NOW + timedelta(seconds=1))
    timeline.observe(quote(100))
    with pytest.raises(ValueError, match='ordered'):
        timeline.observe(quote(50))
    with pytest.raises(ValueError, match='confirmation'):
        PressureTimeline(NOW, NOW, step_ms=500)
