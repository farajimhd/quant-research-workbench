"""Contract checks for actual-candle V6 features."""
from datetime import date

import numpy as np
import polars as pl

from research.rl_trading.v6 import features


class _Stream:
    def __init__(self, seed, **kwargs):
        self.engine = type('Engine', (), {'rows': []})()
        self.last = None

    def update_second(self, row, *, at):
        self.last = at

    def context(self, *, as_of, price):
        return {'v7_max_input_timestamp': (self.last or as_of).timestamp(),
                'qmd_structure_unified_levels': []}


def test_actual_candles_preserve_gaps_and_previous_session_rvol(monkeypatch):
    monkeypatch.setattr(features, 'FixedV7Stream', _Stream)
    day = date(2026, 8, 3)
    bars = pl.DataFrame({
        'resolution_ms': [1000, 1000], 'bucket_index': [14400, 14402],
        'open_int': [10000, 11000], 'high_int': [10500, 11500],
        'low_int': [9900, 10900], 'close_int': [10100, 11100],
        'notional': [201., 222.], 'volume': [200., 200.],
        'trade_count': [2, 3], 'price_valid': [1, 1],
        'extremes_valid': [1, 1],
    })
    prior = pl.DataFrame({'bucket_index': [14400, 14402],
                          'volume': [100., 100.]})
    indicators = pl.DataFrame({
        'bucket_index': [14400, 14402], 'macd_line': [0.01, 0.02],
        'macd_signal': [0.005, 0.01], 'rsi_14': [50., 60.],
        'atr_14': [0.1, 0.2], 'ema_7': [1.0, 1.1],
        'ema_26': [1.0, 1.1],
    })
    refs = dict.fromkeys(features.FUNDAMENTAL_NAMES, 0.)
    result = features.encode(day, bars, indicators, prior, {'ticker': 'TEST'},
                             [], refs)
    assert len(features.SCALAR_NAMES) == 37
    assert result.scalar.shape == (2, 37)
    assert result.levels.shape == (2, 2, 5, 11)
    assert np.diff(result.close_us).tolist() == [2_000_000]
    rvol = features.SCALAR_NAMES.index('log_rvol_10s_prev_session')
    np.testing.assert_allclose(result.scalar[:, rvol], np.log1p(2.))
    gap = features.SCALAR_NAMES.index('log_inter_candle_gap')
    np.testing.assert_allclose(result.scalar[1, gap], np.log1p(2.))


def test_five_nearest_levels_per_side_with_origin_and_today_count():
    now = 1_800_000_000_000_000
    levels = []
    rows = []
    for offset in (-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6):
        center = 10 + offset * 0.1
        identity = str(offset)
        levels.append({'unified_level_id': identity, 'price': center,
                       'lower': center - 0.01, 'upper': center + 0.01,
                       'confirmed_at_ms': now // 1000 - 1000,
                       'role': 'support' if offset < 0 else 'resistance',
                       'observation_count': 3, 'historical': offset == -1})
        rows.append({'id': identity, 'observations': [
            {'session': '2026-08-03'}, {'session': '2026-08-01'}]})
    result = features._level_slots(levels, 10., now, '2026-08-03', rows)
    np.testing.assert_allclose(result[:, :, -1], 1.)
    np.testing.assert_allclose(result[0, :, 0], [-.01, -.02, -.03, -.04, -.05])
    np.testing.assert_allclose(result[1, :, 0], [.01, .02, .03, .04, .05])
    np.testing.assert_allclose(result[:, :, 4], np.log1p(1))
    assert result[0, 0, 9] == 1.
