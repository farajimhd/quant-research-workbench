from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from strategy_222_verify_cache_map import prepared_identity


def cache(close=10.):
    connection = sqlite3.connect(':memory:')
    connection.execute('create table strategy_frames(ticker text,timeframe text,sequence integer,as_of_us integer,bar_json text)')
    bar = dict(sym='X', timeframe='1s', bar_end='2026-01-02T09:00:01+00:00', open=10., high=11., low=9., close=close, volume=100.)
    stamp = int(datetime.fromisoformat(bar['bar_end']).timestamp() * 1e6)
    connection.execute('insert into strategy_frames values(?,?,?,?,?)', ('X', '1s', 1, stamp, json.dumps(bar)))
    return connection


def test_same_source_headers_do_not_prove_equal_prepared_bar_contents():
    a = cache(); b = cache(10.1)
    try:
        x, count = prepared_identity(a, 'X', '2026-01-02')
        y, other_count = prepared_identity(b, 'X', '2026-01-02')
        assert count == other_count == 1 and x != y
        assert prepared_identity(a, 'X', '2026-01-02') == (x, count)
    finally: a.close(); b.close()


def test_duplicate_or_nonfinite_bars_cannot_match_authority():
    a = cache(); b = cache(float('nan'))
    try:
        a.execute('insert into strategy_frames select ticker,timeframe,2,as_of_us,bar_json from strategy_frames')
        with pytest.raises(ValueError, match='unordered'): prepared_identity(a, 'X', '2026-01-02')
        with pytest.raises(ValueError, match='Nonfinite'): prepared_identity(b, 'X', '2026-01-02')
    finally: a.close(); b.close()
