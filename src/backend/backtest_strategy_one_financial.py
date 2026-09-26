"""Post-broker financial view for the numbered Strategy 1 reducer.

The sparse Backtest coordinator reads this only after the completed liquidity
boundary has reconciled. It does not infer positions from bars or journal rows.
"""
from __future__ import annotations

from typing import Any

from src.trading_runtime.order_management import (
    OrderManagementState, TERMINAL_MANAGEMENT_STATES,
)
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


async def read_strategy_one_financial_view(
    assignment: StrategyAssignment, broker: Any, order_manager: Any,
) -> StrategyOneFinancialView:
    """Read the shared broker and OMS, never a strategy-maintained position copy."""
    if (not isinstance(assignment, StrategyAssignment)
            or not callable(getattr(broker, "positions", None))
            or not callable(getattr(order_manager, "snapshots", None))):
        raise TypeError("Strategy 1 financial view needs assignment, broker, and OMS")
    positions = await broker.positions(assignment.account_id)
    quantity = sum(float(row.position) for row in positions
                   if int(row.conid) == assignment.conid
                   and str(row.contractDesc).upper() == assignment.ticker)
    groups = [row for row in order_manager.snapshots()
              if row.account_id == assignment.account_id
              and row.assignment_id == assignment.assignment_id]
    if len({row.group_id for row in groups}) != len(groups):
        raise RuntimeError("Strategy 1 OMS repeated a financial group")
    if any(row.ticker != assignment.ticker
           or not isinstance(row.state, OrderManagementState)
           for row in groups):
        raise RuntimeError("Strategy 1 OMS group differs from assignment")
    entries = [row for row in groups if row.action == "enter_long"]
    pending_entry = any(
        row.state not in TERMINAL_MANAGEMENT_STATES
        and not row.entry_submission_closed for row in entries)
    pending_exit = any(
        row.action in {"exit_long", "reduce_long"}
        and row.state not in TERMINAL_MANAGEMENT_STATES for row in groups)
    state = assignment.state
    reentry = state.get("reentry_not_before_ms", 0)
    if type(reentry) is not int or reentry < 0:
        raise ValueError("Strategy 1 reentry clock is invalid")
    return StrategyOneFinancialView(
        assignment.assignment_id, assignment.account_id, assignment.ticker,
        assignment.status, assignment.permissions, quantity, pending_entry,
        pending_exit, bool(state.get("pending_capital_request")),
        sum(row.filled_quantity > 0 for row in entries), reentry)
