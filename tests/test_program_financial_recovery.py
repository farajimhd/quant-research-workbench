import asyncio
from dataclasses import replace
from threading import Event

import pytest

from src.backend import program_financial_recovery as module
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.portfolio import PortfolioSyncState
from tests.test_arte_portfolio_recovery import _client, AT


@pytest.mark.parametrize("movement", [None, "before", "after"])
def test_program_financial_read_uses_normalized_snapshot_and_closes(monkeypatch, movement):
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    calls = []
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True), raising=False)

    def head(reader, run_id):
        assert reader is client and run_id == prefix.run_id
        calls.append(True)
        changed = movement == "before" or (movement == "after" and len(calls) == 2)
        return replace(prefix, last_sequence=8) if changed else prefix

    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    async def run():
        return await module.recover_program_portfolio_async(lambda: client, prefix,
            profiles=(profile,), cutoff_at=AT)
    if movement:
        with pytest.raises(ValueError, match="prefix changed"):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result.revisions == {"account-id": 7}
        assert result.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
        assert result.last_filled_by_reservation == {"reservation-1": 2}
    assert closed == [True]


def test_cancelled_program_financial_read_drains_owned_client(monkeypatch):
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    entered, release, closed = Event(), Event(), Event()
    monkeypatch.setattr(client, "close", closed.set, raising=False)
    def head(reader, run_id):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Synthetic reader was not released")
        return prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    async def run():
        task = asyncio.create_task(module.recover_program_portfolio_async(lambda: client,
            prefix, profiles=(profile,), cutoff_at=AT))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    asyncio.run(run())


@pytest.mark.parametrize("mismatch", [None, "clock", "accounts", "prefix"])
def test_program_oms_joins_native_empty_broker_and_observation_checkpoint(mismatch):
    from datetime import date, timedelta
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.domain import TradingMode
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from src.trading_runtime.strategy_one_oms_observation_snapshot import project_oms_observation_snapshot
    day = date(2026, 8, 18)
    at = market_day_boundary(day, 1000)
    broker = SimulatedBrokerAdapter(["account"], initial_time=market_day_boundary(day, 0),
        fixed_bar_mode=True, mode=TradingMode.BACKTEST)
    rows = project_broker_match_snapshot(run_id="run", session_date=day,
        checkpoint_sequence=7, boundary_ms=1000, state=broker.checkpoint_state())
    observed = project_oms_observation_snapshot(run_id="run", session_date=day,
        checkpoint_sequence=7, boundary_ms=1000, groups={})
    prefix = V4CommittedPrefix("run", 7, "batch", "cursor", "running", ("batch",))
    history = CompleteProtectionHistory("run", 7, ("batch",), ())
    kwargs = dict(lineages=(), protection=history, broker=rows, observations=observed,
        strategy_id="declared-program", strategy_revision=1, cutoff_at=at,
        account_ids=("account",))
    if mismatch == "clock":
        kwargs["cutoff_at"] += timedelta(seconds=1)
    elif mismatch == "accounts":
        kwargs["account_ids"] = ("other",)
    elif mismatch == "prefix":
        prefix = replace(prefix, last_sequence=8)
    if mismatch:
        with pytest.raises(ValueError, match="prefix|scope differs"):
            module.reconstruct_program_oms_image(prefix, **kwargs)
    else:
        image = module.reconstruct_program_oms_image(prefix, **kwargs)
        assert image.strategy_id == "declared-program"
        assert image.groups == {} and image.group_by_broker_id == {}


def test_program_oms_rebuilds_working_group_from_native_broker_checkpoint():
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.domain import TradingMode
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from src.trading_runtime.strategy_one_oms_observation_snapshot import project_oms_observation_snapshot
    from tests.test_arte_oms_actor_restore import _source, RUN, AT as OMS_AT
    lineage, history = _source()
    broker = SimulatedBrokerAdapter(["DU1"], initial_time=market_day_boundary(OMS_AT.date(), 0),
        fixed_bar_mode=True, mode=TradingMode.BACKTEST)
    request = lineage.orders[0]
    async def place():
        await broker.initialize()
        await broker.place_orders("DU1", [request])
    asyncio.run(place())
    state = broker.checkpoint_state()
    rows = project_broker_match_snapshot(run_id=RUN, session_date=OMS_AT.date(),
        checkpoint_sequence=7, boundary_ms=300000, state=state)
    identity = rows.open_orders[0]["broker_order_id"]
    group = {**lineage.state.group, "strategy_id": "declared-program"}
    binding = {**lineage.state.broker_bindings[0], "broker_order_id": identity}
    lineage = replace(lineage, state=replace(lineage.state, group=group, broker_bindings=(binding,)))
    fingerprint = ("working", "Submitted", 0., 5., 0., 10., 0., "", "", "")
    from types import SimpleNamespace
    observed = project_oms_observation_snapshot(run_id=RUN, session_date=OMS_AT.date(),
        checkpoint_sequence=7, boundary_ms=300000,
        groups={"group-1": SimpleNamespace(broker_order_ids=[identity],
            broker_order_state_fingerprints={identity: fingerprint})})
    prefix = V4CommittedPrefix(RUN, 7, "batch-1", "cursor", "running", ("batch-1",))
    image = module.reconstruct_program_oms_image(prefix, lineages=(lineage,), protection=history,
        broker=rows, observations=observed, strategy_id="declared-program", strategy_revision=1,
        cutoff_at=OMS_AT, account_ids=("DU1",))
    assert image.group_by_broker_id == {identity: "group-1"}
    assert image.groups["group-1"].broker_order_state_fingerprints[identity] == fingerprint
