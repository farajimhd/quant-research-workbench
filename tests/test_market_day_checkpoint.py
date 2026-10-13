"""Completed ClickHouse receipts replace producer disk-ledger dependencies."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from datetime import date
from threading import get_ident

import pytest

from pipelines.market_sip.events.market_day_checkpoint import (
    CompletedMarketDayLedger, MarketDayCheckpointWriter, UNIT, SEED,
    load_completed_market_day_ledger,
)
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient
from src.trading_runtime.arte_market_day_certification import prepare_market_day_certificate, verify_market_day_certificate
from test_arte_market_day_certification import BUILD, DAY, PIN, inventory, FakeReader
from test_arte_market_day_source_plan import plan as source_plan_fixture
from test_arte_market_day_publisher import FakeClickHouse, setup


class Reader(FakeClickHouse):
    def __init__(self):
        super().__init__()
        self.closed = False
        self.threads = []
    def execute(self, sql):
        self.threads.append(get_ident())
        if sql == "SELECT getSetting('readonly')":
            return '1'
        return super().execute(sql)
    def close(self):
        self.closed = True


def test_completed_checkpoint_assembles_native_certificate_without_disk():
    definition = dict(version='market-day-core-v5', plan=source_plan_fixture(),
        calculation_source=PIN, rules_hash=PIN)
    build = sha256(json.dumps(definition, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    receipts = inventory()
    stages = tuple({**row, 'build_id': build} for row in receipts[UNIT])
    seeds = tuple({**row, 'build_id': build} for row in receipts[SEED])
    ledger = CompletedMarketDayLedger(build, ((DAY, 'TEST'),), stages, seeds)
    prepared = prepare_market_day_certificate(definition, build, ledger)
    proof = verify_market_day_certificate(FakeReader(prepared), build, sessions=(DAY,))
    assert proof.scopes == ((DAY, 'TEST'),)
    assert all(row[4:6] == (0, '0') for row in proof.stages)
    copy = ledger.unit(build, DAY, 'TEST', 'bars')
    copy['output_rows'] = 100
    assert ledger.unit(build, DAY, 'TEST', 'bars')['output_rows'] == 0
    assert ledger.unit(build, date.fromisoformat(DAY), 'TEST', 'bars')['output_rows'] == 0
    assert set(ledger.unit(build, DAY, 'TEST', 'bars')) == {
        'status', 'attempt_id', 'source_hash', 'output_rows', 'output_hash'}


def test_missing_or_duplicate_receipts_never_become_a_completed_build():
    receipts = inventory()
    ledger = CompletedMarketDayLedger(BUILD, ((DAY, 'TEST'),), (), ())
    assert ledger.unit(BUILD, DAY, 'TEST', 'bars') is None
    assert ledger.seed(BUILD, DAY, 'TEST') is None
    with pytest.raises(ValueError, match='duplicate receipt'):
        CompletedMarketDayLedger(BUILD, ((DAY, 'TEST'),), tuple(receipts[UNIT]) * 2, ())
    with pytest.raises(ValueError, match='outside'):
        ledger.unit(BUILD, DAY, 'OTHER', 'bars')


def test_checkpoint_reads_are_owned_off_loop_and_closed():
    reader = Reader()
    receipts = inventory()
    reader.rows[UNIT], reader.rows[SEED] = receipts[UNIT], receipts[SEED]
    main = get_ident()
    ledger = asyncio.run(load_completed_market_day_ledger(lambda: reader, BUILD, ((DAY, 'TEST'),)))
    assert ledger.unit(BUILD, DAY, 'TEST', 'technical')['status'] == 'complete'
    assert reader.closed and all(thread != main for thread in reader.threads)
    assert not reader.inserts


def test_wrong_part_placement_blocks_checkpoint_reader():
    reader = Reader()
    reader.disk = 'default'
    reader.rows[UNIT] = inventory()[UNIT]
    with pytest.raises(RuntimeError):
        asyncio.run(load_completed_market_day_ledger(lambda: reader, BUILD, ((DAY, 'TEST'),)))
    assert reader.closed


@pytest.mark.parametrize('uncertain', [False, True])
def test_checkpoint_commit_reconciles_ack_and_skips_exact_retry(monkeypatch, uncertain):
    reader, _, keeper, claim = setup()
    row = inventory()[UNIT][0]
    calls = []
    def insert(client, database, name, columns, rows):
        assert database == 'arte' and name == UNIT
        calls.append(name)
        reader.rows[name].extend(deepcopy(rows))
        if uncertain:
            raise TimeoutError('Uncertain acknowledgement')
    monkeypatch.setattr('src.trading_runtime.arte_market_day_publisher.insert_json_each_row', insert)
    writer = MarketDayCheckpointWriter(reader, MarketDayCertificateClient(reader), keeper, claim, ((DAY, 'TEST'),))
    async def run():
        outcomes = await asyncio.gather(writer.commit(UNIT, row), writer.commit(UNIT, row))
        assert sorted(outcomes) == ['published_verified', 'skipped_verified']
    asyncio.run(run())
    assert calls == [UNIT] and reader.rows[UNIT] == [row]


def test_stale_owner_cannot_write_checkpoint(monkeypatch):
    reader, _, keeper, claim = setup()
    monkeypatch.setattr(keeper, 'current', lambda supplied: False)
    writer = MarketDayCheckpointWriter(reader, MarketDayCertificateClient(reader), keeper, claim, ((DAY, 'TEST'),))
    with pytest.raises(RuntimeError, match='ownership is stale'):
        asyncio.run(writer.commit(SEED, inventory()[SEED][0]))
    assert not reader.inserts and not reader.rows[SEED]
