"""Current finalization reads shared receipts and reuses the native final fence."""
import asyncio
from copy import deepcopy
from threading import get_ident

import pytest

from pipelines.market_sip.events.current_market_day_publication import publish_current_market_day_build
from pipelines.market_sip.events.market_day_checkpoint import MarketDayCheckpointWriter, UNIT, SEED
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient
from src.trading_runtime.arte_market_day_certification import _typed_readback_row
from scripts.build_market_day import digest
from test_arte_market_day_certification import DAY, PIN, inventory
from test_arte_market_day_source_plan import plan as source_plan_fixture
from test_arte_market_day_keeper import FakeKeeper
from test_arte_market_day_publisher import FakeClickHouse


@pytest.mark.parametrize('missing', [False, True])
def test_final_publication_requires_complete_receipts_before_source_client(monkeypatch, missing):
    definition = dict(version='market-day-core-v5', calculation_source=PIN,
        rules_hash=PIN, plan=source_plan_fixture())
    build = digest(definition)
    store = FakeClickHouse()
    supplied = inventory()
    store.rows[UNIT] = [{**row, 'build_id': build} for row in supplied[UNIT]]
    store.rows[SEED] = [{**row, 'build_id': build} for row in supplied[SEED]]
    if missing: store.rows[UNIT] = store.rows[UNIT][1:]
    keeper = MarketDayKeeperAuthority(FakeKeeper())
    claim = keeper.acquire(build, 'current-producer')
    writer = MarketDayCheckpointWriter(store, MarketDayCertificateClient(store), keeper, claim, ((DAY, 'TEST'),))
    closed, threads, opened = [], [], []
    main = get_ident()
    class Reader:
        def execute(self, query):
            threads.append(get_ident())
            return '1' if query == "SELECT getSetting('readonly')" else store.execute(query)
        def close(self): closed.append('checkpoint')
    class Source:
        def close(self): closed.append('source')
    def source_factory():
        opened.append('source')
        return Source()
    monkeypatch.setattr('scripts.build_market_day.source_plan', lambda *args: deepcopy(definition['plan']))
    def insert(client, database, name, columns, rows):
        assert database == 'arte'
        store.insert_typed_rows(name, [_typed_readback_row(name, row) for row in rows])
    monkeypatch.setattr('src.trading_runtime.arte_market_day_publisher.insert_json_each_row', insert)
    monkeypatch.setattr('sqlite3.connect', lambda *args, **kwargs: pytest.fail('Opened SQLite'))
    pending = publish_current_market_day_build(definition, writer,
        checkpoint_reader_factory=Reader, source_client_factory=source_factory)
    if missing:
        with pytest.raises(ValueError, match='lacks completed'):
            asyncio.run(pending)
        assert not opened and not store.rows['market_day_build_fence_v1']
        assert keeper.load(build) is None
    else:
        proof = asyncio.run(pending)
        assert proof == keeper.load(build)
        assert len(store.rows['market_day_build_fence_v1']) == 1
        assert closed == ['checkpoint', 'source']
    assert threads and all(thread != main for thread in threads)
