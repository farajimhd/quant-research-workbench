import asyncio
from dataclasses import replace

import pytest

from src.backend.program_runtime_recovery import build_recovered_program_runtime
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime import arte_portfolio_recovery as reader
from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.domain import TradingMode, InstrumentContract
from src.trading_runtime.portfolio import profiles_for_runtime, PortfolioSyncState
from src.trading_runtime.runtime import RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient, _state
from tests.test_arte_portfolio_recovery import AT
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize("resolution", [100, 1000])
def test_shared_runtime_initializes_from_real_normalized_cold_portfolio(monkeypatch, resolution):
    _, template = definition_and_actor()
    assignment = replace(template.assignments()[0], account_id="account-id")
    strategy = AssignedProgramStrategy(template.program_release, (assignment,), {"assignment": "listing"})
    profile = replace(profiles_for_runtime(("account-id",), mode="backtest")[0], account_key="account-key")
    client = SnapshotClient()
    raw = _state()
    raw.update(disabled_strategy_allocations=[], pending_operational_commands=[])
    publish_portfolio_snapshot(client, run_id="cold-run", account_id="account-id",
        state_revision=7, snapshot_at=AT, state=raw)
    monkeypatch.setattr(reader, "load_typed_run_context", lambda *_: {"account_ids": ("account-id",)})
    cold = reader.recover_portfolio_engine_state(client, run_id="cold-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    journal = BacktestMemoryJournal(run_id="cold-run", initial_sequence=7)
    config = RunConfig(RunMode.BACKTEST, strategy.strategy_id, strategy.revision,
        ("account-id",), AT.date(), run_id="cold-run", write_progress_checkpoints=False)
    simulation = SimulationConfig(initial_cash=1000., liquidity_resolution_ms=resolution)
    broker = SimulatedBrokerAdapter(["account-id"], simulation, mode=TradingMode.BACKTEST,
        initial_time=AT, fixed_bar_mode=True)
    oms = TypedOmsActorImage("cold-run", strategy.strategy_id, strategy.revision,
        {}, {}, {}, {}, frozenset())
    planner = RuntimeIbkrStrategyOrderPlanner({"ABC": InstrumentContract("listing", 1, "ABC", "STK", "USD")},
        strategy_id=strategy.strategy_id, strategy_revision=strategy.revision, run_id="cold-run")
    kwargs = dict(portfolio_recovery=cold, oms_image=oms, broker_image=broker.checkpoint_state(),
        simulation_config=simulation, completed_at=AT, intent_planner=planner, strategy_configuration={})
    with pytest.raises(ValueError, match="financial scope"):
        build_recovered_program_runtime(replace(config, run_id="foreign"), strategy, journal, **kwargs)
    runtime = build_recovered_program_runtime(config, strategy, journal, **kwargs)
    async def run():
        try:
            await runtime.initialize(record_lifecycle=False)
            state = runtime.portfolio.states["account-id"]
            assert state.sync_state is PortfolioSyncState.SYNCHRONIZED
            assert state.ledger.cashbalance == 1000.
            assert state.snapshot_id == "broker-1"
            assert runtime.order_manager is not None and runtime._typed_oms_recovered
            assert not runtime.order_manager._groups
            assert cold.states["account-id"].ledger is None
            assert cold.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
        finally:
            await runtime.finish()
    asyncio.run(run())
