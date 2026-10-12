"""Common-boundary orchestration; individual SQL readers have separate tests."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.backend import program_checkpoint_recovery as module
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioPolicy
from src.trading_runtime.arte_oms_observation_snapshot import project_shared_oms_observations
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_shared_broker_match_snapshot import snapshot


@pytest.mark.parametrize("failure", [None, "prefix", "head", "session", "clock", "multiple", "cancel"])
def test_parallel_checkpoint_reads_settle_and_keep_one_boundary(monkeypatch, failure):
    broker, contract = snapshot(1000)
    observed = project_shared_oms_observations(run_id="run",
        session_date=date.fromisoformat(broker.snapshot["session_date"]), checkpoint_sequence=7,
        boundary_ms=broker.snapshot["boundary_ms"], groups={})
    batch = "00000000-0000-0000-0000-000000000007"
    prefix = V4CommittedPrefix("run", 7, batch, "cursor", "running", (batch,))
    kazoo = FakeKazoo()
    kazoo.add_listener = lambda listener: None
    session = ManagedKeeperSession(kazoo)
    session._on_state("CONNECTED")
    heads = (module.ManagedSharedBrokerMatchHeadReader(session), module.ManagedSharedOmsObservationHeadReader(session))
    for head, digest in zip(heads, (broker.snapshot["content_hash"], observed.root["content_hash"]), strict=True):
        kazoo.create(head.path("run"), f"1\nrun\n7\n{batch}\n{digest}".encode())
    clients = []
    def factory():
        client = SimpleNamespace(closed=False)
        client.close = lambda: setattr(client, "closed", True)
        clients.append(client)
        return client
    reads = []
    def verified(*args):
        reads.append(True)
        return replace(prefix, last_sequence=8) if failure == "prefix" and len(reads) > 2 else prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", verified)
    monkeypatch.setattr(module, "load_latest_backtest_cursor", lambda *args: dict(run_id="run",
        event_sequence=7, batch_id=batch, session_date=broker.snapshot["session_date"],
        boundary_ms=broker.snapshot["boundary_ms"]))
    profile = PortfolioAccountProfile("account", "account", "backtest", "default", PortfolioPolicy())
    async def run():
        entered, completed, release = [], [], asyncio.Event()
        async def leaf(identity, result):
            entered.append(identity)
            if len(entered) == 3 and failure != "cancel":
                release.set()
            await release.wait()
            completed.append(identity)
            if failure == "multiple" and identity in ("broker", "oms"):
                raise ValueError(identity + " read failed")
            if identity == "portfolio":
                if failure == "head":
                    path = heads[0].path("run")
                    raw, version, owner = kazoo.nodes[path]
                    kazoo.nodes[path] = (raw, version + 1, owner)
                if failure == "session":
                    session._on_state("SUSPENDED")
                    session._on_state("CONNECTED")
            return result
        monkeypatch.setattr(module, "load_attested_shared_broker_snapshot",
            lambda *a, **k: leaf("broker", broker))
        monkeypatch.setattr(module, "load_attested_shared_oms_observations",
            lambda *a, **k: leaf("oms", replace(observed,
                root={**observed.root, "boundary_ms": observed.root["boundary_ms"] + 1000})
                if failure == "clock" else observed))
        monkeypatch.setattr(module, "recover_program_portfolio_async",
            lambda *a, **k: leaf("portfolio", SimpleNamespace(revisions={"account": 7})))
        task = asyncio.create_task(module.recover_program_financial_checkpoint(factory,
            prefix=prefix, broker_contract=contract, broker_head_reader=heads[0],
            observation_head_reader=heads[1], profiles=(profile,), broker_read_concurrency=2))
        if failure == "cancel":
            async def ready():
                while len(entered) < 3:
                    await asyncio.sleep(0)
            await asyncio.wait_for(ready(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not completed
            release.set()
        if failure:
            with pytest.raises((ValueError, RuntimeError, BaseExceptionGroup, asyncio.CancelledError)):
                await task
        else:
            result = await asyncio.wait_for(task, 2)
            assert result.broker is broker and result.observations is observed
            assert result.prefix == prefix
        assert set(completed) == {"broker", "oms", "portfolio"}
    asyncio.run(run())
    assert clients and all(client.closed for client in clients)
