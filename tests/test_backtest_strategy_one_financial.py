"""Strategy 1 financial admission follows broker/OMS state after a boundary."""
import asyncio
from types import SimpleNamespace

import pytest

from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_view
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)


def _assignment():
    return StrategyAssignment(
        "assignment-1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})


def _group(*, state=OrderManagementState.WORKING, filled=0., closed=False,
           action="enter_long"):
    return SimpleNamespace(
        group_id="group-1", account_id="DU1", assignment_id="assignment-1",
        ticker="AAA", action=action, state=state, filled_quantity=filled,
        entry_submission_closed=closed)


def test_financial_view_uses_exact_position_and_live_oms_state():
    broker = SimpleNamespace(positions=lambda _account: _positions())
    async def _positions():
        return [SimpleNamespace(conid=123, contractDesc="AAA", position=5.),
                SimpleNamespace(conid=456, contractDesc="AAA", position=99.)]
    manager = SimpleNamespace(snapshots=lambda: [_group(filled=5., closed=True)])
    view = asyncio.run(read_strategy_one_financial_view(
        _assignment(), broker, manager))
    assert view.position_quantity == 5.
    assert view.completed_entries == 1
    assert not view.pending_entry and not view.pending_exit


def test_financial_view_retains_pending_entry_and_rejects_mismatched_group():
    async def positions(_account):
        return []
    broker = SimpleNamespace(positions=positions)
    manager = SimpleNamespace(snapshots=lambda: [_group()])
    assert asyncio.run(read_strategy_one_financial_view(
        _assignment(), broker, manager)).pending_entry
    manager.snapshots = lambda: [_group(), _group()]
    with pytest.raises(RuntimeError, match="repeated"):
        asyncio.run(read_strategy_one_financial_view(
            _assignment(), broker, manager))
    wrong = _group()
    wrong.ticker = "BBB"
    manager.snapshots = lambda: [wrong]
    with pytest.raises(RuntimeError, match="differs"):
        asyncio.run(read_strategy_one_financial_view(
            _assignment(), broker, manager))
