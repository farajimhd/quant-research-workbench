import asyncio
from threading import Lock

import pytest

from src.backend.backtest_program_feature_cache import ProgramFeatureBlockCache
from src.market_engine import columnar_history_product as c
from tests.test_backtest_program_inputs import release_for
from tests.test_columnar_history_publication import prepared_source, source, Store, publish, consume
from tests.test_qmd_columnar_history_transport import response_for


def test_cache_single_flights_exact_source_and_origin_and_rechecks_availability(prepared_source):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    calls, lock = [], Lock()
    def post(path, request, timeout):
        with lock: calls.append(request)
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])
    cache = ProgramFeatureBlockCache()
    kwargs = dict(first_ordinal=0, stop_ordinal=200, evaluation_block="optimizer-origin",
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=2, history_post=post)
    pins = {pin.scope.listing_id: pin}
    async def run():
        first, second = await asyncio.gather(cache.load(release, pins, **kwargs),
                                            cache.load(release, pins, **kwargs))
        assert first is second and len(calls) == 1
        assert await cache.load(release, pins, **kwargs) is first
        assert len(calls) == 1
        with pytest.raises(ValueError, match="beyond its as-of clock"):
            await cache.load(release, pins, **{**kwargs, "as_of": c.completed_clock(pin.scope, pin.scope.first_bucket)})
        assert len(calls) == 1
        changed = await cache.load(release, pins, **{**kwargs, "evaluation_block": "another-origin"})
        assert changed is not first and len(calls) == 2
        assert cache._block is changed  # Exactly one retained block.
    asyncio.run(run())


def test_cache_does_not_retain_failed_preparation(prepared_source):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin, cache = certificate.pin(), ProgramFeatureBlockCache()
    def failed(*args, **kwargs): raise RuntimeError("read failed")
    kwargs = dict(first_ordinal=0, stop_ordinal=200, evaluation_block="optimizer-origin",
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=2)
    async def run():
        with pytest.raises(RuntimeError, match="read failed"):
            await cache.load(release_for(pin), {pin.scope.listing_id: pin}, **kwargs, history_post=failed)
        assert cache._block is None and cache._key is None
    asyncio.run(run())
