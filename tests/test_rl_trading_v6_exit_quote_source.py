from datetime import date

import polars as pl
import pytest

from research.rl_trading.v6 import exit_quote_source as source


def test_exit_quote_source_pages_only_observed_later_buckets(monkeypatch):
    day = date(2026, 8, 5)
    origin = source._midnight_us(day)
    monkeypatch.setattr(source, 'broker_attempts',
                        lambda *args: {'ABC': 'attempt'})
    monkeypatch.setattr(source, 'assert_liquidity_storage', lambda _: None)
    queries = []
    def fake_frame(_, query, schema):
        queries.append(query)
        rows = [{
            'bucket_index': bucket,
            'quote_timestamp_us': origin+bucket*100_000+10_000,
            'quote_valid': 1, 'bid_int': 99900, 'ask_int': 100000,
            'bid_size': 50., 'ask_size': 60., 'event_count': 1,
            'last_event_us': origin+bucket*100_000+20_000}
            for bucket in ([21, 23] if len(queries) == 1 else [24])]
        return pl.DataFrame(rows, schema=schema)
    monkeypatch.setattr(source, 'frame', fake_frame)
    quotes = list(source.iter_exit_quotes(None, {'build_id': 'build'}, None,
        day, 'ABC', decision_us=origin+2_000_000,
        end_us=origin+2_600_000, page_rows=2))
    assert len(quotes) == 3
    assert all(quote.fresh() for quote in quotes)
    assert quotes[0].bucket_end_us == origin+2_200_000
    assert 'bucket_index>19' in queries[0]
    assert 'bucket_index>23' in queries[1]
    assert len(queries) == 2


def test_exit_quote_source_rejects_unbounded_or_reverse_window():
    day = date(2026, 8, 5)
    origin = source._midnight_us(day)
    with pytest.raises(ValueError, match='boundary'):
        list(source.iter_exit_quotes(None, {}, None, day, 'ABC',
            decision_us=origin+1_000_000,
            end_us=origin+1_000_000))
