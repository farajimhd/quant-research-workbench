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


def test_historical_portfolio_revision_is_exact_inside_full_verified_prefix(monkeypatch):
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 9, "batch", "source", "running", ("batch",))
    closed, reads = [], []
    monkeypatch.setattr(client, "close", lambda: closed.append(True), raising=False)
    def head(actual, run_id):
        assert actual is client and run_id == prefix.run_id
        reads.append(prefix.last_sequence)
        return prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    async def run():
        return await module.recover_program_portfolio_async(lambda: client, prefix,
            profiles=(profile,), cutoff_at=AT, through_sequence=7)
    image = asyncio.run(run())
    assert image.revisions == {"account-id": 7}
    assert image.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
    assert prefix.last_sequence == 9 and reads == [9, 9] and closed == [True]
    for invalid in (0, 10, True):
        with pytest.raises(ValueError, match="historical revision"):
            asyncio.run(module.recover_program_portfolio_async(lambda: client, prefix,
                profiles=(profile,), cutoff_at=AT, through_sequence=invalid))
    assert closed == [True]


def test_program_portfolio_rejects_earlier_snapshot_at_same_revision(monkeypatch):
    from datetime import timedelta
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True), raising=False)
    monkeypatch.setattr(module, "load_verified_v4_prefix", lambda *_: prefix)
    async def run():
        return await module.recover_program_portfolio_async(lambda: client, prefix,
            profiles=(profile,), cutoff_at=AT + timedelta(seconds=1))
    with pytest.raises(ValueError, match="completed source clock"):
        asyncio.run(run())
    assert closed == [True]


def test_program_portfolio_reads_each_exact_account_snapshot_once(monkeypatch):
    from src.trading_runtime import arte_portfolio_recovery
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    reads = []
    original = arte_portfolio_recovery.load_portfolio_snapshot
    def snapshot(*args, **kwargs):
        reads.append((kwargs["account_id"], kwargs["state_revision"]))
        return original(*args, **kwargs)
    monkeypatch.setattr(arte_portfolio_recovery, "load_portfolio_snapshot", snapshot)
    monkeypatch.setattr(module, "load_verified_v4_prefix", lambda *_: prefix)
    monkeypatch.setattr(client, "close", lambda: None, raising=False)
    image = asyncio.run(module.recover_program_portfolio_async(lambda: client, prefix,
        profiles=(profile,), cutoff_at=AT))
    assert reads == [("account-id", 7)]
    assert image.revisions == {"account-id": 7}


def test_program_portfolio_constructs_shared_actor_without_mutating_cold_evidence(monkeypatch):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.domain import TradingMode
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    cold = recover_portfolio_engine_state(client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    journal = BacktestMemoryJournal(run_id="live-run")
    actor = module.build_program_portfolio_engine(cold, journal=journal,
        strategy_id="declared-program", strategy_revision=1, event_clock=lambda: AT)
    assert actor._typed_backtest_recovery
    assert actor.states["account-id"] is not cold.states["account-id"]
    assert actor.reservations["reservation-1"] is not cold.reservations["reservation-1"]
    broker = SimulatedBrokerAdapter(["account-id"], mode=TradingMode.BACKTEST,
        initial_time=AT, fixed_bar_mode=True)
    async def synchronize():
        await broker.initialize()
        await actor.synchronize(broker)
    asyncio.run(synchronize())
    assert actor.states["account-id"].ledger is not None
    assert actor.states["account-id"].summary is not None
    assert cold.states["account-id"].ledger is None
    assert cold.states["account-id"].summary is None
    assert cold.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
    with pytest.raises(ValueError, match="blocked cold"):
        module.build_program_portfolio_engine(replace(cold, states=actor.states),
            journal=journal, strategy_id="declared-program", strategy_revision=1,
            event_clock=lambda: AT)


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
        later_prefix = replace(prefix, last_sequence=9)
        historical = module.reconstruct_program_oms_image(later_prefix, through_sequence=7, **kwargs)
        assert historical == image
        assert later_prefix.last_sequence == 9
        with pytest.raises(ValueError, match="shared committed prefix"):
            module.reconstruct_program_oms_image(later_prefix, through_sequence=8, **kwargs)


@pytest.mark.parametrize("shared_observations", [False, True])
def test_program_oms_rebuilds_working_group_from_native_broker_checkpoint(shared_observations):
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
    import math
    fingerprint = ("working", "Submitted", 0., 5.,
        math.nextafter(0.001, 1.) if shared_observations else 0., 10., 0., "", "", "")
    from types import SimpleNamespace
    if shared_observations:
        from src.trading_runtime.arte_oms_observation_snapshot import project_shared_oms_observations
        projector = project_shared_oms_observations
    else:
        projector = project_oms_observation_snapshot
    observed = projector(run_id=RUN, session_date=OMS_AT.date(),
        checkpoint_sequence=7, boundary_ms=300000,
        groups={"group-1": SimpleNamespace(broker_order_ids=[identity],
            broker_order_state_fingerprints={identity: fingerprint})})
    prefix = V4CommittedPrefix(RUN, 7, "batch-1", "cursor", "running", ("batch-1",))
    image = module.reconstruct_program_oms_image(prefix, lineages=(lineage,), protection=history,
        broker=rows, observations=observed, strategy_id="declared-program", strategy_revision=1,
        cutoff_at=OMS_AT, account_ids=("DU1",))
    assert image.group_by_broker_id == {identity: "group-1"}
    assert image.groups["group-1"].broker_order_state_fingerprints[identity] == fingerprint
    historical = module.reconstruct_program_oms_image(replace(prefix, last_sequence=9),
        lineages=(lineage,), protection=history, broker=rows, observations=observed,
        strategy_id="declared-program", strategy_revision=1, cutoff_at=OMS_AT,
        account_ids=("DU1",), through_sequence=7)
    from src.trading_runtime.arte_oms_projection import freeze_oms_group
    assert freeze_oms_group(historical.groups["group-1"]) == freeze_oms_group(image.groups["group-1"])
    assert historical.group_by_broker_id == image.group_by_broker_id
    assert historical.group_by_client_id == image.group_by_client_id
    assert historical.groups["group-1"].broker_order_state_fingerprints == image.groups["group-1"].broker_order_state_fingerprints
