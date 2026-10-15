"""Managed composition delegates to native pipelines with aggregate capacity."""
import asyncio

import pytest

from pipelines.market_sip.events import program_input_publication as publication
from test_columnar_history_publication import source as history_source
from test_columnar_history_publication import prepared_source


def test_native_composition_is_lazy_and_bounds_cross_pipeline_work(monkeypatch):
    calls = []
    def reader(role, **kwargs):
        calls.append(('reader_factory', role))
        return lambda: pytest.fail('Unexpected connection')
    def writer(role, **kwargs):
        calls.append(('writer_factory', role))
        return lambda: pytest.fail('Unexpected connection')
    monkeypatch.setattr(publication, 'program_app_reader_factory', reader)
    monkeypatch.setattr(publication, 'program_app_writer_factory', writer)
    lane = publication.ProgramInputPublication(query_limits={}, maximum_concurrent_units=1)
    assert type(lane.opening) is publication.OpeningReferencePipeline
    assert type(lane.history) is publication.HistoryProductPipeline
    assert {role for kind, role in calls if kind == 'writer_factory'} == {
        'opening_reference_publisher', 'history_publisher', 'liquidity_publisher'}
    async def run():
        source = history_source().liquidity_source
        entered, release = asyncio.Event(), asyncio.Event()
        async def opening(*args, **kwargs):
            entered.set()
            await release.wait()
            return 'opening-receipt'
        async def history(*args, **kwargs):
            return 'history-receipt'
        async def liquidity(*args, **kwargs):
            return 'liquidity-receipt'
        monkeypatch.setattr(lane.opening, 'publish', opening)
        monkeypatch.setattr(lane.history, 'publish', history)
        monkeypatch.setattr(publication, 'publish_unit_async', liquidity)
        job = asyncio.create_task(lane.publish_opening(None, None, ownership=None, code_hash='pin'))
        await entered.wait()
        with pytest.raises(ValueError, match='capacity'):
            await lane.publish_history(None, ownership=None, code_hash='pin')
        with pytest.raises(ValueError, match='capacity'):
            await lane.publish_liquidity(source, ownership=None, code_hash='pin')
        release.set()
        assert await job == 'opening-receipt'
        assert await lane.publish_history(None, ownership=None, code_hash='pin') == 'history-receipt'
        assert await lane.publish_liquidity(source, ownership=None, code_hash='pin') == 'liquidity-receipt'
        assert lane.active == 0
    asyncio.run(run())
    with pytest.raises(ValueError, match='event-loop'):
        asyncio.run(lane.publish_history(None, ownership=None, code_hash='pin'))


def test_failure_releases_aggregate_capacity(monkeypatch):
    lane = publication.ProgramInputPublication(query_limits=dict(max_threads=2,
        max_memory_usage=1024, max_execution_time=30, max_result_rows=10, max_result_bytes=2048),
        maximum_concurrent_units=1)
    async def fail(*args, **kwargs):
        raise ValueError('Native publication failure')
    monkeypatch.setattr(lane.history, 'publish', fail)
    with pytest.raises(ValueError, match='Native publication'):
        asyncio.run(lane.publish_history(None, ownership=None, code_hash='pin'))
    assert lane.active == 0


def test_duplicate_liquidity_is_rejected_before_second_publication(monkeypatch):
    lane = publication.ProgramInputPublication(query_limits=dict(max_threads=2,
        max_memory_usage=1024, max_execution_time=30, max_result_rows=10, max_result_bytes=2048),
        maximum_concurrent_units=2)
    source = history_source().liquidity_source
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def liquid(*args, **kwargs):
            entered.set()
            await release.wait()
            raise ValueError('Uncertain publication')
        monkeypatch.setattr(publication, 'publish_unit_async', liquid)
        task = asyncio.create_task(lane.publish_liquidity(source, ownership=None, code_hash='pin'))
        await entered.wait()
        with pytest.raises(ValueError, match='reconciliation'):
            await lane.publish_liquidity(source, ownership=None, code_hash='pin')
        release.set()
        with pytest.raises(ValueError, match='Uncertain'):
            await task
        with pytest.raises(ValueError, match='reconciliation'):
            await lane.publish_liquidity(source, ownership=None, code_hash='pin')
        assert not lane._liquidity_active and lane.active == 0
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['complete', 'missing_volume', 'origin_mismatch', 'bad_windows'])
def test_session_receipts_bridge_native_publication_to_app_request(prepared_source, monkeypatch, mode):
    from dataclasses import replace
    from datetime import datetime, UTC
    from types import MappingProxyType
    from test_columnar_history_publication import Store, Client, Owner, CODE, publish, consume
    from tests.test_backtest_program_inputs import release_for
    from src.backend.backtest_program_input_plan import ProgramPublishedInputRequest
    from src.backend.qmd_columnar_history import certify_history_product
    opening = replace(prepared_source.opening_reference, rows_by_listing=MappingProxyType({
        'listing': MappingProxyType(dict(ticker='ABC', listing_id='listing',
            status='resolved', adjusted_prior_close=10.))}))
    feature = replace(prepared_source, opening_reference=opening)
    volume = replace(feature, fields=())
    feature_store, volume_store = Store(), Store()
    publish(feature_store, feature)
    release = release_for(consume(feature_store, feature).pin())
    lane = publication.ProgramInputPublication(query_limits=dict(max_threads=2,
        max_memory_usage=1024, max_execution_time=30, max_result_rows=1000, max_result_bytes=65536),
        maximum_concurrent_units=1)
    # The fixture models one product partition per store; real ClickHouse
    # filters the shared tables by the native product-revision scope.
    readers, writers = iter((feature_store, volume_store)), iter((feature_store, volume_store))
    lane.history.reader_factory = lambda: Client(next(readers))
    lane.history.writer_factory = lambda: Client(next(writers), True)
    packet = () if mode == 'missing_volume' else (volume,)
    if mode == 'origin_mismatch':
        packet = (replace(volume, first_bucket=34201, stop_bucket=34401),)
    windows = ((34200, 34500),) if mode == 'bad_windows' else ((34200, 34400),)
    reports = []
    async def report(value):
        reports.append(dict(value))
    pending = lane.publish_session_histories(release, opening, (feature,), packet,
        ownership=Owner(), code_hash=CODE, first_ordinal=0, stop_ordinal=200,
        evaluation_block='full-session', cohort_windows=windows,
        as_of=datetime(2026, 8, 4, tzinfo=UTC), on_progress=report)
    if mode != 'complete':
        with pytest.raises(ValueError):
            asyncio.run(pending)
        assert not volume_store.writes and not reports
        assert lane.history.completed == lane.history.skipped_verified == 0
        return
    request = asyncio.run(pending)
    assert type(request) is ProgramPublishedInputRequest
    assert len(request.feature_requests) == len(request.volume_requests) == 1
    assert not request.volume_requests[0].fields
    for pin, store in zip(request.feature_requests + request.volume_requests, (feature_store, volume_store)):
        product = certify_history_product(Client(store), pin.scope, pin.fields, pin.parents,
            expected_content_hash=pin.expected_content_hash)
        assert product.pin().scope == pin.scope
    assert lane.history.skipped_verified == 1 and lane.history.completed == 1
    assert reports[-1]['queued'] == reports[-1]['active'] == reports[-1]['failed'] == 0
    assert reports[-1]['completed'] == reports[-1]['skipped'] == 1
    assert all(sum(row[key] for key in ('queued', 'active', 'completed', 'skipped', 'failed')) == row['total']
        for row in reports)

def test_compact_volume_receipts_keep_managed_feature_publication(prepared_source, monkeypatch):
    from dataclasses import replace
    from datetime import datetime, UTC
    from types import MappingProxyType
    from test_columnar_history_publication import Store, Client, Owner, CODE, publish, consume
    from tests.test_backtest_program_inputs import release_for
    from pipelines.market_sip.events.qmd_volume_producer import CertifiedVolumeSource
    from src.backend.qmd_volume_reader import VolumePinRequest
    opening = replace(prepared_source.opening_reference, rows_by_listing=MappingProxyType({
        'listing': MappingProxyType(dict(ticker='ABC', listing_id='listing',
            status='resolved', adjusted_prior_close=10.))}))
    feature = replace(prepared_source, opening_reference=opening)
    store = Store(); publish(store, feature)
    release = release_for(consume(store, feature).pin())
    source = CertifiedVolumeSource(feature.opening_source, opening, feature.ticker,
        feature.first_bucket, feature.stop_bucket, 30)
    volume = VolumePinRequest(source.scope('00000000-0000-0000-0000-000000000091', CODE),
        source.parents(CODE), '5' * 64, source)
    lane = publication.ProgramInputPublication(query_limits=dict(max_threads=2,
        max_memory_usage=1024, max_execution_time=30, max_result_rows=1000, max_result_bytes=65536),
        maximum_concurrent_units=1)
    lane.history.reader_factory = lambda: Client(store)
    lane.history.writer_factory = lambda: Client(store, True)
    request = asyncio.run(lane.publish_session_histories(release, opening, (feature,), (volume,),
        ownership=Owner(), code_hash=CODE, first_ordinal=0, stop_ordinal=200,
        evaluation_block='full-session', cohort_windows=((34200, 34400),),
        as_of=datetime(2026, 8, 4, tzinfo=UTC), maximum_retained_population_bytes=1024))
    assert request.volume_requests == (volume,)
    assert request.maximum_retained_population_bytes == 1024
    assert lane.history.skipped_verified == 1 and lane.history.completed == 0


def test_scanner_volume_publication_closes_owned_clients_on_failure(monkeypatch):
    from pipelines.market_sip.events import qmd_volume_producer
    import threading
    lane = publication.ProgramInputPublication(query_limits=dict(max_threads=1,
        max_memory_usage=1024, max_execution_time=30, max_result_rows=1000, max_result_bytes=65536),
        maximum_concurrent_units=1)
    clients = []
    class Client:
        closed = False
        def close(self): self.closed = True
    def factory():
        client = Client(); clients.append(client); return client
    lane.history.reader_factory = lane.history.writer_factory = factory
    caller_thread = threading.get_ident()
    def publish(*args, **kwargs):
        assert threading.get_ident() != caller_thread
        assert kwargs['maximum_retained_bytes'] == 1024
        raise RuntimeError('Ambiguous volume publication')
    monkeypatch.setattr(qmd_volume_producer, 'publish_volume_batch', publish)
    with pytest.raises(RuntimeError, match='Ambiguous'):
        asyncio.run(lane.publish_scanner_volume((), ownership=None, code_hash='5'*64,
            maximum_retained_bytes=1024))
    assert len(clients) == 2 and all(client.closed for client in clients)
    assert lane.active == 0
