from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderRequest
from src.trading_runtime.signals import StrategyIntent


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_open_orders_per_account: int = 100
    maximum_snapshot_age_ms: int = 6_000


@dataclass(slots=True)
class RiskSnapshot:
    account_id: str
    open_orders: int
    gross_position_value: float
    available_funds: float
    observed_at: datetime
    reserved_orders: int = 0
    reserved_notional: float = 0.0


class OrderSafetyGuard:
    """Execution-only safety over a portfolio-approved semantic intent.

    Portfolio management is the sole authority for cash, margin, exposure,
    concentration, loss budgets, and final sizing. This guard prevents the OMS
    from expanding that approval or sending a price outside its envelope.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._snapshots: dict[str, RiskSnapshot] = {}

    async def prime(self, broker: BrokerAdapter, account_ids: list[str] | tuple[str, ...]) -> None:
        """Populate the live risk cache before enabling the execution hot path."""
        live_orders = await broker.live_orders()
        now = datetime.now(timezone.utc)
        for account_id in account_ids:
            summary = await broker.account_summary(account_id)
            self._snapshots[account_id] = RiskSnapshot(
                account_id=account_id,
                open_orders=sum(
                    1
                    for row in live_orders
                    if row.account == account_id and row.order_status in OPEN_ORDER_STATUSES
                ),
                gross_position_value=float(summary.grosspositionvalue),
                available_funds=float(summary.availablefunds),
                observed_at=now,
            )

    def update(
        self,
        account_id: str,
        *,
        open_orders: int,
        gross_position_value: float,
        available_funds: float,
        observed_at: datetime | None = None,
    ) -> None:
        previous = self._snapshots.get(account_id)
        self._snapshots[account_id] = RiskSnapshot(
            account_id=account_id,
            open_orders=max(0, int(open_orders)),
            gross_position_value=float(gross_position_value),
            available_funds=float(available_funds),
            observed_at=(observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc),
            reserved_orders=previous.reserved_orders if previous else 0,
            reserved_notional=previous.reserved_notional if previous else 0.0,
        )

    async def validate(
        self,
        broker: BrokerAdapter,
        account_id: str,
        orders: list[OrderRequest],
        *,
        intent: StrategyIntent | None = None,
        require_fresh: bool = True,
    ) -> None:
        snapshot = self._snapshots.get(account_id)
        if snapshot is None:
            await self.prime(broker, [account_id])
            snapshot = self._snapshots[account_id]
        age_ms = (
            datetime.now(timezone.utc) - snapshot.observed_at.astimezone(timezone.utc)
        ).total_seconds() * 1000.0
        if require_fresh and age_ms > self.config.maximum_snapshot_age_ms:
            raise ValueError(f"Risk snapshot is stale ({age_ms:.0f} ms); execution is frozen")
        if snapshot.open_orders + snapshot.reserved_orders + len(orders) > self.config.max_open_orders_per_account:
            raise ValueError("Order safety limit exceeded: too many open orders")
        if intent is None:
            return
        root_orders = [order for order in orders if not order.parentId]
        root_quantity = sum(float(order.quantity or 0) for order in root_orders)
        if root_quantity > float(intent.quantity) + 1e-9:
            raise ValueError("Order safety violation: broker plan exceeds portfolio-approved quantity")
        envelope = intent.resolved_execution_policy().envelope
        for order in root_orders:
            if order.orderType.upper() not in {"LMT", "STOP_LIMIT", "TRAILLMT"} or order.price is None:
                continue
            if not envelope.permits(order.side, float(order.price)):
                raise ValueError("Order safety violation: price exceeds the portfolio-approved execution envelope")

    def reserve(self, account_id: str, orders: list[OrderRequest]) -> None:
        snapshot = self._snapshots.get(account_id)
        if snapshot is None:
            raise RuntimeError("Risk authority must be primed before reserving an order")
        snapshot.reserved_orders += len(orders)
        snapshot.reserved_notional += sum(
            float(order.quantity or 0) * float(order.price or order.auxPrice or 0)
            for order in orders
            if order.side.upper() == "BUY"
        )

    def release(self, account_id: str, orders: list[OrderRequest]) -> None:
        snapshot = self._snapshots.get(account_id)
        if snapshot is None:
            return
        snapshot.reserved_orders = max(0, snapshot.reserved_orders - len(orders))
        snapshot.reserved_notional = max(
            0.0,
            snapshot.reserved_notional
            - sum(
                float(order.quantity or 0) * float(order.price or order.auxPrice or 0)
                for order in orders
                if order.side.upper() == "BUY"
            ),
        )


# Backward-compatible import while callers migrate to the narrower name.
RiskAuthority = OrderSafetyGuard
