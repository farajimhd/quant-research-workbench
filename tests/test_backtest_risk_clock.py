"""Risk actions inherit the historical boundary instead of a wall-clock stamp."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.risk_supervisor import AccountRiskState, RiskEvaluation
from src.trading_runtime.runtime import TradingRuntime
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def test_emergency_risk_forwards_its_causal_time_to_both_oms_actions():
    boundary = datetime(2026, 8, 18, 12, 34, tzinfo=timezone.utc)
    runtime = object.__new__(TradingRuntime)
    runtime.order_manager = SimpleNamespace(
        kill_entries=AsyncMock(), emergency_flatten=AsyncMock())
    runtime.portfolio = SimpleNamespace(states={"DU1": SimpleNamespace(
        policy_override=None,
        profile=SimpleNamespace(policy=SimpleNamespace(
            allow_emergency_auto_liquidation=True)))})
    evaluation = RiskEvaluation(
        "DU1", "main", AccountRiskState.EMERGENCY_EXIT,
        ("risk_limit",), {}, boundary)

    asyncio.run(runtime._on_emergency_risk(evaluation))

    runtime.order_manager.kill_entries.assert_awaited_once_with(
        "DU1", reason="continuous_risk_emergency", event_time=boundary)
    runtime.order_manager.emergency_flatten.assert_awaited_once_with(
        "DU1", reason="continuous_risk_emergency", event_time=boundary)


def test_kill_entry_journal_uses_passed_historical_boundary():
    boundary = datetime(2026, 8, 18, 12, 34, tzinfo=timezone.utc)
    manager = object.__new__(OrderManagementEngine)
    group = SimpleNamespace(account_id="DU1", remaining_quantity=1.,
                            group_id="group-1")
    manager._groups = {"group-1": group}
    manager.broker = SimpleNamespace(cancel_order=AsyncMock(return_value={"status": "cancelled"}))
    manager._command_lanes = {}
    manager._record = Mock()
    manager._transition = Mock()
    with patch("src.trading_runtime.order_management._open_entry_roots",
               return_value=[("broker-1", None)]):
        asyncio.run(manager.kill_entries("DU1", reason="risk_limit",
                                         event_time=boundary))

    assert manager._record.call_args.args[:5] == (
        "risk", "kill_entry_order", "broker-1", "DU1", boundary)


def test_numbered_emergency_flatten_keeps_protection_on_rejected_pair():
    boundary = datetime(2026, 8, 18, 12, 34, tzinfo=timezone.utc)
    manager = object.__new__(OrderManagementEngine)
    manager._broker_connected = True
    manager.causal_execution_clock = True
    manager.enforce_wall_clock_quote_freshness = False
    manager.strategy_id = STRATEGY_ID
    manager.strategy_revision = STRATEGY_NUMBER
    manager.policy = SimpleNamespace(maximum_reprice_ticks=2)
    manager.reconcile = AsyncMock()
    manager.kill_entries = AsyncMock()
    manager.cancel_strategy_protection = AsyncMock()
    manager._record = Mock()
    manager.execution_market_data = SimpleNamespace(snapshot=lambda _ticker:
        SimpleNamespace(bid=10., ask=10.01, tick_size=.01,
                        observed_at=boundary))
    manager.broker = SimpleNamespace(
        positions=AsyncMock(return_value=[SimpleNamespace(
            position=10., contractDesc="AAA", raw={}, conid=123)]),
        place_orders=AsyncMock(return_value=[{"error": "Order rejected"}]))

    @asynccontextmanager
    async def lane(_account_id):
        yield

    manager._command_lane = lane
    try:
        asyncio.run(manager.emergency_flatten(
            "DU1", reason="risk_limit", event_time=boundary))
    except RuntimeError as exc:
        assert "two exact simulated broker acknowledgements" in str(exc)
    else:
        raise AssertionError("Rejected emergency OCA pair must stop")
    manager.cancel_strategy_protection.assert_not_awaited()
    manager._record.assert_not_called()
