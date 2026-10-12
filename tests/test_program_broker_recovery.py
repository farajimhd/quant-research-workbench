import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.backend import program_broker_recovery as module
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot


@pytest.mark.parametrize("resolution", [100, 1000])
@pytest.mark.parametrize("movement", [None, "before", "after"])
def test_program_broker_native_empty_checkpoint_restores_and_closes(monkeypatch, resolution, movement):
    day = date(2026, 8, 18)
    config = SimulationConfig(liquidity_resolution_ms=resolution)
    broker = SimulatedBrokerAdapter(["account"], config=config, mode=TradingMode.BACKTEST,
        initial_time=market_day_boundary(day, 0), fixed_bar_mode=True)
    snapshot = broker.checkpoint_state()
    rows = project_broker_match_snapshot(run_id="run", session_date=day,
        checkpoint_sequence=7, boundary_ms=1000, state=snapshot)
    prefix = V4CommittedPrefix("run", 7, "batch", "cursor", "running", ("batch",))
    closed, calls = [], []
    client = SimpleNamespace(close=lambda: closed.append(True))
    def head(*_):
        calls.append(True)
        changed = movement == "before" or movement == "after" and len(calls) == 2
        return replace(prefix, last_sequence=8) if changed else prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    def executions(reader, authority, **scope):
        assert reader is client and authority == prefix
        assert scope == dict(requests_by_coid={}, coid_by_broker_id={}, next_execution_id=1)
        return []
    monkeypatch.setattr(module, "load_v4_broker_executions", executions)
    async def run():
        return await module.recover_program_broker_async(lambda: client, prefix,
            broker=rows, quotes={}, lineages=(), simulation_config=config)
    if movement:
        with pytest.raises(ValueError, match="prefix moved"):
            asyncio.run(run())
    else:
        state = asyncio.run(run())
        restored = SimulatedBrokerAdapter(["account"], config=config, mode=TradingMode.BACKTEST,
            initial_time=market_day_boundary(day, 0), fixed_bar_mode=True)
        restored.restore_checkpoint_state(state)
        actual = restored.checkpoint_state()
        for name in ("cash", "positions", "orders", "next_order_id", "next_execution_id"):
            assert actual[name] == snapshot[name]
    assert closed == [True]
