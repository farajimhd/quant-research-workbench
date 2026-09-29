"""Pinned, projected one-second ARTE read contract."""
from datetime import date

import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v6 import source as v6_source


def test_candle_queries_select_required_columns_only(monkeypatch):
    day = date(2026, 8, 3)
    attempt = '11111111-1111-1111-1111-111111111111'
    source = {'build_id': 'build', 'units': {str(day): {'ABC': {
        'bars': {'attempt_id': attempt},
        'technical': {'attempt_id': attempt},
    }}}}
    statements = []

    def fake_frame(client, statement, schema):
        arte_sql._approved(statement)
        statements.append(statement)
        return pl.DataFrame(schema=schema)

    monkeypatch.setattr(v6_source, 'frame', fake_frame)
    bars, indicators = v6_source.read_candles(object(), source, day, 'ABC')
    assert bars.is_empty() and indicators.is_empty()
    assert 'AND resolution_ms=1000' in statements[0]
    assert 'arte.bars_v1' in statements[0]
    assert 'arte.indicators_v1' in statements[1]
    assert 'liquidity' not in ' '.join(statements)
    v6_source.read_previous_volume(object(), source, day, 'ABC')
    assert statements[2].startswith('SELECT bucket_index,volume')
