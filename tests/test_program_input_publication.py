"""Managed composition delegates to native pipelines with aggregate capacity."""
import asyncio

import pytest

from pipelines.market_sip.events import program_input_publication as publication
from test_columnar_history_publication import source as history_source


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
