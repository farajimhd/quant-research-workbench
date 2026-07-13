from __future__ import annotations

from dataclasses import dataclass

from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderRequest


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_open_orders_per_account: int = 100
    max_order_quantity: float = 100_000
    max_order_notional: float = 1_000_000
    max_gross_position_value: float = 5_000_000


class RiskAuthority:
    """Mode-independent pre-trade checks over IBKR-shaped broker state."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    async def validate(self, broker: BrokerAdapter, account_id: str, orders: list[OrderRequest]) -> None:
        live_orders = [row for row in await broker.live_orders() if row.account == account_id and row.order_status in OPEN_ORDER_STATUSES]
        if len(live_orders) + len(orders) > self.config.max_open_orders_per_account:
            raise ValueError("Risk limit exceeded: too many open orders")
        summary = await broker.account_summary(account_id)
        if summary.grosspositionvalue > self.config.max_gross_position_value:
            raise ValueError("Risk limit exceeded: gross position value")
        for order in orders:
            quantity = float(order.quantity or 0)
            if quantity > self.config.max_order_quantity:
                raise ValueError("Risk limit exceeded: order quantity")
            reference_price = float(order.price or order.auxPrice or 0)
            if reference_price > 0 and quantity * reference_price > self.config.max_order_notional:
                raise ValueError("Risk limit exceeded: order notional")
