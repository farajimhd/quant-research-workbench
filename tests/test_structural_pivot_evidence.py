"""Native completed-candle pivot evidence and checkpoint continuity."""
from copy import deepcopy
import json

import pytest

from src.market_engine.structural_detector import StructuralDetector
from src.market_engine.structural_detector_checkpoint import checkpoint, restore


def bars():
    prices = [10., 9.9, 9.8, 9.9, 10., 10.1, 10.2, 10.1] * 12
    return [dict(time=1000+i, end=1001+i, open=p-.01, close=p,
                 low=p-.02, high=p+.02) for i,p in enumerate(prices)]


def test_native_merged_witness_is_available_at_confirmation_without_retiming_anchor():
    engine = StructuralDetector()
    rows = [engine.observe(b) for b in bars()]
    merged = [(row,l) for row in rows for l in row['pivot_swings']
              if l['fresh_pivot']['confirmed_at'] > l['confirmed_at']]
    assert merged
    assert any(l['fresh_pivot']['confirmed_at'] == row['effective_at'] for row,l in merged)
    for row,l in merged:
        witness = l['fresh_pivot']
        assert witness['clock'] == 'event_time'
        assert witness['level_id'] == l['level_id']
        assert witness['side'] == l['side']
        assert witness['pivot_at'] < witness['confirmed_at'] <= row['effective_at']
    frozen = deepcopy(rows)
    engine.observe(dict(time=1096,end=1097,open=10.,close=10.1,low=9.99,high=10.11))
    assert rows == frozen
    assert all('fresh_pivot' not in l for row in rows for l in row['local_swings']+row['confirmed_swings'])


@pytest.mark.parametrize('compact', [False, True])
def test_native_pivot_checkpoint_keeps_exact_future_rows(compact):
    data = bars()
    engine = StructuralDetector()
    for bar in data[:40]: engine.observe(bar)
    assert engine.swings.latest_pivots
    saved = json.loads(json.dumps(checkpoint(engine, compact=compact), allow_nan=False))
    resumed = restore(saved)
    assert resumed.swings.latest_pivots == engine.swings.latest_pivots
    assert [resumed.observe(b) for b in data[40:]] == [engine.observe(b) for b in data[40:]]


def test_previous_detector_checkpoint_is_not_silently_promoted():
    saved = checkpoint(StructuralDetector())
    saved['contract'] = 'structural-candle-detector-10'
    with pytest.raises(ValueError, match='version mismatch'):
        restore(saved)
