"""Shared QMD publication, native opening consumption and causal rejection."""
import asyncio
from dataclasses import replace
from datetime import date
import json
import re
from threading import get_ident

import pytest

from pipelines.market_sip.events import regular_close_history_producer as p
from pipelines.market_sip.events import opening_price_reference_producer as opening
from src.market_engine import regular_close_history as c
from tests.test_opening_price_reference_producer import source as opening_source


class Store:
    def __init__(self):
        self.rows = {table.name: [] for table in c.TABLES}
        self.writes, self.closed, self.threads = [], [], []
        self.empty = self.bad_disk = self.future = self.change_source = self.query_failure = False
        self.fail_insert = None
        self.price_reads = 0
        self.source = None


class Client:
    base_url = 'http://synthetic'
    def __init__(self, store, role):
        self.store, self.role = store, role

    def close(self):
        self.store.closed.append(self.role)

    def execute(self, query):
        self.store.threads.append(get_ident())
        encode = lambda rows: '\n'.join(json.dumps(row, default=str) for row in rows)
        if query == 'SELECT currentUser()':
            return self.role
        if query == "SELECT getSetting('readonly')":
            return '0' if self.role == 'writer' else '1'
        if query == 'SHOW GRANTS FINAL':
            return '\n'.join(f'GRANT SELECT{", INSERT" if self.role == "writer" else ""} ON q_live.{t.name} TO {self.role}'
                for t in c.TABLES)
        if 'FROM system.storage_policies' in query:
            return encode([dict(disks=['sip_raw_ssd' if self.role == 'canonical' else 'live_market_ssd'])])
        if 'FROM system.parts' in query:
            return encode([dict(disk_name='default')]) if self.store.bad_disk else ''
        if 'FROM system.tables' in query:
            if self.role == 'canonical':
                return encode([dict(name='events_' + str(self.store.source.scope.regular.day.year), storage_policy='sip_raw_ssd')])
            return encode([dict(name=t.name, engine='MergeTree', storage_policy='live_market_ssd',
                partition_key=t.partition, sorting_key=t.order) for t in c.TABLES])
        if 'FROM system.columns' in query:
            return encode([dict(table=t.name, name=name, type=kind) for t in c.TABLES for name, kind in t.columns])
        if 'FROM system.data_skipping_indices' in query:
            return ''
        scope = self.store.source.scope
        low = int(scope.regular.opened_at.timestamp() * 1000000)
        high = int(scope.regular.closed_at.timestamp() * 1000000)
        if 'FROM market_sip_compact.events_source_day_stats' in query:
            return encode([dict(stats_version=1, source_filter_key='synthetic-source',
                total_event_rows_after_filters=2, updated_at='2026-10-13 01:00:00')])
        if 'FROM market_sip_compact.events_ordinal_continuity' in query:
            return encode([dict(ticker='ABC', event_count=2, next_ordinal=2, last_ordinal=1,
                first_sip_timestamp_us=low, last_sip_timestamp_us=high - 1,
                build_step=1, updated_at='2026-10-13 01:00:00')])
        if 'FROM market_sip_compact.event_condition_token_reference' in query:
            return encode([dict(token_id=1, modifier_int=0, update_high_low=1, update_last=1, update_volume=1)])
        if 'FROM arte.market_day_source_rule_v1' in query:
            return encode([dict(token_id=1, modifier_int=0, update_high_low=1, update_last=1, update_volume=1)])
        if 'FROM market_sip_compact.events_' in query:
            if self.store.query_failure:
                raise RuntimeError('canonical read failed')
            self.store.price_reads += 1
            if self.store.empty:
                return ''
            return encode([dict(ticker='ABC', closing_bar_count=1,
                prior_close_int=300000 if self.store.change_source and self.store.price_reads > 1 else 200000,
                close_sip_timestamp_us=high if self.store.future else high - 1, close_ordinal=1)])
        if query.startswith('INSERT'):
            assert self.role == 'writer'
            name = re.search(r'INSERT INTO `q_live`\.`([a-z0-9_]+)`', query).group(1)
            self.store.rows[name].extend(json.loads(row) for row in query.split('\n')[1:])
            self.store.writes.append(name)
            if name == self.store.fail_insert:
                raise RuntimeError('ambiguous insert')
            return ''
        if 'FROM q_live.market_stock_split_v1' in query:
            return ''
        for table in c.TABLES:
            if f'FROM q_live.{table.name} ' in query:
                rows = self.store.rows[table.name]
                if query.startswith('SELECT count()'):
                    key = 'ticker' if table == c.VALUES else 'token_id'
                    return encode([dict(n=len(rows), keys=len({row[key] for row in rows}))])
                if query.startswith('SELECT attempt_id'):
                    return encode([dict(attempt_id=row['attempt_id']) for row in rows[:1]])
                if query.startswith('SELECT ticker,closing_bar_count,prior_close_int'):
                    return encode([{key: row[key] for key in ('ticker','closing_bar_count','prior_close_int')}
                        for row in rows if row['closing_bar_count']])
                if 'ticker IN' in query:
                    rows = [row for row in rows if row['ticker'] in self.store.source.scope.tickers]
                return encode(rows)
        raise AssertionError('Unexpected query: ' + query)


class Owner:
    def assert_owner(self):
        pass


@pytest.fixture
def setup(monkeypatch):
    native = opening_source()
    store = Store()
    store.source = p.PriorCloseInput(native.market, 'sip_raw_ssd', 'sip_raw_ssd', p.producer_code_hash(),
        native.identity.token, native.population_reference_hash, ((1,0,1,1,1),))
    # Native identity/reference reader contracts have separate cold-read tests;
    # this fixture supplies their exact already-admitted pins.
    def verify(reader, source):
        assert source.consumer_identity_plan_hash == native.identity.token
        assert source.population_reference_hash == native.population_reference_hash
        assert source.market == native.market
    monkeypatch.setattr(p, '_verify_consumer', verify)
    return store, native


def publish(store):
    return p.publish_prior_close(Client(store, 'reader'), Client(store, 'canonical'),
        Client(store, 'writer'), store.source, ownership=Owner())


def test_full_publication_cold_read_and_existing_opening_consumption(setup):
    store, native = setup
    result = publish(store)
    assert store.writes[-1] == c.COVERAGE.name
    assert result.header['resolved_count'] == 1
    assert result == p.certify_prior_close(Client(store, 'reader'), store.source.scope, expected_hash=result.token)
    source = opening.CertifiedOpeningInput(native.market, result, native.identity,
        native.listings, native.population_reference_hash)
    references, splits = opening.materialize_opening_reference(Client(store, 'reader'), source,
        attempt_id='00000000-0000-0000-0000-000000000005')
    assert references[0]['prior_close_int'] == 200000
    assert references[0]['adjusted_prior_close'] == 20
    assert references[0]['prior_source_build_id'] == result.build_id
    assert not splits
    before = tuple(store.writes)
    assert publish(store) == result
    assert tuple(store.writes) == before


def test_july29_history_uses_july30_consumer_without_historical_universe(setup, monkeypatch):
    store, native = setup
    scope = c.PriorCloseScope(native.market.build_id, date(2026, 7, 30), native.market.token,
        native.market.tickers, p.producer_code_hash())
    query = p.prior_events_sql(scope, ('ABC',), [dict(token_id=1,modifier_int=0,update_last=1,
        update_high_low=1,update_volume=1)])
    assert scope.regular.day == date(2026, 7, 29)
    assert scope.regular.closed_at < scope.cutoff
    assert 'market_sip_compact.events_2026' in query
    assert '2026-07-29' in query and 'snapshot' not in query
    assert 'sip_timestamp_us,ordinal' in query
    assert 'file(' not in query and 'flatfile' not in query
    market = replace(native.market, sessions=('2026-07-30',))
    store.source = replace(store.source, market=market)
    monkeypatch.setattr(p, '_verify_consumer', lambda reader, source: None)
    published = publish(store)
    assert published.scope.consumer_session_date == date(2026,7,30)
    assert published.records[0]['prior_session_date'] == date(2026,7,29)
    assert published.header['consumer_cutoff'] > published.scope.regular.closed_at


@pytest.mark.parametrize('future', [False,True])
def test_native_consumer_preparation_uses_real_pin_contract_and_rejects_future_reference(setup, monkeypatch, future):
    from dataclasses import asdict
    from src.backend import backtest_strategy_one_identity
    from src.trading_runtime import historical_reference_identity
    store, native = setup
    pin = historical_reference_identity.ReferencePin('snapshot','preopen-tradable-snapshot-v3','6'*64,
        '2026-08-03 08:00:00' if future else '2026-08-03 07:59:00', '2026-08-03 08:00:00')
    monkeypatch.setattr(backtest_strategy_one_identity,'certify_identity_plan',lambda market,client: native.identity)
    monkeypatch.setattr(historical_reference_identity,'load_reference_pin',lambda reader,market:pin)
    if future:
        with pytest.raises(ValueError,match='causal cutoff'):
            p.certified_prior_close_input(Client(store,'reader'), native.market,
                canonical_storage_policy='sip_raw_ssd',canonical_disk='sip_raw_ssd')
    else:
        source = p.certified_prior_close_input(Client(store,'reader'),native.market,
            canonical_storage_policy='sip_raw_ssd',canonical_disk='sip_raw_ssd')
        assert source.consumer_rules == ((1,0,1,1,1),)
        assert source.consumer_identity_plan_hash == native.identity.token
        assert source.population_reference_hash == c.content_hash(asdict(pin))


@pytest.mark.parametrize('mode', ['future','bad_disk','query_failure'])
def test_invalid_or_unavailable_source_fails_before_any_write(setup, mode):
    store, _ = setup
    setattr(store, mode, True)
    with pytest.raises((ValueError,RuntimeError)):
        publish(store)
    assert not store.writes


def test_query_success_without_eligible_prices_is_explicit_covered_empty(setup):
    store, native = setup
    store.empty = True
    result = publish(store)
    assert result.header['covered_empty_count'] == 1
    assert result.records[0]['prior_close_int'] is None
    source = opening.CertifiedOpeningInput(native.market, result, native.identity,
        native.listings, native.population_reference_hash)
    rows, _ = opening.materialize_opening_reference(Client(store, 'reader'), source,
        attempt_id='00000000-0000-0000-0000-000000000005')
    assert rows[0]['status'] == 'covered_empty'


@pytest.mark.parametrize('mode', ['change_source','ambiguous'])
def test_source_change_or_ambiguous_insert_never_gets_a_seal(setup, mode):
    store, _ = setup
    if mode == 'ambiguous':
        store.fail_insert = c.VALUES.name
    else:
        store.change_source = True
    with pytest.raises((ValueError,RuntimeError)):
        publish(store)
    assert not store.rows[c.COVERAGE.name]
    before = tuple(store.writes)
    with pytest.raises(ValueError, match='reconciliation'):
        publish(store)
    assert tuple(store.writes) == before


@pytest.mark.parametrize('mode', ['price','extra','consumer'])
def test_cold_read_rejects_tampering_extra_rows_or_changed_consumer(setup, mode):
    store, native = setup
    result = publish(store)
    if mode == 'consumer':
        with pytest.raises(ValueError, match='consumer'):
            result.verify_consumer(replace(native.market, token='7'*64))
        return
    if mode == 'price':
        store.rows[c.VALUES.name][0]['prior_close_int'] = 999999
    else:
        store.rows[c.VALUES.name].append({**store.rows[c.VALUES.name][0], 'ticker':'FOREIGN'})
    with pytest.raises(ValueError):
        p.certify_prior_close(Client(store, 'reader'), store.source.scope)


def test_async_publication_owns_closes_and_keeps_io_off_loop(setup):
    store, _ = setup
    main = get_ident()
    result = asyncio.run(p.publish_prior_close_async(lambda:Client(store,'reader'),
        lambda:Client(store,'canonical'),lambda:Client(store,'writer'),store.source,ownership=Owner()))
    assert result.token and store.closed == ['writer','canonical','reader']
    assert store.threads and all(thread != main for thread in store.threads)


def test_managed_publication_routes_native_unit_through_existing_roles_and_capacity(setup, monkeypatch):
    from pipelines.market_sip.events import program_input_publication as managed
    store, _ = setup
    lane = managed.ProgramInputPublication(query_limits=dict(max_threads=2,max_memory_usage=1048576,
        max_execution_time=30,max_result_rows=100000,max_result_bytes=16000000),maximum_concurrent_units=1)
    calls = []
    async def native(reader_factory, canonical_factory, writer_factory, source, *, ownership):
        assert lane.active == 1 and source is store.source
        assert all(callable(factory) for factory in (reader_factory,canonical_factory,writer_factory))
        calls.append(source.scope)
        return 'native-sealed-plan'
    monkeypatch.setattr(p,'publish_prior_close_async',native)
    canonical_factory = lambda: pytest.fail('Synthetic composition must not open a real connection')
    async def run():
        lane.active = 1
        with pytest.raises(ValueError,match='capacity'):
            await lane.publish_prior_close(store.source,canonical_reader_factory=canonical_factory,ownership=Owner())
        assert not lane._prior_close_failed and not calls
        lane.active = 0
        result = await lane.publish_prior_close(store.source,canonical_reader_factory=canonical_factory,ownership=Owner())
        assert result == 'native-sealed-plan' and lane.active == 0
        assert not lane._prior_close_active
    asyncio.run(run())
    assert calls == [store.source.scope]


def test_schemas_are_shared_normalized_ssd_qmd_products():
    for table in c.TABLES:
        assert table.database == 'q_live'
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert all('json' not in name.lower() and 'blob' not in name.lower() for name,_ in table.columns)
    assert c.COVERAGE.columns[-1][0] == 'certified_at'
