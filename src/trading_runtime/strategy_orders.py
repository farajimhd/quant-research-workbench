from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.signals import StrategyIntent
from src.market_engine.events import MarketEvent


@dataclass(frozen=True, slots=True)
class StrategyOrderPlan:
    orders: tuple[OrderRequest, ...]
    cancel_oca_groups: tuple[str, ...] = ()
    cancel_strategy_protection: bool = False
    protection_reconciliation_required: bool = False


class IbkrStrategyOrderPlanner:
    """Translate semantic strategy intents into one IBKR-compatible order plan."""

    def plan(
        self,
        *,
        account_id: str,
        instrument: InstrumentContract,
        intent: StrategyIntent,
        strategy_id: str,
        strategy_revision: int,
        limit_offset_bps: float = 5.0,
    ) -> StrategyOrderPlan:
        quantity = float(intent.quantity)
        if quantity <= 0:
            raise ValueError("Order planning requires a positive quantity")
        prefix = f"{strategy_id[:14]}-v{strategy_revision}-{uuid4().hex[:12]}"
        if intent.action == "exit":
            price = _aggressive_limit(intent.reference_price, "SELL", limit_offset_bps)
            if intent.invalidation_price is None or intent.invalidation_price <= 0:
                raise ValueError("Full exit requires a broker-held fallback stop")
            orders = [
                _order(
                    account_id, instrument, f"{prefix}-exit", "SELL", "LMT", quantity,
                    intent, price=price, grouped=True,
                ),
                _order(
                    account_id, instrument, f"{prefix}-fallback-stop", "SELL", "STP", quantity,
                    intent, stop=intent.invalidation_price, grouped=True,
                ),
            ]
            if intent.trailing_amount is not None and intent.trailing_amount > 0:
                orders.append(
                    _order(
                        account_id, instrument, f"{prefix}-fallback-trail", "SELL", "TRAIL", quantity,
                        intent, trailing=intent.trailing_amount, grouped=True,
                    )
                )
            return StrategyOrderPlan(
                orders=tuple(orders),
                cancel_strategy_protection=True,
            )
        if intent.action in {"reduce_long", "take_profit"}:
            price = _aggressive_limit(intent.reference_price, "SELL", limit_offset_bps)
            return StrategyOrderPlan(
                orders=(
                    _order(
                        account_id, instrument, f"{prefix}-reduce", "SELL", "LMT", quantity,
                        intent, price=price,
                    ),
                ),
                protection_reconciliation_required=True,
            )
        if intent.action not in {"enter_long", "add_long"}:
            return StrategyOrderPlan(())

        entry_type = "MKT" if intent.urgency == "market" else "LMT"
        entry_price = None if entry_type == "MKT" else _aggressive_limit(intent.reference_price, "BUY", limit_offset_bps)
        parent = _order(
            account_id, instrument, f"{prefix}-entry", "BUY", entry_type, quantity,
            intent, price=entry_price, grouped=True,
        )
        if intent.action == "add_long":
            return StrategyOrderPlan((parent,), protection_reconciliation_required=True)

        children: list[OrderRequest] = []
        if intent.profit_target_price is not None and intent.profit_target_price > intent.reference_price:
            children.append(
                _order(
                    account_id, instrument, None, "SELL", "LMT", quantity,
                    intent, parent_id=parent.cOID, price=intent.profit_target_price,
                    grouped=True,
                )
            )
        if intent.invalidation_price is not None and intent.invalidation_price < intent.reference_price:
            children.append(
                _order(
                    account_id, instrument, None, "SELL", "STP", quantity,
                    intent, parent_id=parent.cOID, stop=intent.invalidation_price,
                    grouped=True,
                )
            )
        if intent.trailing_amount is not None and intent.trailing_amount > 0:
            children.append(
                _order(
                    account_id, instrument, None, "SELL", "TRAIL", quantity,
                    intent, parent_id=parent.cOID, trailing=intent.trailing_amount,
                    grouped=True,
                )
            )
        if not children:
            raise ValueError("Long entry requires at least one broker-held protective exit")
        return StrategyOrderPlan((parent, *children))


class RuntimeIbkrStrategyOrderPlanner:
    """Runtime adapter with explicit point-in-time instrument identity."""

    def __init__(
        self,
        instruments: dict[str, InstrumentContract],
        *,
        strategy_id: str,
        strategy_revision: int,
        limit_offset_bps: float = 5.0,
    ) -> None:
        self.instruments = {ticker.upper(): instrument for ticker, instrument in instruments.items()}
        self.strategy_id = strategy_id
        self.strategy_revision = strategy_revision
        self.limit_offset_bps = limit_offset_bps
        self._planner = IbkrStrategyOrderPlanner()

    def plan(
        self,
        *,
        intent: StrategyIntent,
        account_id: str,
        event: MarketEvent | None,
    ) -> list[OrderRequest]:
        instrument = self.instruments.get(intent.ticker.upper())
        if instrument is None:
            raise ValueError(f"No point-in-time instrument contract for strategy ticker: {intent.ticker}")
        return list(
            self._planner.plan(
                account_id=account_id,
                instrument=instrument,
                intent=intent,
                strategy_id=self.strategy_id,
                strategy_revision=self.strategy_revision,
                limit_offset_bps=self.limit_offset_bps,
            ).orders
        )

    def should_cancel_strategy_protection(self, intent: StrategyIntent) -> bool:
        return intent.action == "exit"

    def protective_order_prefix(self) -> str:
        return f"{self.strategy_id[:14]}-v{self.strategy_revision}-"


def _order(
    account_id: str,
    instrument: InstrumentContract,
    client_id: str | None,
    side: str,
    order_type: str,
    quantity: float,
    intent: StrategyIntent,
    *,
    parent_id: str | None = None,
    price: float | None = None,
    stop: float | None = None,
    trailing: float | None = None,
    grouped: bool = False,
) -> OrderRequest:
    return OrderRequest(
        acctId=account_id,
        conid=instrument.conid,
        secType=instrument.security_type,
        orderType=order_type,
        side=side,
        quantity=quantity,
        cOID=client_id[:64] if client_id else "",
        parentId=parent_id,
        ticker=instrument.symbol,
        tif=intent.time_in_force,
        outsideRTH=intent.outside_rth,
        price=price,
        auxPrice=stop,
        trailingAmt=trailing,
        trailingType="amt" if trailing is not None else None,
        listingExchange=instrument.exchange,
        isSingleGroup=grouped,
    )


def _aggressive_limit(reference_price: float, side: str, offset_bps: float) -> float:
    direction = 1 if side == "BUY" else -1
    return round(reference_price * (1 + direction * offset_bps / 10_000), 4)
