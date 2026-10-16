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


@pytest.mark.parametrize('fault', [None, 'duplicate', 'order', 'ticker', 'session',
    'clock', 'before', 'after', 'resolution', 'indicator'])
def test_fixed_liquidity_batches_tickers_without_changing_sparse_rows(fault):
    import asyncio
    from tests.test_backtest_market_data import BacktestMarketDataTests
    from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
    from src.backend.backtest_liquidity_resolution import read_fixed_liquidity_cohort_async
    parent = BacktestMarketDataTests()._plan()
    tickers = ('AAA', 'BBB', 'CCC')
    units = tuple(replace(unit, ticker=ticker) for ticker in tickers for unit in parent.units)
    plan = replace(parent, tickers=tickers, units=units)
    prices = PriceLevelPlan(plan.build_id, tuple(PriceLevelUnit(unit.session_date,
        unit.ticker, unit.attempt_id, '00000000-0000-0000-0000-000000000002',
        1, 1, 0., 'a' * 64) for unit in units if unit.stage == 'broker_100ms'), 'b' * 64)
    rows = [dict(session_date=plan.sessions[0], ticker=ticker, boundary_ms=boundary,
        resolution_ms=100, bucket_index=147000 + (boundary - 300100) // 100,
        price_valid=0, execution_price_levels=[[12300, 2.5]])
        for boundary, ticker in ((300100, 'AAA'), (300100, 'BBB'), (300200, 'BBB'))]
    if fault == 'duplicate': rows.insert(1, dict(rows[0]))
    elif fault == 'order': rows.reverse()
    elif fault == 'ticker': rows[0]['ticker'] = 'OTHER'
    elif fault == 'session': rows[0]['session_date'] = '2026-08-19'
    elif fault == 'clock': rows[0]['bucket_index'] += 1
    elif fault == 'before': rows[0].update(boundary_ms=300000, bucket_index=146999)
    elif fault == 'after': rows[-1].update(boundary_ms=300300, bucket_index=147002)
    elif fault == 'resolution': rows[0]['resolution_ms'] = 1000
    elif fault == 'indicator': rows[0].update(price_valid=1, indicator_resolution_ms=1000)
    clients = []
    class Reader:
        def __init__(self):
            self.closed = self.stream_closed = False
            self.queries = []
            clients.append(self)
        def iter_json_each_row(self, query):
            self.queries.append(query)
            assert all(ticker in query for ticker in tickers)
            assert prices.units[0].derivation_attempt_id in query
            try:
                yield from (dict(row) for row in rows)
            finally:
                self.stream_closed = True
        def close(self): self.closed = True
    async def check():
        request = read_fixed_liquidity_cohort_async(Reader, plan, prices,
            after_boundary_ms=300000, through_boundary_ms=300200, concurrency=2)
        if fault:
            with pytest.raises(ExceptionGroup): await request
        else:
            result = await request
            assert tuple(result) == tickers
            assert [row['boundary_ms'] for row in result['AAA']] == [300100]
            assert [row['boundary_ms'] for row in result['BBB']] == [300100, 300200]
            assert result['CCC'] == ()
            assert result['AAA'][0]['execution_price_levels'] == ({'price_int': 12300, 'volume': 2.5},)
            with pytest.raises(TypeError): result['CCC'] = ()
        assert len(clients) == 1 and len(clients[0].queries) == 1
        assert clients[0].closed and clients[0].stream_closed
    asyncio.run(check())


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


def test_broker_read_projection_keeps_parent_units_and_source_identity():
    from tests.test_backtest_market_data import BacktestMarketDataTests
    from src.backend.backtest_liquidity_resolution import fixed_liquidity_read_plan
    parent = BacktestMarketDataTests()._plan()
    view = fixed_liquidity_read_plan(parent)
    assert parent.required_resolutions_ms == (100, 1000)
    assert view.required_resolutions_ms == (100,)
    assert view.token != parent.token
    assert view.units is parent.units and view.tickers == parent.tickers
    assert (view.build_id, view.definition_hash, view.sessions, view.execution_interval) == (
        parent.build_id, parent.definition_hash, parent.sessions, parent.execution_interval)
    assert fixed_liquidity_read_plan(view) is view
    assert fixed_liquidity_read_plan(parent).token == view.token


@pytest.mark.parametrize('resolutions', [(1000,), (100, 100), (1000, 100), (100, 999), (True, 100)])
def test_broker_read_projection_rejects_missing_or_malformed_clock_coverage(resolutions):
    from tests.test_backtest_market_data import BacktestMarketDataTests
    from src.backend.backtest_liquidity_resolution import fixed_liquidity_read_plan
    with pytest.raises(ValueError, match='certified100ms'):
        fixed_liquidity_read_plan(replace(BacktestMarketDataTests()._plan(),
            required_resolutions_ms=resolutions))


def test_broker_read_projection_rejects_a_different_execution_clock():
    from tests.test_backtest_market_data import BacktestMarketDataTests
    from src.backend.backtest_market_data import ExecutionInterval
    from src.backend.backtest_liquidity_resolution import fixed_liquidity_read_plan
    with pytest.raises(ValueError, match='certified100ms'):
        fixed_liquidity_read_plan(replace(BacktestMarketDataTests()._plan(),
            execution_interval=ExecutionInterval.fixed(1000)))


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("full_source", [False, True])
@pytest.mark.parametrize("strategy_row", [False, True])
def test_fixed_liquidity_cohort_uses_native_pinned_query_and_closes_stream(duplicate, full_source, strategy_row):
    import asyncio
    from tests.test_backtest_market_data import BacktestMarketDataTests
    from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
    from src.backend.backtest_liquidity_resolution import read_fixed_liquidity_cohort_async
    parent = BacktestMarketDataTests()._plan()
    plan = parent if full_source else replace(parent, required_resolutions_ms=(100,))
    source = next(unit for unit in plan.units if unit.stage == "broker_100ms")
    prices = PriceLevelPlan(plan.build_id, (PriceLevelUnit(source.session_date, source.ticker,
        source.attempt_id, "00000000-0000-0000-0000-000000000002", 1, 1, 0., "a" * 64),), "b" * 64)
    row = dict(session_date=source.session_date, ticker=source.ticker, boundary_ms=300100,
        resolution_ms=1000 if strategy_row else 100, bucket_index=147000, price_valid=0, execution_price_levels=[])
    clients = []
    class Reader:
        def __init__(self):
            self.closed = self.stream_closed = False
            clients.append(self)
        def iter_json_each_row(self, query):
            assert query.startswith("SELECT") and "execution_price_levels" in query
            assert source.attempt_id in query and prices.units[0].derivation_attempt_id in query
            assert 'resolution_ms IN (1000)' not in query and 'resolution_ms=1000' not in query
            try:
                yield dict(row)
                if duplicate:
                    yield dict(row)
            finally:
                self.stream_closed = True
        def close(self):
            self.closed = True
    async def run_test():
        request = read_fixed_liquidity_cohort_async(Reader, plan, prices,
            after_boundary_ms=300000, through_boundary_ms=300200, concurrency=2)
        if duplicate or strategy_row:
            with pytest.raises(ExceptionGroup, match="TaskGroup"):
                await request
        else:
            result = await request
            assert tuple(result) == (source.ticker,)
            assert len(result[source.ticker]) == 1
            assert result[source.ticker][0]["execution_price_levels"] == ()
        assert len(clients) == 1 and clients[0].closed and clients[0].stream_closed
        wrong_prices = replace(prices, units=(replace(prices.units[0], source_attempt_id=
            "00000000-0000-0000-0000-000000000003"),))
        with pytest.raises(ValueError, match="liquidity attempts"):
            await read_fixed_liquidity_cohort_async(Reader, plan, wrong_prices,
                after_boundary_ms=300000, through_boundary_ms=300200, concurrency=2)
        assert len(clients) == 1
    asyncio.run(run_test())
