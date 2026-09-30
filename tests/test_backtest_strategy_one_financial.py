"""Strategy 1 financial admission follows broker/OMS state after a boundary."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend.backtest_strategy_one_financial import (
    read_strategy_one_financial_view, read_strategy_one_financial_views,
)
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)


def _assignment():
    return StrategyAssignment(
        "assignment-1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})


def _group(*, state=OrderManagementState.WORKING, filled=0., closed=False,
           action="enter_long", group_id="group-1"):
    return SimpleNamespace(
        group_id=group_id, account_id="DU1", assignment_id="assignment-1",
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
    assert view.current_purchase_groups == 1


def test_financial_view_prefers_exact_broker_quantity_without_account_projection():
    requested = []
    def exact(account_id, conid, ticker):
        requested.append((account_id, conid, ticker))
        return 5.0
    async def forbidden(_account_id):
        pytest.fail("Strategy 1 projected the full broker position account")
    broker = SimpleNamespace(position_quantity=exact, positions=forbidden)
    manager = SimpleNamespace(snapshots=lambda: [])
    view = asyncio.run(read_strategy_one_financial_view(
        _assignment(), broker, manager))
    assert view.position_quantity == 5.0
    assert requested == [("DU1", 123, "AAA")]


def test_financial_view_counts_add_groups_not_partial_fills():
    async def positions(_account):
        return [SimpleNamespace(conid=123, contractDesc="AAA", position=7.)]

    groups = [
        _group(filled=5., closed=True),
        _group(action="add_long", group_id="add-1", filled=2.,
               state=OrderManagementState.PARTIALLY_FILLED, closed=True),
    ]
    manager = SimpleNamespace(snapshots=lambda: groups)
    broker = SimpleNamespace(positions=positions)
    view = asyncio.run(read_strategy_one_financial_view(
        _assignment(), broker, manager))
    assert view.current_purchase_groups == 2
    assert not view.pending_entry
    groups.append(_group(action="add_long", group_id="add-2",
                         state=OrderManagementState.WORKING))
    view = asyncio.run(read_strategy_one_financial_view(
        _assignment(), broker, manager))
    assert view.current_purchase_groups == 3
    assert view.pending_entry


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


def test_same_ticker_assignments_share_one_account_read_and_oms_snapshot():
    first = _assignment()
    second = StrategyAssignment(
        "assignment-2", first.strategy_id, first.strategy_revision, "DU1",
        "AAA", 456, AssignmentStatus.WATCHING,
        StrategyPermissions(enter=True), {})
    reads = {"positions": 0, "groups": 0}

    async def positions(_account):
        reads["positions"] += 1
        return [SimpleNamespace(conid=123, contractDesc="AAA", position=5.),
                SimpleNamespace(conid=456, contractDesc="AAA", position=7.)]

    def groups():
        reads["groups"] += 1
        return [_group(filled=5., closed=True)]

    views = asyncio.run(read_strategy_one_financial_views(
        (first, second), SimpleNamespace(positions=positions),
        SimpleNamespace(snapshots=groups)))
    assert [view.position_quantity for view in views] == [5., 7.]
    assert [view.completed_entries for view in views] == [1, 0]
    assert reads == {"positions": 1, "groups": 1}


def test_financial_view_uses_exact_indexed_oms_snapshot_when_available():
    async def positions(_account):
        return [SimpleNamespace(conid=123, contractDesc="AAA", position=5.)]

    requested = []

    def indexed(account_id, assignment_id):
        requested.append((account_id, assignment_id))
        return [_group(filled=5., closed=True)]

    def global_snapshot():
        pytest.fail("Strategy 1 scanned the global OMS history")

    manager = SimpleNamespace(
        snapshots_for_assignment=indexed, snapshots=global_snapshot)
    view = asyncio.run(read_strategy_one_financial_view(
        _assignment(), SimpleNamespace(positions=positions), manager))
    assert view.position_quantity == 5.
    assert requested == [("DU1", "assignment-1")]

    manager.snapshots_for_assignment = lambda *_: [
        SimpleNamespace(**{**vars(_group()), "assignment_id": "foreign"})]
    with pytest.raises(RuntimeError, match="crossed its assignment"):
        asyncio.run(read_strategy_one_financial_view(
            _assignment(), SimpleNamespace(positions=positions), manager))


@pytest.mark.parametrize("state", [
    {"pending_capital_request": {"request_id": "r1"}},
    {"pending_capital_request": False},
    {"reentry_not_before_ms": 100},
    {"reentry_not_before_ms": 0},
])
def test_strategy_one_rejects_legacy_mutable_assignment_financial_state(state):
    async def positions(_account):
        return []

    assignment = replace(_assignment(), state=state)
    broker = SimpleNamespace(positions=positions)
    manager = SimpleNamespace(snapshots=lambda: [])
    with pytest.raises(ValueError, match="unsupported assignment financial state"):
        asyncio.run(read_strategy_one_financial_view(
            assignment, broker, manager))
