"""Risk actions inherit the historical boundary instead of a wall-clock stamp."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.risk_supervisor import AccountRiskState, RiskEvaluation
from src.trading_runtime.runtime import TradingRuntime


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
