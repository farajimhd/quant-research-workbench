import polars as pl
import pytest

from research.rl_trading.v6.price_action_oracle import labels


def test_price_action_oracle_uses_only_complete_candle_path():
    positions = pl.DataFrame({'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.]})
    bars = pl.DataFrame({'ticker': ['ABC']*6,
        'time_us': [1_000_000,2_000_000,3_000_000,
                    5_000_000,6_000_000,7_000_000],
        'high': [9.9,10.,10.,10.2,10.4,10.3],
        'low': [9.5,9.6,9.8,9.7,9.9,9.8],
        'extremes_valid': [1]*6})
    result = labels(positions, bars, tick_size=.01)
    row = result.row(0,named=True)
    assert row['path_complete'] and row['label_available']
    assert row['oracle_stop'] == pytest.approx(9.49)
    assert row['oracle_target'] == pytest.approx(10.4)
    assert 'quoted_spread' not in result.columns


def test_missing_held_second_withholds_perfect_stop_label():
    positions = pl.DataFrame({'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.]})
    bars = pl.DataFrame({'ticker': ['ABC']*5,
        'time_us': [1_000_000,2_000_000,3_000_000,
                    5_000_000,7_000_000],
        'high': [10.]*5, 'low': [9.]*5,
        'extremes_valid': [1]*5})
    result = labels(positions, bars, tick_size=.01)
    assert not result['path_complete'][0]
    assert not result['label_available'][0]
    assert result['oracle_stop'][0] is None
