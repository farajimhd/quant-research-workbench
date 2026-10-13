"""Technical-stage orchestration uses native immutable parent/receipt contracts."""
import asyncio
from dataclasses import replace
from datetime import date
from threading import get_ident

import pytest

from pipelines.market_sip.events.current_market_day_technical import build_current_market_day_technical
from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger, MarketDayCheckpointWriter, UNIT, SEED
from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient
from scripts import build_market_day as legacy
from test_market_day_predecessor import supplied, TARGET
from test_arte_market_day_keeper import FakeKeeper
from test_arte_market_day_publisher import FakeClickHouse
from test_arte_market_day_certification import BUILD, DAY


@pytest.mark.parametrize('mode', ['new', 'technical_only', 'skipped', 'missing_parent', 'orphan'])
def test_current_technical_stage_recovers_seed_without_reinserting_output(monkeypatch, mode):
    original, definition, parent = supplied()
    build = original.build_id
    bar_row = dict(build_id=build, session_date=TARGET, ticker='TEST', stage='bars',
        attempt_id='bar-attempt', source_hash='bars-source', output_rows=8, output_hash='42')
    current = CompletedMarketDayLedger(build, original.scopes, (bar_row,), ())
    bar = current.unit(build, TARGET, 'TEST', 'bars')
    dependency = legacy.digest([[dict(session_date=TARGET, **bar)], None, legacy.sql.VERSION])
    technical_row = {**bar_row, 'stage': 'technical', 'attempt_id': 'technical-attempt', 'source_hash': dependency}
    seed_row = dict(build_id=build, session_date=TARGET, ticker='TEST', attempt_id='technical-attempt',
        mode=0, predecessor_date=DAY, prior_build_id='', prior_state_hash='')
    if mode in ('technical_only', 'skipped'):
        current = CompletedMarketDayLedger(build, original.scopes, (bar_row, technical_row),
            (seed_row,) if mode == 'skipped' else ())
    registry = MarketDayPredecessorLedger(current, definition, () if mode == 'missing_parent' else (parent,))
    keeper = MarketDayKeeperAuthority(FakeKeeper())
    claim = keeper.acquire(build, 'technical-worker')
    reader = FakeClickHouse()
    writer = MarketDayCheckpointWriter(reader, MarketDayCertificateClient(reader), keeper, claim, current.scopes)
    phases, threads = [], []
    main = get_ident()
    class Client:
        closed = False
        def query(self, query, phase, *args):
            threads.append(get_ident())
            phases.append(phase)
            if phase == 'uncheckpointed_technical': return [dict(n=int(mode == 'orphan'))]
            return []  # The fully audited parent is an explicit zero-row scope.
        def close(self): self.closed = True
    client = Client()
    monkeypatch.setattr(legacy, 'storage_preflight', lambda *args, **kwargs: None)
    def evidence(client, database, stage, selected_build, *args):
        return dict(n=0, unique_keys=0, hash=0) if selected_build == BUILD else dict(n=8, unique_keys=8, hash=42)
    monkeypatch.setattr(legacy, 'evidence', evidence)
    monkeypatch.setattr(legacy, 'validate_technical', lambda *args: dict(n=8, unique_keys=8, hash=42))
    monkeypatch.setattr('src.trading_runtime.arte_market_day_publisher.insert_json_each_row',
        lambda client, database, name, columns, rows: reader.rows[name].extend(rows))
    monkeypatch.setattr('sqlite3.connect', lambda *args, **kwargs: pytest.fail('Opened SQLite'))
    pending = build_current_market_day_technical(lambda: client, writer, registry,
        day=date.fromisoformat(TARGET), ticker='TEST')
    if mode in ('missing_parent', 'orphan'):
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(pending)
        assert not reader.rows[UNIT] and not reader.rows[SEED]
    else:
        result = asyncio.run(pending)
        assert not result['consumer_ready']
        assert len(reader.rows[UNIT]) == int(mode == 'new')
        assert len(reader.rows[SEED]) == int(mode != 'skipped')
        assert ('technical' in phases) == (mode == 'new')
        if reader.rows[SEED]:
            assert reader.rows[SEED][0]['predecessor_date'] == DAY
            assert reader.rows[SEED][0]['mode'] == 0
    assert client.closed
    assert all(thread != main for thread in threads)
