from dataclasses import replace
import json

import pytest

from test_liquidity_resolution_producer import Client, Store, run, source
from pipelines.market_sip.events import liquidity_resolution_sql as sql
from src.backend.backtest_liquidity_resolution import (
    certify_liquidity_resolution, liquidity_window_sql, read_liquidity_window,
)


def certified(store):
    return certify_liquidity_resolution(Client(store), source().unit,
        producer_code_hash="f" * 64, expected_execution_volume=90.)


def test_reader_requires_seal_parents_children_and_select_only_grants():
    store = Store()
    with pytest.raises(ValueError, match="missing or duplicate"):
        certified(store)
    run(store)
    seal = certified(store)
    assert seal.resolution_ms == 1000 and seal.liquidity_row_count == 1
    assert len(store.writes) == 3  # The reader never adds writes.
    with pytest.raises(ValueError, match="SELECT-only"):
        certify_liquidity_resolution(Client(store, writer=True), source().unit,
            producer_code_hash="f" * 64, expected_execution_volume=90.)
    with pytest.raises(ValueError, match="parents/code"):
        certify_liquidity_resolution(Client(store), replace(source().unit,
            source_market_unit_hash="a" * 64), producer_code_hash="f" * 64,
            expected_execution_volume=90.)
    store.bad_parity = True
    with pytest.raises(ValueError, match="bucket volumes"):
        certified(store)


def test_reader_rejects_duplicate_and_tampered_seals():
    store = Store()
    run(store)
    original = dict(store.coverage[0])
    store.coverage.append(original)
    with pytest.raises(ValueError, match="duplicate"):
        certified(store)
    store.coverage.pop()
    store.coverage[0]["total_execution_volume"] = 91.
    with pytest.raises(ValueError, match="hash differs"):
        certified(store)


def test_window_is_bounded_driven_by_liquidity_and_keeps_quote_only_rows():
    store = Store()
    run(store)
    seal = certified(store)
    query = liquidity_window_sql(seal, first_bucket=50400, stop_bucket=50656)
    assert "LEFT JOIN" in query and "LIMIT 257" in query
    assert "liquidity_bars_v2" in query and "liquidity_execution_price_bars_v2" in query
    assert "source_attempt_id=" in query and seal.derivation_attempt_id in query
    with pytest.raises(ValueError, match="256"):
        liquidity_window_sql(seal, first_bucket=50400, stop_bucket=50657)
    class WindowReader:
        def __init__(self, rows):
            self.rows = rows
        def execute(self, query):
            assert query.startswith("SELECT")
            return "\n".join(json.dumps(row) for row in self.rows)
    row = {name: 0 for name, _ in sql.LIQUIDITY.columns}
    row.update(ticker="AAPL", resolution_ms=1000, bucket_index=50400,
        source_build_id=seal.source.build_id, session_date=seal.source.session_date.isoformat(),
        source_attempt_id=seal.source.source_attempt_id,
        derivation_attempt_id=seal.derivation_attempt_id, execution_price_levels=[])
    result = read_liquidity_window(WindowReader([row]), seal,
                                  first_bucket=50400, stop_bucket=50402)
    assert len(result) == 1 and result[0]["execution_price_levels"] == ()
    with pytest.raises(ValueError, match="ordering"):
        read_liquidity_window(WindowReader([row, row]), seal,
                              first_bucket=50400, stop_bucket=50402)


@pytest.mark.parametrize("operation", ["read", "certify"])
def test_owned_async_reader_drains_and_closes_before_cancellation_returns(monkeypatch, operation):
    import asyncio
    from threading import Event
    from src.backend import backtest_liquidity_resolution as module
    entered, release, closed = Event(), Event(), Event()
    class Reader:
        def close(self):
            closed.set()
    def blocked(reader, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return ()
    monkeypatch.setattr(module, "read_liquidity_window" if operation == "read" else "certify_liquidity_resolution", blocked)
    async def run_test():
        if operation == "read":
            task = asyncio.create_task(module.read_liquidity_window_async(Reader, None, first_bucket=50400, stop_bucket=50401))
        else:
            task = asyncio.create_task(module.certify_liquidity_resolution_async(Reader, None))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not closed.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set()
    asyncio.run(run_test())


@pytest.mark.parametrize("failure", [False, True])
def test_liquidity_cohort_bounds_concurrent_clients_and_closes_every_reader(monkeypatch, failure):
    import asyncio
    from threading import Event, Lock
    from src.backend import backtest_liquidity_resolution as module
    store = Store()
    run(store)
    seal = certified(store)
    certificates = tuple(replace(seal, source=replace(seal.source, ticker=ticker)) for ticker in ("A", "B", "C", "D"))
    lock, entered, release = Lock(), Event(), Event()
    active = peak = opened = closed = 0
    class Reader:
        def __init__(self):
            nonlocal active, peak, opened
            with lock:
                active += 1
                opened += 1
                peak = max(peak, active)
                if active == 2:
                    entered.set()
        def close(self):
            nonlocal active, closed
            with lock:
                active -= 1
                closed += 1
    def read(reader, certificate, **kwargs):
        assert release.wait(5)
        if failure and certificate.source.ticker == "B":
            raise ValueError("injected sealed liquidity read failure")
        return ()  # Certified empty windows remain explicit.
    monkeypatch.setattr(module, "read_liquidity_window", read)
    async def run_test():
        task = asyncio.create_task(module.read_liquidity_cohort_async(Reader, certificates,
            first_bucket=50400, stop_bucket=50402, concurrency=2))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert peak == 2 and opened == 2
        finally:
            release.set()
        if failure:
            with pytest.raises(ExceptionGroup, match="TaskGroup"):
                await task
        else:
            result = await task
            assert tuple(result) == ("A", "B", "C", "D")
            assert all(rows == () for rows in result.values())
            assert opened == 4
        assert closed == opened and active == 0 and peak == 2
        before = opened
        with pytest.raises(ValueError, match="256"):
            await module.read_liquidity_cohort_async(Reader, certificates,
                first_bucket=50400, stop_bucket=50657, concurrency=2)
        with pytest.raises(ValueError, match="unique tickers"):
            await module.read_liquidity_cohort_async(Reader, certificates[:1] * 2,
                first_bucket=50400, stop_bucket=50402, concurrency=2)
        assert opened == before
    asyncio.run(run_test())
