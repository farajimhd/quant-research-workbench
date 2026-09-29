import polars as pl

from research.rl_trading.v1.bracket_oracle import labels


def test_hindsight_bracket_uses_sparse_path_and_fresh_observed_spread():
    positions = pl.DataFrame({
        'ticker': ['ABC'], 'episode_uid': ['ABC_1'],
        'entry_us': [4_000_000], 'exit_us': [7_000_000],
        'entry_price': [10.0], 'entry_fill_confirmed': [True],
    })
    bars = pl.DataFrame({
        'ticker': ['ABC'] * 8 + ['XYZ'],
        'time_us': [i * 1_000_000 for i in range(8)] + [5_000_000],
        'high': [11., 10.5, 10.4, 10.2, 10.1, 10.7, 11.4, 11.1, 999.],
        'low': [8., 9.6, 9.5, 9.8, 9.9, 9.7, 9.2, 9.4, 1.],
        'extremes_valid': [True] * 9,
    })
    quotes = pl.DataFrame({
        'ticker': ['ABC'], 'entry_us': [4_000_000],
        'quote_timestamp_us': [3_900_000], 'quote_valid': [True],
        'bid_int': [99_800], 'ask_int': [100_000],
        'bid_size': [100], 'ask_size': [100],
    })
    result = labels(positions, bars, quotes, tick_size=.01).row(0, named=True)
    assert result['pre_entry_low'] == 9.5
    assert result['active_min_low'] == 9.2
    assert result['active_max_high'] == 11.4
    assert round(result['quoted_spread'], 4) == .02
    assert round(result['oracle_stop'], 2) == 9.17
    assert round(result['oracle_target'], 2) == 11.4
    assert result['label_available']


def test_missing_or_stale_quote_never_becomes_zero_spread():
    position = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A_1'],
                             'entry_us': [2_000_000], 'exit_us': [4_000_000],
                             'entry_price': [10.], 'entry_fill_confirmed': [True]})
    bars = pl.DataFrame({'ticker': ['A'] * 4,
                         'time_us': [1_000_000, 2_000_000, 3_000_000, 4_000_000],
                         'high': [10., 10., 11., 10.],
                         'low': [9., 9.5, 9.4, 9.7],
                         'extremes_valid': [True] * 4})
    quotes = pl.DataFrame({'ticker': ['A'], 'entry_us': [2_000_000],
                           'quote_timestamp_us': [500_000], 'quote_valid': [True],
                           'bid_int': [99_000], 'ask_int': [100_000],
                           'bid_size': [10], 'ask_size': [10]})
    row = labels(position, bars, quotes, tick_size=.01).row(0, named=True)
    assert not row['label_available']
    assert row['oracle_stop'] is None
