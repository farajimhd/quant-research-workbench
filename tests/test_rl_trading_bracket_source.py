from datetime import date, datetime, timezone
import sqlite3
from zoneinfo import ZoneInfo

import polars as pl

from research.rl_trading.v1.bracket_source import (
    _bucket_ids, entry_quotes, select_entry_quotes,
)


def test_fetch_window_is_only_ten_completed_liquidity_buckets():
    day = date(2026, 8, 18)
    clock = int(datetime(2026, 8, 18, 9, 31,
                         tzinfo=ZoneInfo('America/New_York'))
                .astimezone(timezone.utc).timestamp() * 1_000_000)
    indexes = _bucket_ids(day, [clock])
    assert len(indexes) == 10
    assert indexes[-1] == 9 * 36000 + 31 * 600 - 1


def test_select_quotes_uses_latest_valid_completed_bucket_only():
    positions = pl.DataFrame({'ticker': ['A', 'B'],
                              'entry_us': [2_000_000, 2_000_000]})
    candidates = pl.DataFrame({
        'ticker': ['A', 'A', 'A', 'B'],
        'boundary_us': [1_800_000, 1_900_000, 2_100_000, 1_900_000],
        'quote_timestamp_us': [1_700_000, 1_850_000, 2_050_000, 1_850_000],
        'quote_valid': [1, 0, 1, 1],
        'bid_int': [99_000] * 4, 'ask_int': [100_000] * 4,
        'bid_size': [10., 10., 10., 0.],
        'ask_size': [10.] * 4,
        'event_count': [1] * 4,
        'last_event_us': [1_750_000, 1_850_000, 2_050_000, 1_850_000],
    })
    selected = select_entry_quotes(positions, candidates).sort('ticker')
    assert selected['quote_timestamp_us'].to_list() == [1_700_000, None]


def test_sparse_pinned_reader_projects_only_needed_quote_fields(tmp_path):
    day = date(2026, 8, 18)
    clock = int(datetime(2026, 8, 18, 9, 31,
                         tzinfo=ZoneInfo('America/New_York'))
                .astimezone(timezone.utc).timestamp() * 1_000_000)
    ledger = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE units '
                   '(build_id TEXT, session_date TEXT, ticker TEXT, '
                   'stage TEXT, attempt_id TEXT, status TEXT)')
        db.execute('INSERT INTO units VALUES (?,?,?,?,?,?)',
                   ('build', str(day), 'A', 'broker_100ms',
                    '00000000-0000-0000-0000-000000000001', 'complete'))

    class Reader:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if 'system.tables' in statement:
                return '{"name":"liquidity_100ms_v1","storage_policy":"live_market_ssd"}\n'
            if 'system.parts' in statement:
                return '{"disk_name":"live_market_ssd","rows":10}\n'
            assert 'FROM arte.liquidity_100ms_v1' in statement
            assert '(ticker,attempt_id,bucket_index) IN (' in statement
            assert 'execution_price_levels' not in statement
            return ('ticker,boundary_us,quote_timestamp_us,quote_valid,bid_int,ask_int,'
                    'bid_size,ask_size,event_count,last_event_us\n'
                    f'A,{clock},{clock-50_000},1,99900,100000,100,100,1,{clock-50_000}\n')

    reader = Reader()
    source = {'build_id': 'build', 'units': {str(day): {'A': {}}}}
    positions = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A_1'],
                              'entry_us': [clock]})
    result = entry_quotes(reader, source, ledger, day, positions)
    assert result['quote_timestamp_us'].to_list() == [clock-50_000]
    assert len(reader.statements) == 3
