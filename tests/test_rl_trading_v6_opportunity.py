"""Vectorized ticker-local episodes and sparse teacher candidates."""
from datetime import date

import polars as pl
import pytest

from research.rl_trading.v6.opportunity import compile_ticker


def test_candidate_funnel_and_minimum_hold():
    buckets = list(range(14400, 14410))
    closes = [10000, 9900, 10000, 10500, 11000,
              12000, 13000, 12500, 12000, 11900]
    bars = pl.DataFrame({'resolution_ms': [1000] * len(buckets),
                         'bucket_index': buckets, 'close_int': closes,
                         'price_valid': [1] * len(buckets)})
    indicators = pl.DataFrame({'bucket_index': buckets,
                               'macd_line': [-1., -1.] + [1.] * 7 + [-1.],
                               'macd_signal': [0.] * len(buckets)})
    episodes, candidates, report = compile_ticker(
        date(2026, 8, 3), 'TEST', 'listing-1', bars, indicators)
    assert report['intervals'] == 2  # Final MACD state persists to session end.
    assert report['episodes'] == 1
    assert episodes['entry_hint_close'][0] == pytest.approx(.99)
    assert episodes['exit_hint_close'][0] == pytest.approx(1.3)
    assert candidates.height > 0
    assert candidates['hold_seconds'].min() >= 3
    assert candidates['score'].min() >= .01
    assert candidates['episode_uid'].n_unique() == 1
    assert candidates['time_us'].n_unique() == candidates.height
