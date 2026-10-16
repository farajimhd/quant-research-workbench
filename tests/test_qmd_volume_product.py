"""Shared volume math, independent source proofs, seals and bounded projections."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone, timedelta
import re

import numpy as np
import polars as pl
import pytest

from pipelines.market_sip.events import qmd_volume_producer as p
from src.backend import qmd_volume_reader as r
from src.market_engine import qmd_volume_product as v
from src.market_engine import columnar_history_product as history
from src.market_engine.causal_volume_history import elapsed_volume_history
from tests.test_columnar_history_publication import prepared_source


CODE = '5' * 64


@pytest.fixture
def source(prepared_source):
    return p.CertifiedVolumeSource(prepared_source.opening_source, prepared_source.opening_reference,
        prepared_source.ticker, prepared_source.first_bucket, prepared_source.stop_bucket, 30)


class Client:
    base_url = 'http://synthetic-volume'

    def __init__(self, store, writable=False):
        self.store, self.writable, self.closed = store, writable, False

    def execute(self, query):
        user = 'volume_writer' if self.writable else 'volume_reader'
        if query == 'SELECT currentUser()':
            return user
        if query == 'SHOW GRANTS FINAL':
            targets = [('q_live', t.name) for t in v.TABLES] + [('system', n) for n in (
                'storage_policies', 'tables', 'columns', 'parts', 'data_skipping_indices')]
            grants = [f'GRANT SELECT ON {db}.{name} TO {user}' for db, name in targets]
            if self.writable:
                grants += [f'GRANT INSERT ON q_live.{t.name} TO {user}' for t in v.TABLES]
            return '\n'.join(grants)
        raise AssertionError(query)

    def close(self):
        self.closed = True


class Owner:
    def assert_owner(self):
        pass


class Store:
    def __init__(self, source):
        self.source = source
        self.records = {t.name: [] for t in v.TABLES}
        self.bars = [dict(ticker=source.ticker, bucket_index=source.first_bucket + i, volume=float(i % 7) / 4)
                     for i in (1, 2, 3, 9, 50, 100)]
        self.writes, self.queries, self.clients, self.audit = [], [], [], []
        self.fail = None

    def client(self, writable=False):
        client = Client(self, writable)
        self.clients.append(client)
        return client

    def rows(self, client, query, bound):
        self.queries.append(query)
        if 'FROM arte.bars_v1' in query:
            begin = int(re.search(r'bucket_index>=(\d+)', query)[1])
            stop = int(re.search(r'bucket_index<(\d+)', query)[1])
            return [dict(x) for x in self.bars if begin <= x['bucket_index'] < stop]
        table = next(t for t in v.TABLES if f'FROM q_live.{t.name} ' in query)
        def matches(row):
            for name in ('source_build_id', 'listing_id', 'ticker', 'product_revision', 'derivation_attempt_id'):
                match = re.search(rf"{name}=(?:toUUID\()?\'([^']+)\'", query)
                if match and row[name] != match[1]:
                    return False
            for name in ('first_bucket', 'stop_bucket'):
                match = re.search(rf'{name}=(\d+)', query)
                if match and row[name] != int(match[1]):
                    return False
            for name, comparator in [('first_ordinal', '>'), ('first_ordinal', '<'), ('stop_ordinal', '>')]:
                match = re.search(rf'{name}{comparator}(-?\d+)', query)
                if match and not (row[name] > int(match[1]) if comparator == '>' else row[name] < int(match[1])):
                    return False
            return True
        rows = [dict(x) for x in self.records[table.name] if matches(x)]
        if table == v.INTERVAL:
            rows.sort(key=lambda x: x['first_ordinal'])
        limit = re.search(r'LIMIT (\d+)', query)
        return rows[:int(limit[1])] if limit else rows

    def insert(self, client, table, rows):
        self.writes.append(table.name)
        self.records[table.name].extend(dict(x) for x in rows)
        if self.fail == table.name:
            raise RuntimeError('injected ambiguous volume write')


@pytest.fixture
def store(source, monkeypatch):
    result = Store(source)
    monkeypatch.setattr(p, '_rows', result.rows)
    monkeypatch.setattr(r, '_rows', result.rows)
    monkeypatch.setattr(p, 'insert_product_rows', result.insert)
    monkeypatch.setattr(p, 'source_storage_preflight', lambda reader, **kwargs: None)
    monkeypatch.setattr(p, 'storage_preflight', lambda reader, **kwargs: None)
    monkeypatch.setattr(r, 'storage_preflight', lambda reader, **kwargs: None)
    monkeypatch.setattr(p.CertifiedOpeningInput, 'verify',
                        lambda self, reader: result.audit.append(('opening', self.market.token)))
    monkeypatch.setattr(p, 'certify_opening_reference', lambda *args, **kwargs: source.opening_reference)
    monkeypatch.setattr(p, '_verify_market_batch', lambda plan, stage, table, day, units, reader:
                        result.audit.append(('bars', tuple(units))))
    return result


def publish(store):
    return p.publish_volume_batch(store.client(), store.client(True), (store.source,),
                                  ownership=Owner(), code_hash=CODE)


@pytest.mark.parametrize('pattern', ['fractional', 'zero', 'dense'])
def test_interval_math_exactly_matches_shared_elapsed_transform(source, pattern):
    scope = source.scope('00000000-0000-0000-0000-000000000099', CODE)
    rng = np.random.default_rng(901)
    positions = np.arange(scope.clock_count) if pattern == 'dense' else np.array([1, 2, 3, 9, 50, 100])
    values = rng.random(len(positions)) if pattern != 'zero' else np.zeros(len(positions))
    bars = pl.DataFrame({'bucket_index': (positions + scope.first_bucket).astype(np.uint32), 'volume': values})
    rows = v.prepare_intervals(scope, bars, source.parents(CODE))
    actual = v.expand_window(scope, rows, first_ordinal=0, stop_ordinal=scope.clock_count)
    dense = pl.DataFrame({'listing_id': [scope.listing_id] * scope.clock_count,
        'session_date': [scope.session_date] * scope.clock_count,
        'boundary_at': actual['boundary_at'], 'available_at': actual['available_at'],
        'volume': np.zeros(scope.clock_count)})
    volume = np.zeros(scope.clock_count)
    volume[positions] = values
    dense = dense.with_columns(pl.Series('volume', volume))
    expected = elapsed_volume_history(dense, 30)
    assert actual['rolling_volume'].equals(expected['rolling_volume'])
    if pattern == 'zero':
        assert len(rows) == 1 and rows[0]['rolling_volume'] == 0
        assert rows[0]['first_ordinal'] == 0 and rows[0]['stop_ordinal'] == scope.clock_count


def test_real_publication_seals_last_and_cold_snapshot_expands_without_new_queries(store):
    (status, pin), = publish(store)
    assert status == 'published'
    assert store.writes[-1] == v.COVERAGE.name
    assert [kind for kind, _ in store.audit] == ['opening', 'bars', 'opening', 'bars']
    assert not any('liquidity' in q for q in store.queries)
    request = r.VolumePinRequest(pin.scope, pin.parents, pin.header['content_hash'], store.source)
    cold = r.certify_volume_product(store.client(), request)
    before = len(store.queries)
    actual = asyncio.run(r.read_volume_window_async(cold, first_ordinal=0, stop_ordinal=100,
        as_of=history.completed_clock(pin.scope, pin.scope.stop_bucket - 1)))
    assert actual.height == 100 and len(store.queries) == before
    assert set(actual.columns) == {'listing_id', 'boundary_at', 'available_at', 'rolling_volume'}
    import sys
    expected_bytes = sys.getsizeof(cold.intervals) + sys.getsizeof(cold._starts) + sum(
        sys.getsizeof(run) + sys.getsizeof(run.first_ordinal) + sys.getsizeof(run.stop_ordinal)
        + sys.getsizeof(run.rolling_volume) for run in cold.intervals)
    assert cold.retained_interval_bytes == expected_bytes > 0
    with pytest.raises(FrozenInstanceError):
        cold.intervals[0].rolling_volume = 7.
    with pytest.raises(TypeError):
        cold.header['content_hash'] = 'f' * 64
    with pytest.raises(ValueError, match='interval hash'):
        replace(cold, intervals=(replace(cold.intervals[0], rolling_volume=99.),) + cold.intervals[1:])
    writes = len(store.writes)
    assert publish(store)[0][0] == 'skipped_verified'
    assert len(store.writes) == writes


def test_ambiguous_child_write_leaves_no_seal_and_retry_fails_closed(store):
    store.fail = v.INTERVAL.name
    with pytest.raises(RuntimeError, match='ambiguous'):
        publish(store)
    assert not store.records[v.COVERAGE.name]
    store.fail = None
    with pytest.raises(ValueError, match='Unsealed volume attempt'):
        publish(store)


def test_source_change_before_seal_cannot_publish_success(store, monkeypatch):
    original = p.verify_volume_sources
    calls = 0
    def verify(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('source changed')
        return original(*args, **kwargs)
    monkeypatch.setattr(p, 'verify_volume_sources', verify)
    with pytest.raises(ValueError, match='source changed'):
        publish(store)
    assert store.records[v.INTERVAL.name] and not store.records[v.COVERAGE.name]


def test_complete_uncertain_attempt_reconciles_only_with_exact_hash_and_no_child_reinsert(store):
    projected = []
    store.fail = v.INTERVAL.name
    with pytest.raises(RuntimeError, match='ambiguous'):
        p.publish_volume_batch(store.client(), store.client(True), (store.source,), ownership=Owner(),
                               code_hash=CODE, progress=projected.append)
    event, = projected
    request = r.VolumePinRequest(event['scope'], store.source.parents(CODE),
                                event['expected_content_hash'], store.source)
    store.fail = None
    before = len(store.records[v.INTERVAL.name])
    receipts = p.publish_volume_batch(store.client(), store.client(True), (store.source,),
        ownership=Owner(), code_hash=CODE, reconcile_requests=(request,))
    assert receipts[0][0] == 'published'
    assert len(store.records[v.INTERVAL.name]) == before
    with pytest.raises(ValueError, match='existing coverage'):
        p.publish_volume_batch(store.client(), store.client(True), (store.source,),
            ownership=Owner(), code_hash=CODE,
            reconcile_requests=(replace(request, expected_content_hash='f' * 64),))


@pytest.mark.parametrize('corruption', ['gap', 'duplicate', 'volume', 'header', 'wrong_source'])
def test_cold_reader_rejects_corrupt_or_wrong_lineage_products(store, corruption):
    _, pin = publish(store)[0]
    request = r.VolumePinRequest(pin.scope, pin.parents, pin.header['content_hash'], store.source)
    if corruption == 'gap':
        store.records[v.INTERVAL.name].pop(0)
    elif corruption == 'duplicate':
        store.records[v.INTERVAL.name].append(dict(store.records[v.INTERVAL.name][0]))
    elif corruption == 'volume':
        store.records[v.INTERVAL.name][0]['rolling_volume'] += 1.
    elif corruption == 'header':
        store.records[v.COVERAGE.name].append(dict(pin.header))
    else:
        with pytest.raises(ValueError, match='source lineage'):
            replace(request, parents=replace(pin.parents, market_plan_hash='f' * 64))
        return
    with pytest.raises(ValueError):
        r.certify_volume_product(store.client(), request)


def test_async_fixed_reader_pool_retained_budget_and_completed_scope(store):
    _, pin = publish(store)[0]
    request = r.VolumePinRequest(pin.scope, pin.parents, pin.header['content_hash'], store.source)
    pins = asyncio.run(r.certify_volume_pins_async(store.client, (request,), concurrency=1,
                                                  maximum_retained_bytes=pin.retained_interval_bytes))
    assert pins[pin.scope.listing_id].intervals == pin.intervals
    assert all(c.closed for c in store.clients[-3:])
    with pytest.raises(ExceptionGroup, match='TaskGroup'):
        asyncio.run(r.certify_volume_pins_async(store.client, (request,), concurrency=1,
                                               maximum_retained_bytes=1))
    with pytest.raises(ValueError, match='completed scope'):
        asyncio.run(r.read_volume_window_async(pin, first_ordinal=0, stop_ordinal=100,
            as_of=history.completed_clock(pin.scope, pin.scope.first_bucket + 50)))


def test_schema_has_only_normalized_scalars_on_explicit_ssd():
    for table in v.TABLES:
        assert table.database == 'q_live' and "storage_policy = 'live_market_ssd'" in table.ddl()
        assert not any(kind.startswith(('Array', 'JSON', 'Map', 'Tuple')) for _, kind in table.columns)


@pytest.mark.parametrize('optional', ['none', 'history', 'features', 'both', 'partial', 'unknown', 'ddl'])
def test_shared_publisher_permissions_admit_only_complete_known_write_groups(source, optional):
    from src.market_engine.feature_source_plan import TABLES as FEATURES
    client = Client(Store(source), True)
    execute = client.execute
    extras = []
    if optional in {'history', 'both'}:
        extras += [t.name for t in history.TABLES]
    if optional in {'features', 'both'}:
        extras += [t.name for t in FEATURES]
    if optional == 'partial':
        extras = [history.TABLES[0].name]
    if optional == 'unknown':
        extras = ['unrelated_table']
    def query(sql):
        result = execute(sql)
        if sql == 'SHOW GRANTS FINAL':
            result += ''.join(f'\nGRANT INSERT ON q_live.{name} TO volume_writer' for name in extras)
            if optional == 'ddl':
                result += '\nGRANT ALTER ON q_live.qmd_volume_interval_v1 TO volume_writer'
        return result
    client.execute = query
    if optional in {'partial', 'unknown', 'ddl'}:
        with pytest.raises(ValueError):
            r.permission_preflight(client, writable=True)
    else:
        assert r.permission_preflight(client, writable=True) == 'volume_writer'


def test_grant_plan_is_exact_new_table_delta_without_wildcards():
    grants = v.plan_required_grants()
    assert len(grants) == 6 and not any('*' in g for g in grants)
    assert all(any(t.name in grant for t in v.TABLES) for grant in grants)
    with pytest.raises(ValueError):
        v.plan_required_grants(reader='invalid; user')


def test_interval_pagination_revisits_lookahead_and_rejects_duplicate_page_boundary(store):
    source = replace(store.source, stop_bucket=store.source.first_bucket + 5000)
    scope = source.scope('00000000-0000-0000-0000-000000000098', CODE)
    bars = pl.DataFrame({'bucket_index': pl.Series(range(scope.first_bucket, scope.stop_bucket), dtype=pl.UInt32),
                         'volume': np.arange(5000, dtype=np.float64)})
    intervals = v.prepare_intervals(scope, bars, source.parents(CODE))
    assert len(intervals) > 4096
    store.records[v.INTERVAL.name] = [dict(row) for row in intervals]
    assert r.read_intervals(store.client(), scope) == intervals
    store.records[v.INTERVAL.name].append(dict(intervals[4095]))
    with pytest.raises(ValueError, match='duplicate or unordered'):
        r.read_intervals(store.client(), scope)


def test_session_admission_refresh_rejects_changed_coverage_without_expanding_dense_origin(store):
    _, pin = publish(store)[0]
    before = len(store.queries)
    asyncio.run(r.verify_volume_pin_headers_async(store.client, (pin,)))
    assert len(store.queries) == before + 1
    store.records[v.COVERAGE.name][0]['content_hash'] = 'f' * 64
    with pytest.raises(ValueError, match='changed or became ambiguous'):
        asyncio.run(r.verify_volume_pin_headers_async(store.client, (pin,)))
