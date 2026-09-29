from datetime import date

import polars as pl

from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.entry_source import (_keys, attach_quotes,
                                                 with_decision_fallback)


def test_sparse_first_arrival_quote_retains_missing_as_explicit_unavailable():
    day = date(2026, 8, 5)
    origin, _ = bounds(day)
    decisions = pl.DataFrame({'ticker': ['ABC', 'XYZ'],
        'time_us': [origin+1_000_000, origin+1_000_000],
        'episode_uid': ['a', 'x']})
    keys = _keys(day, decisions, {'ABC': 'id1', 'XYZ': 'id2'})
    assert keys['bucket_index'].to_list() == [144010, 144010]
    quotes = pl.DataFrame({'ticker': ['ABC'], 'bucket_index': [144010],
        'quote_timestamp_us': [origin+1_030_000], 'quote_valid': [1],
        'bid_int': [99900], 'ask_int': [100000],
        'bid_size': [20.], 'ask_size': [30.],
        'event_count': [1], 'last_event_us': [origin+1_050_000]})
    joined = attach_quotes(keys, quotes, day)
    assert joined['quote_available'].to_list() == [True, False]
    assert joined['arrival_bucket_end_us'][0] == origin+1_100_000
    assert joined['quote_timestamp_us'][1] is None


def test_causal_decision_quote_is_used_only_if_fresh_at_arrival():
    day = date(2026, 8, 5)
    origin, _ = bounds(day)
    decisions = pl.DataFrame({'ticker': ['ABC', 'XYZ'],
        'time_us': [origin+1_000_000, origin+1_000_000],
        'episode_uid': ['a', 'x']})
    keys = _keys(day, decisions, {'ABC': 'a', 'XYZ': 'x'})
    empty = pl.DataFrame(schema={'ticker': pl.String, 'bucket_index': pl.Int64,
        'quote_timestamp_us': pl.Int64, 'quote_valid': pl.Int64,
        'bid_int': pl.Int64, 'ask_int': pl.Int64,
        'bid_size': pl.Float64, 'ask_size': pl.Float64,
        'event_count': pl.Int64, 'last_event_us': pl.Int64})
    arrivals = attach_quotes(keys, empty, day)
    prior = pl.DataFrame({'ticker': ['ABC', 'XYZ'],
        'entry_us': [origin+1_000_000, origin+1_000_000],
        'quote_timestamp_us': [origin+900_000, origin],
        'quote_valid': [1, 1], 'bid_int': [99000, 99000],
        'ask_int': [100000, 100000], 'bid_size': [10., 10.],
        'ask_size': [20., 20.]})
    result = with_decision_fallback(arrivals, prior)
    assert result['quote_available'].to_list() == [True, False]
    assert result['quote_source'].to_list() == [
        'carried_decision_quote', 'unavailable']
    assert result['ask_int'][0] == 100000
