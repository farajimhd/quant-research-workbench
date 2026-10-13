"""Whole invocation ownership, source preflight and cancellation boundaries."""
import asyncio
from threading import Event, get_ident

import pytest

from pipelines.market_sip.events import current_market_day_runtime as runtime
from pipelines.market_sip.events.current_market_day_transport import MarketDayQueryLimits
from scripts.build_market_day import digest
from test_arte_market_day_keeper import FakeKeeper
from test_arte_market_day_source_plan import plan
from test_arte_market_day_certification import PIN


@pytest.mark.parametrize('mode', ['complete', 'source_failure', 'contended', 'run_failure', 'cancel_open'])
def test_owned_runtime_preflights_drains_and_closes(monkeypatch, mode):
    definition = dict(version='market-day-core-v5', calculation_source=PIN, rules_hash=PIN, plan=plan())
    build = digest(definition)
    calls, closed, threads = [], [], []
    main = get_ident()
    entered, released = Event(), Event()
    keeper = FakeKeeper()
    class Resource:
        def __init__(self, kind):
            self.kind = kind
        def execute(self, query, **kwargs):
            threads.append(get_ident())
            return '1'
        def close(self):
            threads.append(get_ident())
            closed.append(self.kind)
    class Session(Resource):
        client = keeper
    def reader():
        calls.append('reader')
        threads.append(get_ident())
        if mode == 'cancel_open':
            entered.set()
            assert released.wait(5)
        return Resource('reader')
    def source():
        calls.append('source')
        return Resource('source')
    def session():
        calls.append('keeper')
        return Session('keeper')
    def writer(settings):
        calls.append('writer')
        assert settings['max_bytes_before_external_sort'] == 0
        return Resource('writer')
    def verify(source, supplied):
        threads.append(get_ident())
        calls.append('source_verified')
        assert supplied == definition['plan']
        if mode == 'source_failure':
            raise ValueError('Changed source')
    monkeypatch.setattr(runtime, '_placement', lambda _: calls.append('placement'))
    monkeypatch.setattr('src.trading_runtime.arte_market_day_source_plan.verify_canonical_source_plan_at_publication', verify)
    if mode == 'contended':
        from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
        assert MarketDayKeeperAuthority(keeper).acquire(build, 'other') is not None
    async def producer(supplied, checkpoint, **kwargs):
        calls.append('producer')
        assert checkpoint.keeper.current(checkpoint.claim)
        assert checkpoint.claim.build_id == build
        assert kwargs['workers'] == 2
        assert calls.index('source_verified') < calls.index('keeper') < calls.index('writer')
        if mode == 'run_failure':
            raise ValueError('Producer failed')
        return 'native-proof-fixture'
    monkeypatch.setattr(runtime, 'run_current_market_day_producer', producer)
    async def run():
        task = asyncio.create_task(runtime.execute_current_market_day_build(definition,
            owner_id='test-owner', keeper_session_factory=session,
            checkpoint_reader_factory=reader, writer_http_factory=writer,
            sql_http_factory=writer, source_client_factory=source,
            limits=MarketDayQueryLimits(2, 1024, 30, 2, 64), parents=(), workers=2))
        if mode == 'cancel_open':
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == 'complete':
            assert await task == 'native-proof-fixture'
        else:
            with pytest.raises((ValueError, RuntimeError)):
                await task
    asyncio.run(run())
    assert threads and all(thread != main for thread in threads)
    assert closed.count('reader') == 1
    assert closed.count('source') == 1
    assert closed.count('keeper') == (0 if mode == 'source_failure' else 1)
    assert closed.count('writer') == (0 if mode in ('source_failure', 'contended') else 1)
    assert ('producer' in calls) == (mode in ('complete', 'run_failure'))


def test_invalid_runtime_is_inert():
    opened = []
    def factory(*args):
        opened.append(True)
    with pytest.raises(ValueError):
        asyncio.run(runtime.execute_current_market_day_build({}, owner_id='owner',
            keeper_session_factory=factory, checkpoint_reader_factory=factory,
            writer_http_factory=factory, sql_http_factory=factory, source_client_factory=factory,
            limits=MarketDayQueryLimits(2, 1024, 30, 2, 64), parents=(), workers=0))
    assert not opened
