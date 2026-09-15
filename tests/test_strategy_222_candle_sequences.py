from copy import deepcopy

import pytest

from scripts.strategy_222_candle_sequences import CandleSequences
from src.market_engine.structural_detector import StructuralDetector


def candles(seconds=1):
    detector = StructuralDetector()
    return [detector.observe(dict(time=i*seconds, end=(i+1)*seconds,
        open=10., close=9.9, high=10.5, low=9.85)) for i in range(12)]


@pytest.mark.parametrize('seconds', [1, 2, 5])
def test_real_detector_labels_are_counted_in_candle_units(seconds):
    sequence = CandleSequences(candle_seconds=seconds)
    for row in candles(seconds):
        result = sequence.observe(row, session='session', observed_at=row['effective_at'])
    f = result['features']
    assert f['candles_5.red_upper_tail_run'] == 5
    assert f['candles_5.label_fraction.geometry:upper_tail'] == 1
    assert f['candles_5.signed_close_efficiency'] == 0
    assert result['evidence']['complete_windows'] == [3, 5, 10]
    assert result['evidence']['available_candles'] == 10


def test_future_suffix_cannot_change_published_prefix():
    rows = candles()
    first = CandleSequences(candle_seconds=1)
    outputs = [first.observe(row, session='session', observed_at=row['effective_at']) for row in rows[:5]]
    frozen = deepcopy(outputs)
    for row in rows[5:]:
        first.observe(row, session='session', observed_at=row['effective_at'])
    assert outputs == frozen
    second = CandleSequences(candle_seconds=1)
    repeated = [second.observe(row, session='session', observed_at=row['effective_at']) for row in rows[:5]]
    assert repeated == frozen


@pytest.mark.parametrize('cause', ['gap', 'session'])
def test_missing_history_is_not_zero_pattern_evidence(cause):
    rows = candles()
    sequence = CandleSequences(candle_seconds=1)
    for row in rows[:5]:
        sequence.observe(row, session='a', observed_at=row['effective_at'])
    row = rows[6] if cause == 'gap' else rows[5]
    result = sequence.observe(row, session='b' if cause == 'session' else 'a', observed_at=row['effective_at'])
    assert result['features'] == {}
    assert result['evidence']['available_candles'] == 1
    assert result['evidence']['reset']


def test_future_wrong_timeframe_and_duplicate_rows_rejected():
    row = candles()[0]
    sequence = CandleSequences(candle_seconds=1)
    with pytest.raises(ValueError, match='Incomplete'):
        sequence.observe(row, session='a', observed_at=0.)
    with pytest.raises(ValueError, match='timeframe'):
        CandleSequences(candle_seconds=2).observe(row, session='a', observed_at=1.)
    sequence.observe(row, session='a', observed_at=1.)
    with pytest.raises(ValueError, match='Duplicate'):
        sequence.observe(row, session='a', observed_at=1.)
