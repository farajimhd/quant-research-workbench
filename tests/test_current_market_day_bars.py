"""Current canonical bar orchestration, without legacy controller persistence."""
import asyncio
from threading import get_ident

import pytest

from pipelines.market_sip.events import market_day_sql as sql
from pipelines.market_sip.events.current_market_day_bars import build_current_market_day_bars
from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger, MarketDayCheckpointWriter, UNIT
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient
from scripts import build_market_day as legacy
from test_arte_market_day_publisher import setup
from test_arte_market_day_certification import BUILD, DAY


@pytest.mark.parametrize('mode', ['new', 'skipped', 'source_changed', 'orphan'])
def test_current_worker_reuses_sql_checks_and_only_commits_verified_receipts(monkeypatch, mode):
    reader, _, keeper, claim = setup()
    rules = [dict(token_id=1, modifier_int=0, update_last=1, update_high_low=1, update_volume=1)]
    source = dict(n=10, session_events=10, reporting_delayed_trades=0)
    source_hash = legacy.digest([source, rules, sql.VERSION])
    receipts = tuple(dict(build_id=BUILD, session_date=DAY, ticker='TEST', stage=stage,
        attempt_id='previous', source_hash=source_hash, output_rows=8, output_hash='42')
        for stage in ('bars', 'broker_100ms')) if mode == 'skipped' else ()
    ledger = CompletedMarketDayLedger(BUILD, ((DAY, 'TEST'),), receipts, ())
    writer = MarketDayCheckpointWriter(reader, MarketDayCertificateClient(reader), keeper, claim, ((DAY, 'TEST'),))
    stages, source_reads, threads = [], [], []
    main = get_ident()
    class Client:
        closed = False
        def query(self, query, phase, *args):
            threads.append(get_ident())
            stages.append((phase, query))
            if phase.startswith('uncheckpointed_'):
                return [dict(n=int(mode == 'orphan'))]
            return []
        def close(self):
            self.closed = True
    client = Client()
    def read_source(*args):
        threads.append(get_ident())
        source_reads.append(1)
        return {**source, 'n': 11} if mode == 'source_changed' and len(source_reads) > 1 else source
    monkeypatch.setattr(legacy, 'storage_preflight', lambda *args, **kwargs: stages.append(('storage', '')))
    monkeypatch.setattr(legacy, 'source_evidence', read_source)
    monkeypatch.setattr(legacy, 'evidence', lambda *args: dict(n=8, unique_keys=8, hash=42))
    monkeypatch.setattr(legacy, 'validate_bars', lambda *args: stages.append(('verify_bars', '')))
    monkeypatch.setattr(legacy, 'validate_broker', lambda *args: stages.append(('verify_broker', '')))
    def insert(client, database, name, columns, rows):
        reader.rows[name].extend(rows)
    monkeypatch.setattr('src.trading_runtime.arte_market_day_publisher.insert_json_each_row', insert)
    def forbidden(*args, **kwargs):
        pytest.fail('Current producer opened a SQLite journal')
    monkeypatch.setattr('sqlite3.connect', forbidden)
    coroutine = build_current_market_day_bars(lambda: client, writer, ledger,
        build_id=BUILD, source_unit=dict(source_date=DAY, ticker='TEST'), rules=rules)
    if mode in ('source_changed', 'orphan'):
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(coroutine)
        assert reader.rows[UNIT] == []
    else:
        result = asyncio.run(coroutine)
        assert not result['consumer_ready']
        assert result['status'] == ('skipped_verified' if mode == 'skipped' else 'completed_bar_stages')
        if mode == 'new':
            phases = [phase for phase, _ in stages]
            assert phases == ['storage', 'uncheckpointed_bars', 'uncheckpointed_broker_100ms',
                'broker_100ms', '100ms', 'fine_rollup', 'higher_rollup', 'verify_bars', 'verify_broker']
            assert len(reader.rows[UNIT]) == 2
            broker_query = next(query for phase, query in stages if phase == 'broker_100ms')
            assert 'market_sip_compact' in broker_query and 'file(' not in broker_query
        else:
            assert not reader.rows[UNIT]
    assert client.closed and threads and all(thread != main for thread in threads)
