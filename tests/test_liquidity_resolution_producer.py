import json
import re
from dataclasses import replace

import pytest

from src.backend.backtest_market_data import MarketDayUnit, CertifiedMarketDayPlan, ExecutionInterval
from src.backend.backtest_liquidity_price import PriceLevelUnit
from src.trading_runtime.eligible_price_contract import summary_digest
from pipelines.market_sip.events import liquidity_resolution_sql as sql
from pipelines.market_sip.events.liquidity_resolution_producer import CertifiedLiquidityInput, publish_unit


def source():
    uid = "00000000-0000-0000-0000-000000000001"
    market_unit = MarketDayUnit("source", "2026-08-03", "AAPL", "broker_100ms", uid, "c" * 64, 10, "123")
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "source", "d" * 64,
        ("2026-08-03",), ("AAPL",), (market_unit,), (100, 1000), "e" * 64)
    summary = dict(row_count=18, unique_keys=18, eligible_bucket_count=9, row_hash="456")
    price = PriceLevelUnit("2026-08-03", "AAPL", uid,
        "00000000-0000-0000-0000-000000000002", 18, 9, 90., summary_digest(summary), "90")
    return CertifiedLiquidityInput(market, market_unit, price)


class Ownership:
    def __init__(self):
        self.active = True
    def assert_owner(self):
        if not self.active:
            raise RuntimeError("owner lost")


class Store:
    def __init__(self):
        self.coverage = []
        self.writes = []
        self.children = set()
        self.bad_parts = False
        self.bad_parity = False
        self.failed_insert = None
        self.changed_source = False
        self.source_reads = 0


class Client:
    base_url = "http://test"
    def __init__(self, store, *, writer=False):
        self.store = store
        self.writer = writer

    def execute(self, query):
        if isinstance(query, bytes):
            from product_binary_fixture import product_request_as_json
            query = product_request_as_json(query, sql.TABLES)
        s = self.store
        def encode(rows):
            return "\n".join(json.dumps(row, default=str) for row in rows)
        if query == "SELECT currentUser()":
            return "writer" if self.writer else "reader"
        if query == "SHOW GRANTS FINAL":
            user = "writer" if self.writer else "reader"
            reads = [*("arte." + table.name for table in sql.TABLES),
                "arte.liquidity_100ms_v1", "arte.liquidity_execution_price_100ms_v1",
                *("system." + name for name in ("storage_policies", "tables", "columns", "parts", "data_skipping_indices"))]
            return "\n".join([*(f"GRANT SELECT ON {name} TO {user}" for name in reads),
                *(f"GRANT INSERT ON arte.{table.name} TO {user}" for table in sql.TABLES if self.writer)])
        if "FROM system.storage_policies" in query:
            return encode([dict(disks=["live_market_ssd"])])
        if "FROM system.tables" in query:
            if "'liquidity_100ms_v1'" in query:
                return encode([dict(name=name, engine="MergeTree", storage_policy="live_market_ssd")
                               for name in ("liquidity_100ms_v1", "liquidity_execution_price_100ms_v1")])
            return encode([dict(name=t.name, engine="MergeTree", storage_policy="live_market_ssd",
                               partition_key=t.partition, sorting_key=t.order) for t in sql.TABLES])
        if "FROM system.columns" in query:
            return encode([dict(table=t.name, name=name, type=kind) for t in sql.TABLES for name, kind in t.columns])
        if "FROM system.data_skipping_indices" in query:
            return ""
        if "FROM system.parts" in query:
            return encode([dict(table=sql.LIQUIDITY.name, disk_name="default")]) if s.bad_parts else ""
        if query.startswith("INSERT"):
            assert self.writer
            table = re.search(r"INSERT INTO (?:`arte`\.`|arte\.)([a-z0-9_]+)", query).group(1)
            s.writes.append(table)
            if table == sql.COVERAGE.name:
                s.coverage.append(json.loads(query.split("\n", 1)[1]))
            else:
                s.children.add(table)
            if s.failed_insert == table:
                raise RuntimeError("ambiguous INSERT")
            return ""
        if "FROM arte.liquidity_100ms_v1" in query:
            s.source_reads += 1
            return encode([dict(ticker="AAPL", attempt_id=source().market_unit.attempt_id,
                n=11 if s.changed_source and s.source_reads > 1 else 10,
                unique_keys=10, hash="123", resolutions=[100])])
        if "FROM arte.liquidity_execution_price_100ms_v1" in query:
            return encode([dict(row_count=18, unique_keys=18, eligible_bucket_count=9,
                               total_execution_volume=90., row_hash="456")])
        if f"FROM arte.{sql.COVERAGE.name}" in query:
            return encode(s.coverage)
        if "FULL JOIN" in query:
            return encode([dict(bucket_index=50400)]) if s.bad_parity else ""
        if "AS row_hash" in query:
            liquid = f"FROM arte.{sql.LIQUIDITY.name}" in query
            return encode([dict(row_count=1 if liquid else 18, unique_keys=1 if liquid else 18,
                total_execution_volume=90., invalid_rows=0, row_hash="100" if liquid else "200")])
        raise AssertionError("Unexpected bounded query: " + query[:100])


def run(store, ownership=None, code="f" * 64):
    return publish_unit(Client(store), Client(store, writer=True), source(),
                        ownership=ownership or Ownership(), code_hash=code)


def test_children_are_verified_before_coverage_and_exact_resume_skips():
    store = Store()
    assert run(store) == "published"
    assert store.writes == [sql.LIQUIDITY.name, sql.PRICE.name, sql.COVERAGE.name]
    assert run(store) == "skipped_verified"
    assert len(store.writes) == 3
    with pytest.raises(ValueError, match="code identities"):
        run(store, code="a" * 64)
    assert len(store.writes) == 3


@pytest.mark.parametrize("failure", ["bad_parity", "changed_source"])
def test_invalid_or_changed_inputs_never_receive_coverage(failure):
    store = Store()
    setattr(store, failure, True)
    with pytest.raises(ValueError):
        run(store)
    assert store.coverage == [] and sql.COVERAGE.name not in store.writes


def test_wrong_disk_and_lost_owner_stop_before_writers():
    store = Store()
    store.bad_parts = True
    with pytest.raises(ValueError, match="outside live_market_ssd"):
        run(store)
    assert store.writes == []
    store.bad_parts = False
    owner = Ownership()
    owner.active = False
    with pytest.raises(RuntimeError, match="owner lost"):
        run(store, owner)
    assert store.writes == []


def test_ambiguous_write_keeps_orphan_unsealed_and_preserves_all_existing_rows():
    store = Store()
    store.failed_insert = sql.PRICE.name
    with pytest.raises(RuntimeError, match="ambiguous"):
        run(store)
    assert store.coverage == [] and store.children == {sql.LIQUIDITY.name, sql.PRICE.name}
    assert all(not name.startswith(("DELETE", "ALTER")) for name in store.writes)


def test_cancelled_async_publication_drains_unit_and_closes_connections(monkeypatch):
    import asyncio
    import threading
    from pipelines.market_sip.events import liquidity_resolution_producer as producer
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    closed = []
    class OwnedClient:
        def close(self):
            closed.append(self)
    def publish(*args, **kwargs):
        started.set()
        assert release.wait(5)
        finished.set()
        return "published"
    monkeypatch.setattr(producer, "publish_unit", publish)
    async def exercise():
        task = asyncio.create_task(producer.publish_unit_async(OwnedClient, OwnedClient, source()))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        task.cancel()  # Repeated cancellation must still drain the worker.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and len(closed) == 2
    try:
        asyncio.run(exercise())
    finally:
        release.set()
