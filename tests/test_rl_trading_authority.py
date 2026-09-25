"""The relocated RL workflow must not gain ClickHouse write or event authority."""
import pytest

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.build_phase2 import phase1_plan
from src.market_engine.level_book_store import write
from research.rl_trading.v1.common import digest


@pytest.mark.parametrize('statement', [
    'SELECT count() FROM arte.bars_v1',
    'SELECT macd_line FROM arte.indicators_v1',
    'SELECT ticker FROM q_live.feature_tradable_universe_snapshot_v2',
    "SELECT disk_name FROM system.parts WHERE database='arte'",
])
def test_research_reader_admits_only_certified_reads(statement):
    assert arte_sql._approved(statement) == statement


@pytest.mark.parametrize('statement', [
    'INSERT INTO arte.bars_v1 VALUES (1)',
    'CREATE TABLE arte.bad (x Int32)',
    'SELECT * FROM market_sip_compact.events_2026',
    'SELECT * FROM q_live.market_stock_split_v1',
    'SELECT * FROM arte.bars_v1; DROP TABLE arte.bars_v1',
    'SELECT * FROM arte.bars_v1 INTO OUTFILE \'x\'',
    'SELECT * FROM arte.bars_v1 SETTINGS readonly=0',
    "SELECT * FROM system.parts WHERE database='q_live'",
])
def test_research_reader_rejects_writes_and_unapproved_sources(statement):
    with pytest.raises(ValueError):
        arte_sql._approved(statement)


def test_research_reader_sets_clickhouse_readonly(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def execute(self, statement):
            return statement

        def close(self):
            pass

    monkeypatch.setattr(arte_sql, 'ClickHouseHttpClient', Client)
    monkeypatch.setattr(arte_sql, 'default_clickhouse_url', lambda: 'http://localhost:8123')
    monkeypatch.setattr(arte_sql, 'default_clickhouse_user', lambda: 'test')
    monkeypatch.setattr(arte_sql, 'default_clickhouse_password', lambda: '')
    reader = arte_sql.ArteReader()
    assert seen['default_query_params']['readonly'] == 1
    assert reader.execute('SELECT count() FROM arte.bars_v1').startswith('SELECT')
    with pytest.raises(ValueError):
        reader.execute('INSERT INTO arte.bars_v1 VALUES (1)')


def test_rl_phase2_rejects_legacy_phase1(tmp_path):
    plan = dict(version='hindsight-phase1-macd-v1', date='2026-08-21', selected=[{'ticker': 'A'}])
    plan['plan_hash'] = digest(plan)
    write(tmp_path / 'plan.json', plan)
    write(tmp_path / 'complete.json', dict(plan_hash=plan['plan_hash'], listing_count=1, rows=57601))
    with pytest.raises(ValueError, match='arte price-action'):
        phase1_plan(tmp_path)
