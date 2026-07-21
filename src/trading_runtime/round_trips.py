from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.domain import Execution, RoundTripTrade


@dataclass(slots=True)
class _Lot:
    execution_id: str
    side: str
    quantity: Decimal
    price: Decimal
    commission_per_unit: Decimal
    opened_at: datetime


def derive_round_trip_trades(executions: list[Execution]) -> list[RoundTripTrade]:
    """Create deterministic FIFO round trips without pretending to be IBKR tax-lot accounting."""
    lots: dict[tuple[str, int], deque[_Lot]] = defaultdict(deque)
    trades: list[RoundTripTrade] = []
    match_sequence: dict[tuple[str, str], int] = defaultdict(int)
    for execution in sorted(executions, key=lambda row: (row.source_event_time, row.execution_id)):
        key = (execution.account_id, execution.instrument.conid)
        queue = lots[key]
        side = execution.side.upper()
        remaining = execution.quantity
        commission_per_unit = (execution.commission or Decimal("0")) / execution.quantity if execution.quantity else Decimal("0")
        while remaining > 0 and queue and queue[0].side != side:
            lot = queue[0]
            matched = min(remaining, lot.quantity)
            if lot.side == "BUY":
                entry_price, exit_price, trade_side = lot.price, execution.price, "LONG"
                gross = (exit_price - entry_price) * matched
            else:
                entry_price, exit_price, trade_side = lot.price, execution.price, "SHORT"
                gross = (entry_price - exit_price) * matched
            fees = matched * (lot.commission_per_unit + commission_per_unit)
            match_key = (lot.execution_id, execution.execution_id)
            match_sequence[match_key] += 1
            trade_id = str(uuid5(NAMESPACE_URL, ":".join((execution.account_id, str(execution.instrument.conid), lot.execution_id, execution.execution_id, str(match_sequence[match_key])))))
            trades.append(
                RoundTripTrade(
                    trade_id=trade_id,
                    account_id=execution.account_id,
                    instrument=execution.instrument,
                    opened_at=lot.opened_at,
                    closed_at=execution.source_event_time,
                    quantity=matched,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    gross_pnl=gross,
                    fees=fees,
                    net_pnl=gross - fees,
                    side=trade_side,
                    execution_ids=(lot.execution_id, execution.execution_id),
                )
            )
            remaining -= matched
            lot.quantity -= matched
            if lot.quantity <= 0:
                queue.popleft()
        if remaining > 0:
            queue.append(
                _Lot(
                    execution_id=execution.execution_id,
                    side=side,
                    quantity=remaining,
                    price=execution.price,
                    commission_per_unit=commission_per_unit,
                    opened_at=execution.source_event_time,
                )
            )
    return trades
