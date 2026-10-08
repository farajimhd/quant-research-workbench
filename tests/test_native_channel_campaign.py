"""Actual publisher/reader functions with controlled persistent storage and Keeper."""
from dataclasses import replace
from hashlib import sha256
import json
import re

import pyarrow as pa
import pytest

from pipelines.market_sip.events import native_causal_channel_producer as producer
from pipelines.market_sip.events import native_channel_campaign as campaign
from src.backend import backtest_native_channel_store as store
from src.market_engine.native_causal_channel_contract import (
    FEATURE_TABLE, COVERAGE_TABLE, FEATURE_SCHEMA, issue_source_plan, table_hash,
)
from src.market_engine.native_channel_insert_authority import NativeChannelInsertAuthority
from src.market_engine.completed_return_insert_authority import InsertAuthorityUnavailable
from tests.test_native_causal_channel_producer import request, raw, FEATURE_ATTEMPT, Client
from test_completed_return_campaign import FakeKeeper, FixtureTerminalResolver


class PersistentClient(Client):
    def __init__(self):
        super().__init__(raw())
        self.tables = {t: pa.Table.from_batches([], schema=s) for t, s in store.TABLE_SCHEMAS.items()}
        self.catalog = set(self.tables)
        self.disks = ['live_market_ssd']
        self.wrong_policy = self.wrong_parts = self.wrong_columns = False
        self.writes, self.terminal_queries = [], {}
        self.partial = None

    def execute(self, sql, *, query_id=None):
        if type(sql) is bytes:
            header, payload = sql.split(b'\n', 1)
            table = re.search(r'INSERT INTO (\S+)', header.decode()).group(1)
            rows = pa.ipc.open_stream(payload).read_all()
            self.writes.append((table, rows.num_rows))
            assert query_id
            self.terminal_queries[query_id] = sha256(sql).hexdigest()
            if self.partial == table:
                self.tables[table] = pa.concat_tables([self.tables[table], rows.slice(0, 1)])
                self.partial = None
                raise TimeoutError('Controlled uncertain original INSERT')
            self.tables[table] = pa.concat_tables([self.tables[table], rows])
            return ''
        if sql.startswith('CREATE TABLE'):
            self.catalog.add(re.search(r'EXISTS (\S+)', sql).group(1))
            self.writes.append(('DDL', 0))
            return ''
        assert 'SETTINGS' not in sql
        if 'system.storage_policies' in sql:
            rows = [dict(disks=self.disks)]
        elif 'system.tables' in sql:
            rows = [dict(name=t.split('.')[1], storage_policy='default' if self.wrong_policy else 'live_market_ssd') for t in self.catalog]
        elif 'system.parts' in sql:
            rows = [dict(table=FEATURE_TABLE.split('.')[1], disk_name='default')] if self.wrong_parts else []
        elif 'system.columns' in sql:
            rows = [dict(table=t.split('.')[1], name=n, type=kind) for t, s in store.TABLE_SCHEMAS.items()
                    if t in self.catalog for n, kind in store.column_types(s).items()]
            if self.wrong_columns:
                rows[0]['type'] = 'UInt8'
        else:
            raise AssertionError(sql)
        return '\n'.join(json.dumps(row) for row in rows)

    def iter_arrow_record_batches(self, sql):
        if 'FROM arte.bars_v1 ' in sql:
            return super().iter_arrow_record_batches(sql)
        table = re.search(r'FROM (\S+)', sql).group(1)
        self.sql.append(sql)
        assert 'SETTINGS' not in sql and 'feature_attempt_id=toUUID(' in sql
        return iter(self.tables[table].to_batches(max_chunksize=3))


@pytest.fixture
def unit(monkeypatch):
    calls = []
    def verified(m, client):
        calls.append(m.token)
    for module in (producer, campaign, store):
        monkeypatch.setattr(module, 'verify_market_day_plan', verified)
    client = PersistentClient()
    authority = NativeChannelInsertAuthority(FakeKeeper())
    packet = producer.produce_native_channels(request(), FEATURE_ATTEMPT, client)
    return client, authority, packet, calls


def test_publish_read_only_load_and_exact_repeat(unit):
    client, authority, packet, calls = unit
    assert campaign.publish_packet(client, packet, authority=authority) == dict(
        status='published', inserted_feature_rows=12, inserted_coverage_rows=2)
    assert [t for t, _ in client.writes] == [FEATURE_TABLE, COVERAGE_TABLE]
    loaded = store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority)
    assert loaded.token == packet.token and table_hash(loaded.rows) == table_hash(packet.rows)
    assert len(calls) >= 3
    assert campaign.publish_packet(client, packet, authority=authority)['status'] == 'skipped'
    assert len(client.writes) == 2


def test_unknown_insert_blocks_until_original_terminal_proof_then_exact_missing_resume(unit):
    client, authority, packet, _ = unit
    client.partial = FEATURE_TABLE
    with pytest.raises(TimeoutError):
        campaign.publish_packet(client, packet, authority=authority)
    assert client.tables[FEATURE_TABLE].num_rows == 1
    for operation in (lambda: campaign.publish_packet(client, packet, authority=authority),
                      lambda: store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority)):
        with pytest.raises(InsertAuthorityUnavailable):
            operation()
    assert len(client.writes) == 1
    authority.resolve_original_terminal(FEATURE_ATTEMPT, FixtureTerminalResolver(client))
    result = campaign.publish_packet(client, packet, authority=authority)
    assert result['inserted_feature_rows'] == 11 and result['inserted_coverage_rows'] == 2
    assert store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority).token == packet.token


@pytest.mark.parametrize('problem', ['wrong_policy', 'wrong_parts', 'wrong_columns'])
def test_storage_failure_prevents_any_publication(unit, problem):
    client, authority, packet, _ = unit
    setattr(client, problem, True)
    with pytest.raises(ValueError):
        campaign.publish_packet(client, packet, authority=authority)
    assert client.writes == []


def test_no_adoption_of_unregistered_rows_and_no_foreign_authority(unit):
    client, authority, packet, _ = unit
    client.tables[FEATURE_TABLE] = packet.rows.slice(0, 1)
    with pytest.raises(InsertAuthorityUnavailable, match='Unregistered'):
        campaign.publish_packet(client, packet, authority=authority)
    with pytest.raises(ValueError, match='ownership'):
        campaign.publish_packet(client, packet, authority=object())
    assert client.writes == []


def test_completed_fence_does_not_hide_mutated_duplicate_or_missing_rows(unit):
    client, authority, packet, _ = unit
    campaign.publish_packet(client, packet, authority=authority)
    client.tables[FEATURE_TABLE] = pa.concat_tables([packet.rows, packet.rows.slice(0, 1)])
    with pytest.raises(ValueError, match='row bound'):
        store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority)
    client.tables[FEATURE_TABLE] = packet.rows.slice(0, 11)
    with pytest.raises(ValueError):
        store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority)
    with pytest.raises(ValueError, match='preceded'):
        campaign.publish_packet(client, packet, authority=authority)
    assert len(client.writes) == 2


def test_install_requires_ssd_and_checks_schema_after_creation(unit):
    client, _, _, _ = unit
    client.catalog.clear()
    campaign.install(client)
    assert len(client.writes) == 2
    client.disks = ['default']
    with pytest.raises(ValueError, match='SSD'):
        campaign.install(client)


def test_foreign_product_dispatch_rejected_under_native_namespace(unit):
    client, authority, _, _ = unit
    with authority.ownership(FEATURE_ATTEMPT) as lease:
        lease.admit_existing(False)
        with pytest.raises(InsertAuthorityUnavailable, match='Foreign product'):
            lease.execute(client, 'arte.completed_endpoint_return_v1', b'foreign', lambda: None)
    assert client.writes == []


def test_unknown_coverage_insert_also_blocks_and_resumes_exactly(unit):
    client, authority, packet, _ = unit
    client.partial = COVERAGE_TABLE
    with pytest.raises(TimeoutError):
        campaign.publish_packet(client, packet, authority=authority)
    assert client.tables[FEATURE_TABLE].num_rows == 12
    assert client.tables[COVERAGE_TABLE].num_rows == 1
    with pytest.raises(InsertAuthorityUnavailable):
        campaign.publish_packet(client, packet, authority=authority)
    authority.resolve_original_terminal(FEATURE_ATTEMPT, FixtureTerminalResolver(client))
    result = campaign.publish_packet(client, packet, authority=authority)
    assert result['inserted_feature_rows'] == 0 and result['inserted_coverage_rows'] == 1
    assert store.read_installed_native_channels(client, issue_source_plan(packet), authority=authority).token == packet.token


def test_source_plan_boolean_and_foreign_context_cannot_issue_read_admission(unit):
    client, authority, packet, _ = unit
    with pytest.raises(ValueError, match='Typed native'):
        store.read_installed_native_channels(client, True, authority=authority)
    with pytest.raises(ValueError, match='Typed native'):
        store.read_installed_native_channels(client, issue_source_plan(packet), authority=object())
    assert client.writes == []
