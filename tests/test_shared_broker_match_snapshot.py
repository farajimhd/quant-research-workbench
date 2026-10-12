"""Central broker snapshots preserve the real simulator's scalar bit patterns."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import re
from threading import Barrier, Lock, Event
from types import SimpleNamespace

import pytest

from src.trading_runtime import arte_broker_match_snapshot as shared
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.ibkr_schema import OrderRequest
from tests.test_broker_liquidity_resolution import second_bar
from tests.test_simulated_broker_liquidity_bar import START, bar
from src.backend.backtest_market_data import market_day_boundary


def snapshot(resolution):
    config = SimulationConfig(liquidity_resolution_ms=resolution, marketable_liquidity_participation=1)
    contract = BacktestBrokerContract(project_broker_contract("run", config, liquidity_plan_hash="c" * 64), config.initial_cash)
    broker = SimulatedBrokerAdapter(["account"], config=config, mode=RunMode.BACKTEST,
        initial_time=START, fixed_bar_mode=True)
    async def fill():
        await broker.initialize()
        await broker.place_orders("account", [OrderRequest(acctId="account", conid=1,
            cOID="shared-snapshot", ticker="AAPL", orderType="MKT", side="BUY", quantity=10)])
        at = START + timedelta(seconds=1)
        fills = await broker.on_liquidity_bar(second_bar(at) if resolution == 1000 else bar(at), at=at)
        assert fills and sum(row.commission for row in fills) >= 1
        boundary = int((at - market_day_boundary(at.date(), 0)).total_seconds() * 1000)
        rows = shared.project_shared_broker_snapshot(run_id="run", session_date=at.date(),
            checkpoint_sequence=7, boundary_ms=boundary, state=broker.checkpoint_state(),
            simulation_config=config, broker_contract=contract)
        return rows
    return asyncio.run(fill()), contract


class Store:
    def __init__(self, rows):
        self.rows = dict(zip((t.name for t in shared.TABLES),
            ((rows.snapshot,), rows.accounts, rows.positions, rows.open_orders, rows.tickers, rows.marks), strict=True))
        self.closed = 0
        self.lock = Lock()
        self.barrier = Barrier(2)
        self.initial_children = 0
        self.bad_disk = False

    def client(self):
        store = self
        class Client:
            def close(self):
                with store.lock:
                    store.closed += 1
            def execute(self, sql):
                def encode(rows):
                    return "\n".join(json.dumps(dict(row), default=str) for row in rows)
                tables = shared.TABLES
                if "system.storage_policies" in sql:
                    return encode([dict(disks=["live_market_ssd"])])
                if "system.tables" in sql:
                    return encode([dict(name=t.name, engine="MergeTree", storage_policy="live_market_ssd",
                        partition_key=t.partition, sorting_key=t.order) for t in tables])
                if "system.columns" in sql:
                    return encode([dict(table=t.name, name=name, type=kind) for t in tables for name, kind in t.columns])
                if "system.parts" in sql:
                    return encode([dict(table=shared.ROOT.name, disk_name="default")]) if store.bad_disk else ""
                if "system.data_skipping_indices" in sql:
                    return ""
                name = re.search(r"FROM arte\.([a-z0-9_]+)", sql).group(1)
                assert name in store.rows and "strategy_one" not in sql
                if name != shared.ROOT.name:
                    with store.lock:
                        store.initial_children += 1
                        wait = store.initial_children <= 2
                    if wait:
                        store.barrier.wait(timeout=5)
                # Model ClickHouse's quoted UInt64 transport without converting
                # any bit pattern through a floating-point number.
                kinds = dict(next(t for t in tables if t.name == name).columns)
                return encode([{key: str(value) if kinds[key] == "UInt64" else value
                    for key, value in row.items()} for row in store.rows[name]])
        return Client()


@pytest.mark.parametrize("resolution", [100, 1000])
def test_central_projection_concurrent_cold_read_and_contract_binding(resolution):
    rows, contract = snapshot(resolution)
    store = Store(rows)
    actual = asyncio.run(shared.load_shared_broker_snapshot(store.client, concurrency=2,
        run_id="run", checkpoint_sequence=7, broker_contract=contract,
        expected_content_hash=rows.snapshot["content_hash"]))
    assert actual == rows and store.closed == 6 and store.initial_children == 5
    with pytest.raises(TypeError):
        rows.snapshot["liquidity_resolution_ms"] = 100
    changed = BacktestBrokerContract(project_broker_contract("run",
        replace(contract.config, commission_per_share=.01), liquidity_plan_hash="c" * 64), contract.initial_cash)
    with pytest.raises(ValueError, match="pinned scope"):
        shared.verify_shared_broker_snapshot(rows, broker_contract=changed, run_id="run",
            checkpoint_sequence=7, expected_content_hash=rows.snapshot["content_hash"])
    corrupt = replace(rows, accounts=({**rows.accounts[0], "cash_f64_bits": rows.accounts[0]["cash_f64_bits"] ^ 1},))
    with pytest.raises(ValueError, match="child hash"):
        shared.verify_shared_broker_snapshot(corrupt, broker_contract=contract, run_id="run",
            checkpoint_sequence=7, expected_content_hash=rows.snapshot["content_hash"])
    assert all("strategy_one" not in t.name and "live_market_ssd" in t.ddl() for t in shared.TABLES)


@pytest.mark.parametrize("resolution", [100, 1000])
def test_shared_rows_stage_native_broker_reconstruction_without_start(monkeypatch, resolution):
    from src.backend import program_broker_recovery as recovery
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    config = SimulationConfig(liquidity_resolution_ms=resolution)
    contract = BacktestBrokerContract(project_broker_contract("run", config, liquidity_plan_hash="c" * 64), config.initial_cash)
    native = SimulatedBrokerAdapter(["account"], config=config, mode=RunMode.BACKTEST,
        initial_time=START, fixed_bar_mode=True)
    original = native.checkpoint_state()
    boundary = int((START - market_day_boundary(START.date(), 0)).total_seconds() * 1000) + 1000
    rows = shared.project_shared_broker_snapshot(run_id="run", session_date=START.date(), checkpoint_sequence=7,
        boundary_ms=boundary, state=original, simulation_config=config, broker_contract=contract)
    prefix = V4CommittedPrefix("run", 7, "batch", "cursor", "running", ("batch",))
    closed = []
    monkeypatch.setattr(recovery, "load_verified_v4_prefix", lambda *args: prefix)
    monkeypatch.setattr(recovery, "load_v4_broker_executions", lambda *args, **kwargs: [])
    state = asyncio.run(recovery.recover_shared_program_broker_async(
        lambda: SimpleNamespace(close=lambda: closed.append(True)), prefix,
        broker=rows, broker_contract=contract, expected_snapshot_hash=rows.snapshot["content_hash"],
        quotes={}, lineages=(), simulation_config=config))
    inert = SimulatedBrokerAdapter(["account"], config=config, mode=RunMode.BACKTEST,
        initial_time=START, fixed_bar_mode=True)
    inert.restore_checkpoint_state(state)
    restored = inert.checkpoint_state()
    assert all(restored[name] == original[name] for name in ("cash", "positions", "orders", "next_order_id", "next_execution_id"))
    assert closed == [True]


@pytest.mark.parametrize("failure", ["rows", "bytes", "children"])
def test_central_read_bounds_and_failures_close_every_owned_connection(failure):
    rows, contract = snapshot(1000)
    store = Store(rows)
    scope = dict(run_id="run", checkpoint_sequence=7, broker_contract=contract,
        expected_content_hash=rows.snapshot["content_hash"])
    if failure == "rows":
        scope["maximum_rows"] = 1
    elif failure == "bytes":
        scope["maximum_bytes"] = 1
    else:
        store.rows[shared.ACCOUNT.name] = ()
    with pytest.raises(ValueError, match="budget|missing or duplicate"):
        asyncio.run(shared.load_shared_broker_snapshot(store.client, concurrency=2, **scope))
    assert store.closed == (6 if failure == "children" else 1)


def test_cancelled_central_read_settles_concurrent_children_and_closes():
    rows, contract = snapshot(1000)
    store = Store(rows)
    entered, release = Event(), Event()
    def client():
        reader = store.client()
        native = reader.execute
        def execute(sql):
            if "FROM arte." in sql and shared.ROOT.name not in sql:
                entered.set()
                assert release.wait(timeout=5)
            return native(sql)
        reader.execute = execute
        return reader
    async def run():
        job = asyncio.create_task(shared.load_shared_broker_snapshot(client, concurrency=2,
            run_id="run", checkpoint_sequence=7, broker_contract=contract,
            expected_content_hash=rows.snapshot["content_hash"]))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            job.cancel()
            await asyncio.sleep(0)
            job.cancel()
            await asyncio.sleep(0)
            assert not job.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert store.closed == 6
    asyncio.run(run())


def test_central_snapshot_read_fails_on_backup_disk_before_children():
    rows, contract = snapshot(1000)
    store = Store(rows)
    store.bad_disk = True
    with pytest.raises(ValueError, match="SSD|live_market_ssd"):
        asyncio.run(shared.load_shared_broker_snapshot(store.client, concurrency=2,
            run_id="run", checkpoint_sequence=7, broker_contract=contract,
            expected_content_hash=rows.snapshot["content_hash"]))
    assert store.closed == 1 and store.initial_children == 0
