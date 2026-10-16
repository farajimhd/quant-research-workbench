"""Native cold seals become compact app pins without a producer fallback."""
import asyncio
from dataclasses import replace
from threading import Barrier, Event, Lock

import pytest

from src.backend import qmd_columnar_history as history
from src.backend.qmd_columnar_population import certify_population_history_async
from test_columnar_history_publication import Store, Client, publish, consume, prepared_source
from test_qmd_columnar_population import population


def request(product):
    return history.HistoryPinRequest(product.prepared.scope, product.prepared.fields,
        product.parents, product.header['content_hash'])


def test_real_cold_product_loads_only_pin_and_closes_reader(prepared_source):
    store = Store()
    publish(store, prepared_source)
    product = consume(store, prepared_source)
    writes = len(store.writes)
    pins = asyncio.run(history.certify_history_pins_async(lambda: Client(store),
        (request(product),), concurrency=2))
    assert pins['listing'] == product.pin()
    assert type(pins['listing']) is history.CertifiedHistoryPin
    assert store.closed == ['reader'] and len(store.writes) == writes
    with pytest.raises(TypeError):
        pins['other'] = product.pin()


def test_corrupt_children_fail_native_reader_and_still_close(prepared_source):
    store = Store()
    publish(store, prepared_source)
    product = consume(store, prepared_source)
    from src.market_engine import columnar_history_product as c
    store.records[c.CLOCK.name][0]['rolling_volume'] += 1
    with pytest.raises(ExceptionGroup):
        asyncio.run(history.certify_history_pins_async(lambda: Client(store),
            (request(product),), concurrency=1))
    assert store.closed == ['reader']


def test_incomplete_population_rejected_before_client_creation(population):
    pin, products = population
    requests = tuple(request(product) for product in products.values())
    def forbidden():
        pytest.fail('Opened reader for incomplete population')
    with pytest.raises(ValueError, match='complete price-eligible'):
        asyncio.run(certify_population_history_async(pin.opening, pin.rule, requests[:1],
            reader_factory=forbidden, concurrency=2))


def test_complete_population_uses_native_pins_and_exact_ranking_contract(population, monkeypatch):
    pin, products = population
    requests = tuple(request(product) for product in products.values())
    reads = []
    class Reader:
        def close(self):
            reads.append('close')
    def certify(reader, scope, fields, parents, **kwargs):
        return products[scope.ticker]
    monkeypatch.setattr(history, 'certify_history_product', certify)
    result = asyncio.run(certify_population_history_async(pin.opening, pin.rule, requests,
        reader_factory=Reader, concurrency=2))
    assert result.source_revision == pin.source_revision
    assert result.rule == pin.rule and dict(result.histories) == dict(pin.histories)
    assert reads == ['close', 'close']


def test_packet_origin_and_duplicates_rejected_before_io(prepared_source):
    store = Store()
    publish(store, prepared_source)
    original = request(consume(store, prepared_source))
    def forbidden():
        pytest.fail('Opened reader for invalid pin packet')
    for requests in ((original, original), (original, replace(original,
            scope=replace(original.scope, listing_id='second', first_bucket=original.scope.first_bucket + 1)))):
        with pytest.raises(ValueError):
            asyncio.run(history.certify_history_pins_async(forbidden, requests, concurrency=2))


def test_fixed_worker_pool_bounds_live_clients(prepared_source, monkeypatch):
    store = Store()
    publish(store, prepared_source)
    product = consume(store, prepared_source)
    from src.market_engine import columnar_history_product as c
    products = {}
    for i in range(6):
        prepared = replace(product.prepared, scope=replace(product.prepared.scope, listing_id=str(i)))
        header = c.history_seal(prepared, product.parents, certified_at=product.header['certified_at'])
        products[str(i)] = replace(product, prepared=prepared, header=header)
    requests = tuple(request(item) for item in products.values())
    lock, barrier = Lock(), Barrier(2)
    active = peak = closed = 0
    class Reader:
        def __init__(self):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
        def close(self):
            nonlocal active, closed
            with lock:
                active -= 1
                closed += 1
    def certify(reader, scope, fields, parents, **kwargs):
        barrier.wait(timeout=5)
        return products[scope.listing_id]
    monkeypatch.setattr(history, 'certify_history_product', certify)
    pins = asyncio.run(history.certify_history_pins_async(Reader, requests, concurrency=2))
    assert len(pins) == 6 and peak == 2 and active == 0 and closed == 2


@pytest.mark.parametrize('phase', ['open', 'read'])
def test_cancelled_worker_drains_and_closes_owned_connection(prepared_source, monkeypatch, phase):
    store = Store()
    publish(store, prepared_source)
    product = consume(store, prepared_source)
    started, release = Event(), Event()
    closed = []
    def block():
        started.set()
        assert release.wait(5)
    class Reader:
        def __init__(self):
            if phase == 'open':
                block()
        def close(self):
            assert release.is_set()
            closed.append(self)
    def certify(*args, **kwargs):
        if phase == 'read':
            block()
        return product
    monkeypatch.setattr(history, 'certify_history_product', certify)
    async def run():
        task = asyncio.create_task(history.certify_history_pins_async(
            Reader, (request(product),), concurrency=1))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert len(closed) == 1
