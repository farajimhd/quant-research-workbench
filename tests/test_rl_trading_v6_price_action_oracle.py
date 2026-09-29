import polars as pl
import pytest

from research.rl_trading.v6.price_action_oracle import labels


def _ticks():
    return pl.DataFrame({'ticker': ['ABC'], 'tick_size': [.01]})


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
    result = labels(positions, bars, _ticks())
    row = result.row(0,named=True)
    assert row['clock_complete'] and row['label_available']
    assert row['oracle_stop'] == pytest.approx(9.49)
    assert row['oracle_target'] == pytest.approx(10.4)
    assert 'quoted_spread' not in result.columns


def test_missing_trade_second_is_reported_without_inventing_a_price():
    positions = pl.DataFrame({'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.]})
    bars = pl.DataFrame({'ticker': ['ABC']*5,
        'time_us': [1_000_000,2_000_000,3_000_000,
                    5_000_000,7_000_000],
        'high': [10.,10.,10.,10.2,10.3], 'low': [9.]*5,
        'extremes_valid': [1]*5})
    result = labels(positions, bars, _ticks())
    assert not result['clock_complete'][0]
    assert result['unobserved_held_seconds'][0] == 1
    assert result['label_available'][0]
    assert result['oracle_stop'][0] == pytest.approx(8.99)


def test_no_preentry_trade_bar_uses_only_observed_held_low():
    positions = pl.DataFrame({'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.]})
    bars = pl.DataFrame({'ticker': ['ABC'], 'time_us': [6_000_000],
        'high': [10.4], 'low': [9.7], 'extremes_valid': [1]})
    row = labels(positions, bars, _ticks()).row(0,named=True)
    assert row['pre_entry_bars'] is None
    assert row['unobserved_held_seconds'] == 2
    assert row['oracle_stop'] == pytest.approx(9.69)
    assert row['label_available']


def test_oracle_requires_exact_per_listing_tick_map():
    positions = pl.DataFrame({'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.]})
    bars = pl.DataFrame({'ticker': ['ABC'], 'time_us': [6_000_000],
        'high': [10.4], 'low': [9.7], 'extremes_valid': [1]})
    with pytest.raises(ValueError, match='tick authority'):
        labels(positions, bars,
               pl.DataFrame({'ticker': ['XYZ'], 'tick_size': [.01]}))
