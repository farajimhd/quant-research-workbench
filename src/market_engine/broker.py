from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol


OrderStatus = Literal["pending_submit", "submitted", "partially_filled", "filled", "cancelled", "rejected"]


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    account_id: str
    avg_price_after_fill: float
    commission: float
    conid: int
    currency: str
    execution_id: str
    fill_price: float
    fill_quantity: float
    order_id: str
    remaining_quantity: float
    side: str
    ticker: str
    ts: datetime


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    account_id: str
    avg_filled_price: float
    conid: int
    currency: str
    filled_quantity: float
    limit_price: float | None
    order_id: str
    order_type: str
    remaining_quantity: float
    side: str
    status: OrderStatus
    submitted_at: datetime
    ticker: str
    total_quantity: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    account_id: str
    avg_cost: float
    conid: int
    currency: str
    market_price: float
    market_value: float
    quantity: float
    realized_pnl: float
    ticker: str
    unrealized_pnl: float


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    account_type: str
    buying_power: float
    cash: float
    currency: str
    equity: float
    excess_liquidity: float
    gross_position_value: float
    net_liquidation: float
    open_orders: tuple[OrderSnapshot, ...] = ()
    positions: tuple[PortfolioPosition, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerAdapter(Protocol):
    """Live IBKR and simulated backtest brokers expose this shape."""

    async def accounts(self) -> list[AccountSnapshot]:
        ...

    async def submit_order(self, account_id: str, order: dict[str, Any]) -> OrderSnapshot:
        ...

    async def cancel_order(self, account_id: str, order_id: str) -> OrderSnapshot:
        ...
