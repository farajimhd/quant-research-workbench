from datetime import date

import polars as pl

from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.entry_source import _keys, attach_quotes


def test_sparse_first_arrival_quote_retains_missing_as_explicit_unavailable():
    day = date(2026, 8, 5)
    origin, _ = bounds(day)
    decisions = pl.DataFrame({'ticker': ['ABC', 'XYZ'],
        'time_us': [origin+1_000_000, origin+1_000_000],
        'episode_uid': ['a', 'x']})
    keys = _keys(day, decisions, {'ABC': 'id1', 'XYZ': 'id2'})
    quotes = pl.DataFrame({'ticker': ['ABC'], 'bucket_index': [10],
        'quote_timestamp_us': [origin+1_030_000], 'quote_valid': [1],
        'bid_int': [99900], 'ask_int': [100000],
        'bid_size': [20.], 'ask_size': [30.],
        'event_count': [1], 'last_event_us': [origin+1_050_000]})
    joined = attach_quotes(keys, quotes, day)
    assert joined['quote_available'].to_list() == [True, False]
    assert joined['arrival_bucket_end_us'][0] == origin+1_100_000
    assert joined['quote_timestamp_us'][1] is None
