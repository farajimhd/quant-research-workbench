"""Post-broker financial view for the numbered Strategy 1 reducer.

The sparse Backtest coordinator reads this only after the completed liquidity
boundary has reconciled. It does not infer positions from bars or journal rows.
"""
from __future__ import annotations

from typing import Any, Sequence

from src.trading_runtime.order_management import (
    OrderManagementState, TERMINAL_MANAGEMENT_STATES,
)
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


async def read_strategy_one_financial_view(
    assignment: StrategyAssignment, broker: Any, order_manager: Any,
) -> StrategyOneFinancialView:
    """Read the shared broker and OMS, never a strategy-maintained position copy."""
    return (await read_strategy_one_financial_views(
        (assignment,), broker, order_manager))[0]


async def read_strategy_one_financial_views(
    assignments: Sequence[StrategyAssignment], broker: Any, order_manager: Any,
) -> tuple[StrategyOneFinancialView, ...]:
    """Snapshot one ticker's post-broker state once per account and OMS clock.

    The caller must invoke this after the completed liquidity boundary and
    re-read after any intervening OMS mutation; no financial view is cached
    across strategy decisions or boundaries.
    """
    if (not isinstance(assignments, (tuple, list)) or not assignments
            or any(not isinstance(row, StrategyAssignment) for row in assignments)
            or len({row.assignment_id for row in assignments}) != len(assignments)
            or len({row.ticker for row in assignments}) != 1
            or not callable(getattr(broker, "positions", None))
            or not callable(getattr(order_manager, "snapshots", None))):
        raise TypeError("Strategy 1 financial views need one ticker, broker, and OMS")
    by_account = {}
    for account_id in dict.fromkeys(row.account_id for row in assignments):
        by_account[account_id] = await broker.positions(account_id)
    wanted = {(row.account_id, row.assignment_id): row for row in assignments}
    by_assignment = {key: [] for key in wanted}
    for group in order_manager.snapshots():
        key = (group.account_id, group.assignment_id)
        if key in by_assignment:
            by_assignment[key].append(group)
    result = []
    for assignment in assignments:
        positions = by_account[assignment.account_id]
        quantity = sum(float(row.position) for row in positions
                       if int(row.conid) == assignment.conid
                       and str(row.contractDesc).upper() == assignment.ticker)
        groups = by_assignment[(assignment.account_id, assignment.assignment_id)]
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
        if (not isinstance(state, dict)
                or "reentry_not_before_ms" in state
                or "pending_capital_request" in state):
            # Numbered Strategy 1 never mutates these legacy assignment-state
            # fields. OMS owns pending entry/exit, and completed-entry
            # re-entry remains closed until a certified break witness exists.
            raise ValueError("Strategy 1 has unsupported assignment financial state")
        result.append(StrategyOneFinancialView(
            assignment.assignment_id, assignment.account_id, assignment.ticker,
            assignment.status, assignment.permissions, quantity, pending_entry,
            pending_exit, False,
            sum(row.filled_quantity > 0 for row in entries), 0))
    return tuple(result)
