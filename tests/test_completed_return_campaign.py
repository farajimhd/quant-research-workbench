"""Typed persistent-store fixtures: partial writes, restart and installed authority."""
from dataclasses import replace
from hashlib import sha256
import json
import re
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pytest

from test_completed_endpoint_return_foundation import Client, bar, plan
from pipelines.market_sip.events import completed_endpoint_return_producer as producer
from pipelines.market_sip.events import completed_return_campaign as campaign
from src.backend import backtest_certified_completed_returns as installed
from src.backend import backtest_completed_endpoint_returns as native
from src.backend import backtest_market_data as market_data
from src.backend import backtest_strategy_one_candidate_store as candidates
from src.backend import backtest_completed_return_campaign_store as store
from src.market_engine import completed_return_campaign_contract as contract
from src.market_engine.completed_return_insert_authority import (
    KeeperProductInsertAuthority, InsertAuthorityUnavailable, TerminalInsertResolver,
    TerminalInsertResolution, ROOT as INSERT_ROOT,
)
from src.market_engine.completed_endpoint_return_contract import (
    FEATURE_TABLE, COVERAGE_TABLE, FEATURE_SCHEMA, table_hash, producer_implementation_hash,
)



class FakeKeeper:
    connected = True
    def __init__(self): self.nodes = {}
    def ensure_path(self, path): pass
    def create(self, path, value, ephemeral=False):
        if path in self.nodes: raise RuntimeError('NodeExists')
        self.nodes[path] = (value, 0)
    def get(self, path):
        value, version = self.nodes[path]
        return value, SimpleNamespace(version=version)
    def set(self, path, value, version):
        if self.nodes[path][1] != version: raise RuntimeError('BadVersion')
        self.nodes[path] = (value, version + 1)
    def delete(self, path, version):
        if self.nodes[path][1] != version: raise RuntimeError('BadVersion')
        del self.nodes[path]


class FixtureTerminalResolver(TerminalInsertResolver):
    def __init__(self, client): self.client = client
    def verify_original_terminal(self, attempt, query_id, payload_hash):
        # Test-only original server completion registry; never infer from rows.
        if self.client.terminal_queries.get(query_id) != payload_hash:
            raise InsertAuthorityUnavailable('Original dispatch still in flight')
        return TerminalInsertResolution(query_id, payload_hash,
            sha256(('server-terminal:' + query_id + payload_hash).encode()).hexdigest())


class PersistentClient(Client):
    def __init__(self, bars=()):
        super().__init__(bars)
        self.tables = {name: pa.Table.from_batches([], schema=schema) for name, schema in store.TABLE_SCHEMAS.items()}
        self.catalog = set(self.tables)
        self.disks = ['live_market_ssd']
        self.wrong_policy = self.wrong_parts = self.wrong_columns = False
        self.writes = []
        self.partial = None
        self.terminal_queries = {}
        self.authority = KeeperProductInsertAuthority(FakeKeeper())

    def execute(self, sql, *, query_id=None):
        if isinstance(sql, bytes):
            header, body = sql.split(b'\n', 1)
            table = re.search(r'INSERT INTO (\S+)', header.decode()).group(1)
            rows = pa.ipc.open_stream(body).read_all()
            self.writes.append((table, rows.num_rows))
            self.terminal_queries[query_id] = sha256(sql).hexdigest()
            if self.partial == table:
                self.tables[table] = pa.concat_tables([self.tables[table], rows.slice(0, 1)])
                self.partial = None
                raise TimeoutError('uncertain insert, stop original handle')
            self.tables[table] = pa.concat_tables([self.tables[table], rows])
            return ''
        if sql.startswith('CREATE TABLE'):
            self.writes.append(('DDL', 0))
            self.catalog.add(re.search(r'EXISTS (\S+)', sql).group(1))
            return ''
        if 'system.storage_policies' in sql:
            rows = [dict(disks=self.disks)]
        elif 'system.tables' in sql:
            rows = [dict(name=t.split('.')[1], storage_policy='default' if self.wrong_policy else 'live_market_ssd')
                    for t in self.catalog]
        elif 'system.parts' in sql:
            rows = [dict(table='completed_endpoint_return_v1', disk_name='default')] if self.wrong_parts else []
        elif 'system.columns' in sql:
            rows = [dict(table=t.split('.')[1], name=n, type=kind)
                    for t, schema in store.TABLE_SCHEMAS.items() if t in self.catalog
                    for n, kind in store._types(schema).items()]
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
        return iter(self.tables[table].to_batches(max_chunksize=2))


@pytest.fixture
def unit(monkeypatch):
    verification_calls = []
    def verified(m, client):
        verification_calls.append(m.token)
    monkeypatch.setattr(market_data, 'verify_market_day_plan', verified)
    monkeypatch.setattr(producer, 'verify_market_day_plan', verified)
    monkeypatch.setattr(native, 'verify_market_day_plan', verified)
    def native_candidates(market, **kwargs):
        assert kwargs['through_boundary_ms'] == 57600000
        return SimpleNamespace(token='f'*64, prepared=tuple(SimpleNamespace(ticker=t,
            boundary_ms=np.array([600000, 660000], dtype=np.int64)) for t in market.tickers))
    monkeypatch.setattr(candidates, 'certify_candidate_plan', native_candidates)
    m = plan(('AAA', 'BBB'))
    client = PersistentClient([bar(t, minute, price) for t in m.tickers
                               for minute, price in [(4,100000),(5,100000),(9,110000),(10,120000)]])
    source_kind = contract.DecisionSourceKind.CERTIFIED_MACD_CANDIDATES
    population = contract.certify_native_population(m, client, source_kind=source_kind)
    keys, attempt = population.packet(0)
    packet = producer.produce_completed_returns(m, keys, attempt, client)
    with client.authority.ownership(attempt) as lease:
        lease.admit_existing(False)
    return client, population, packet, verification_calls


def test_producer_persist_readback_certified_native_loader(unit):
    client, population, packet, calls = unit
    result = campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert result == dict(status='published', inserted_feature_rows=4, inserted_coverage_rows=2)
    assert [t for t, count in client.writes] == [FEATURE_TABLE, COVERAGE_TABLE, contract.CERTIFICATE_TABLE]
    loaded = installed.load_installed_completed_returns(population.market, 0, client,
        source_kind=population.source_kind, authority=client.authority)
    assert loaded.token == packet.token and len(calls) >= 4
    prior = len(client.writes)
    assert campaign.publish_packet(client, population, 0, packet, authority=client.authority)['status'] == 'skipped'
    assert len(client.writes) == prior


@pytest.mark.parametrize('stage', [FEATURE_TABLE, COVERAGE_TABLE, contract.CERTIFICATE_TABLE])
def test_uncertain_partial_insert_stops_then_explicit_restart_resumes(unit, stage):
    client, population, packet, _ = unit
    client.partial = stage
    with pytest.raises(TimeoutError):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert client.writes[-1][0] == stage
    if stage == FEATURE_TABLE:
        assert client.tables[COVERAGE_TABLE].num_rows == 0
    if stage != contract.CERTIFICATE_TABLE:
        with pytest.raises(ValueError, match='Missing installed'):
            installed.load_installed_completed_returns(population.market, 0, client,
                source_kind=population.source_kind, authority=client.authority)
    # A later row SELECT cannot clear the unknown original POST.
    with pytest.raises(InsertAuthorityUnavailable, match='unknown/unverified'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    client.authority.resolve_original_terminal(packet.feature_attempt_id, FixtureTerminalResolver(client))
    campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert table_hash(client.tables[FEATURE_TABLE]) == table_hash(packet.rows)
    assert table_hash(client.tables[COVERAGE_TABLE]) == table_hash(packet.coverage)
    assert client.tables[contract.CERTIFICATE_TABLE].num_rows == 1


@pytest.mark.parametrize('defect', ['policy', 'parts', 'columns', 'policy_disks', 'missing'])
def test_storage_failure_prevents_any_writes(unit, defect):
    client, population, packet, _ = unit
    if defect == 'policy': client.wrong_policy = True
    if defect == 'parts': client.wrong_parts = True
    if defect == 'columns': client.wrong_columns = True
    if defect == 'policy_disks': client.disks = ['live_market_ssd', 'default']
    if defect == 'missing': client.catalog.remove(FEATURE_TABLE)
    with pytest.raises(ValueError):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert not client.writes


def test_install_checks_existing_parts_before_ddl(unit):
    client, _, _, _ = unit
    client.catalog.clear()
    campaign.install(client)
    assert len(client.catalog) == 3 and [name for name, _ in client.writes] == ['DDL']*3
    client.writes.clear()
    client.wrong_parts = True
    with pytest.raises(ValueError, match='misplaced'):
        campaign.install(client)
    assert not client.writes


@pytest.mark.parametrize('defect', ['duplicate', 'mutation', 'foreign', 'premature_coverage'])
def test_partial_corruption_never_deleted_overwritten_or_certified(unit, defect):
    client, population, packet, _ = unit
    child = packet.rows.slice(0, 1)
    if defect == 'duplicate': child = pa.concat_tables([child, child])
    if defect == 'mutation':
        i = child.schema.get_field_index('return5')
        child = child.set_column(i, 'return5', pa.array([999.], type=pa.float64()))
    if defect == 'foreign':
        i = child.schema.get_field_index('decision_boundary_ms')
        child = child.set_column(i, 'decision_boundary_ms', pa.array([900000], type=pa.uint32()))
    client.tables[FEATURE_TABLE] = child
    if defect == 'premature_coverage': client.tables[COVERAGE_TABLE] = packet.coverage.slice(0, 1)
    before = table_hash(child)
    with pytest.raises(ValueError): campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert not client.writes and table_hash(client.tables[FEATURE_TABLE]) == before
    assert client.tables[contract.CERTIFICATE_TABLE].num_rows == 0


def test_native_certification_rejects_duplicate_receipt_and_mutated_children(unit):
    client, population, packet, _ = unit
    campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    certificate = client.tables[contract.CERTIFICATE_TABLE]
    client.tables[contract.CERTIFICATE_TABLE] = pa.concat_tables([certificate, certificate])
    with pytest.raises(ValueError, match='duplicate'):
        installed.load_installed_completed_returns(population.market, 0, client, source_kind=population.source_kind, authority=client.authority)
    client.tables[contract.CERTIFICATE_TABLE] = certificate
    client.tables[FEATURE_TABLE] = packet.rows.slice(0, 3)
    with pytest.raises(ValueError, match='pinned producer seal'):
        installed.load_installed_completed_returns(population.market, 0, client, source_kind=population.source_kind, authority=client.authority)


def test_full_native_scope_cannot_be_replaced_with_caller_subset(unit):
    client, population, packet, _ = unit
    keys = population.keys[:1]
    narrowed = replace(population, keys=keys, token=contract.population_token(population.market,
        population.candidate_token, keys, population.producer_source_hash, population.campaign_source_hash,
        population.source_kind))
    with pytest.raises(ValueError, match='current certified native source'):
        campaign.publish_packet(client, narrowed, 0, packet, authority=client.authority)
    assert not client.writes


def test_structural_source_missing_is_fatal_without_macd_substitution(unit):
    client, population, _, _ = unit
    before = len(client.sql)
    with pytest.raises(contract.MissingDecisionProducer, match='cannot substitute'):
        contract.certify_native_population(population.market, client,
            source_kind=contract.DecisionSourceKind.CERTIFIED_STRUCTURAL_DECISIONS)
    assert len(client.sql) == before and not client.writes
    with pytest.raises(ValueError, match='explicit'):
        contract.certify_native_population(population.market, client, source_kind='macd')


def test_produce_to_installed_consumer_callable_and_sealed_foundation(unit):
    client, population, _, _ = unit
    assert campaign.produce_and_publish_packet(client, population, 0, authority=client.authority)['status'] == 'published'
    assert producer_implementation_hash() == 'abbac77c485d7eec74536a981303d9ee47cb096d3688ed16b6b8d210b3703a10'


def test_catalog_allowlist_has_no_general_sql_escape(unit):
    client, _, _, _ = unit
    for sql in ['SELECT * FROM system.parts', 'SYSTEM FLUSH LOGS',
                "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'; DROP TABLE arte.x"]:
        with pytest.raises(ValueError, match='exact internal'):
            store._metadata(client, sql)
    assert not client.writes


def test_progress_reports_uncertain_partial_attempt_and_explicit_restart(unit):
    client, population, _, _ = unit
    client.partial = FEATURE_TABLE
    events = []
    with pytest.raises(TimeoutError):
        campaign.run_campaign(client, population.market, source_kind=population.source_kind, progress=events.append, authority=client.authority)
    assert [e['phase'] for e in events] == ['active', 'failed']
    assert events[-1]['failed'] == 1 and events[-1]['active'] == 0 and events[-1]['retried'] == 0
    events.clear()
    client.authority.resolve_original_terminal(population.packet(0)[1], FixtureTerminalResolver(client))
    campaign.run_campaign(client, population.market, source_kind=population.source_kind, progress=events.append, authority=client.authority)
    assert events[-1]['phase'] == 'terminal' and events[-1]['completed'] == 1
    assert events[-1]['failed'] == events[-1]['active'] == events[-1]['queued'] == 0


def test_concurrent_owner_rejected_before_any_subset_read_or_write(unit):
    client, population, packet, _ = unit
    before = len(client.sql)
    with client.authority.ownership(packet.feature_attempt_id):
        with pytest.raises(InsertAuthorityUnavailable, match='already owned'):
            campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert len(client.sql) == before and not client.writes


def test_unknown_original_blocks_even_when_rows_later_appear_complete(unit):
    client, population, packet, _ = unit
    client.partial = FEATURE_TABLE
    with pytest.raises(TimeoutError):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    # Simulate rows arriving after the timed-out caller. Their presence is not
    # proof that the original server request cannot append again later.
    client.tables[FEATURE_TABLE] = packet.rows
    original_terminal = dict(client.terminal_queries)
    client.terminal_queries.clear()
    before = len(client.sql)
    with pytest.raises(InsertAuthorityUnavailable, match='unknown/unverified'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert len(client.sql) == before
    with pytest.raises(InsertAuthorityUnavailable, match='still in flight'):
        client.authority.resolve_original_terminal(packet.feature_attempt_id, FixtureTerminalResolver(client))
    with pytest.raises(InsertAuthorityUnavailable, match='trusted typed'):
        client.authority.resolve_original_terminal(packet.feature_attempt_id, True)
    client.terminal_queries.update(original_terminal)
    client.authority.resolve_original_terminal(packet.feature_attempt_id, FixtureTerminalResolver(client))
    campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert client.tables[FEATURE_TABLE].num_rows == 4
    receipts = [path for path in client.authority.keeper.nodes if '/terminal-resolutions/' in path]
    assert len(receipts) == 1


def test_uncertain_certificate_cannot_be_loaded_without_completed_dispatch(unit):
    client, population, packet, _ = unit
    client.partial = contract.CERTIFICATE_TABLE
    with pytest.raises(TimeoutError):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert client.tables[contract.CERTIFICATE_TABLE].num_rows == 1
    with pytest.raises(InsertAuthorityUnavailable, match='unresolved/incomplete'):
        installed.load_installed_completed_returns(population.market, 0, client,
            source_kind=population.source_kind, authority=client.authority)


def test_required_authority_and_unregistered_rows_fail_closed(unit):
    client, population, packet, _ = unit
    with pytest.raises(ValueError, match='typed Keeper'):
        campaign.publish_packet(client, population, 0, packet, authority=None)
    client.authority = KeeperProductInsertAuthority(FakeKeeper())
    client.tables[FEATURE_TABLE] = packet.rows.slice(0, 1)
    with pytest.raises(InsertAuthorityUnavailable, match='Unregistered prior rows'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert not client.writes


def test_lease_loss_after_post_keeps_original_dispatch_fenced(unit):
    client, population, packet, _ = unit
    original = client.execute
    def lose_owner(sql, **kwargs):
        result = original(sql, **kwargs)
        if isinstance(sql, bytes):
            owner_path = INSERT_ROOT + '/' + packet.feature_attempt_id + '/owner'
            client.authority.keeper.nodes.pop(owner_path)
        return result
    client.execute = lose_owner
    with pytest.raises(InsertAuthorityUnavailable, match='ownership lost'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert client.tables[FEATURE_TABLE].num_rows == 4
    assert client.tables[COVERAGE_TABLE].num_rows == 0
    with pytest.raises(InsertAuthorityUnavailable, match='unknown/unverified'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)


def test_keeper_disconnect_rejects_owner_before_any_source_access(unit):
    client, population, packet, _ = unit
    client.authority.keeper.connected = False
    before = len(client.sql)
    with pytest.raises(InsertAuthorityUnavailable, match='connection unavailable'):
        campaign.publish_packet(client, population, 0, packet, authority=client.authority)
    assert len(client.sql) == before and not client.writes
