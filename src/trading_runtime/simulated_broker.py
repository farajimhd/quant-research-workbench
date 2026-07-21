from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
from src.trading_runtime.domain import BrokerAccount as CanonicalBrokerAccount
from src.trading_runtime.domain import BrokerProvider
from src.trading_runtime.domain import Execution as CanonicalExecution
from src.trading_runtime.domain import OrderState as CanonicalOrderState
from src.trading_runtime.domain import PositionState as CanonicalPositionState
from src.trading_runtime.domain import SnapshotManifest
from src.trading_runtime.domain import BrokerEventEnvelope, BrokerEventType, OrderIntent, TradingMode
from src.trading_runtime.canonical_commands import intent_to_ibkr_request, lifecycle_event, response_events
from src.trading_runtime.ibkr_normalizer import (
    normalize_account_values,
    normalize_execution,
    normalize_ledger,
    normalize_order,
    normalize_position_snapshot,
)
from src.trading_runtime.ibkr_schema import (
    OPEN_ORDER_STATUSES,
    AccountLedger,
    AccountSummary,
    Execution,
    LiveOrder,
    OrderRequest,
    OrderStatus,
    PortfolioPosition,
)


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    initial_cash: float = 100_000.0
    base_currency: str = "USD"
    commission_per_share: float = 0.005
    minimum_commission: float = 1.0
    liquidity_participation: float = 0.25
    market_slippage_bps: float = 0.0
    allow_short: bool = False

    def __post_init__(self) -> None:
        if self.initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        if not 0 < self.liquidity_participation <= 1:
            raise ValueError("liquidity_participation must be in (0, 1]")
        if self.commission_per_share < 0 or self.minimum_commission < 0:
            raise ValueError("commission values cannot be negative")


@dataclass(slots=True)
class _Position:
    conid: int
    ticker: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0


@dataclass(slots=True)
class _OrderState:
    request: OrderRequest
    order_id: str
    status: OrderStatus
    submitted_at: datetime
    filled: float = 0.0
    avg_price: float = 0.0
    stop_triggered: bool = False
    status_description: str = ""

    @property
    def requested_quantity(self) -> float:
        return float(self.request.quantity or 0.0)

    @property
    def remaining(self) -> float:
        return max(0.0, self.requested_quantity - self.filled)

    def snapshot(self) -> LiveOrder:
        return LiveOrder(
            account=self.request.acctId,
            orderId=self.order_id,
            conid=self.request.conid,
            ticker=self.request.ticker,
            side=self.request.side.upper(),
            orderType=self.request.orderType.upper(),
            tif=self.request.tif.upper(),
            totalSize=self.requested_quantity,
            filledQuantity=self.filled,
            remainingQuantity=self.remaining,
            avgPrice=self.avg_price,
            order_status=self.status,
            cOID=self.request.cOID,
            parentId=self.request.parentId,
            price=self.request.price,
            auxPrice=self.request.auxPrice,
            outsideRTH=self.request.outsideRTH,
            lastExecutionTime=None,
            statusDescription=self.status_description,
        )


class SimulatedBrokerAdapter:
    """Deterministic, event-driven implementation of the CPAPI broker contract.

    Orders, executions, positions, summary, and ledger are exposed with the
    same field names and lifecycle used by the live adapter. It intentionally
    does not emulate IBKR transport/session failure; those belong to live
    integration tests, while market and order semantics belong here.
    """

    def __init__(self, account_ids: list[str], config: SimulationConfig | None = None, *, mode: TradingMode = TradingMode.REPLAY) -> None:
        if not account_ids or any(not item.strip() for item in account_ids):
            raise ValueError("At least one non-empty simulated account id is required")
        if len(set(account_ids)) != len(account_ids):
            raise ValueError("Simulated account ids must be unique")
        self.config = config or SimulationConfig()
        self.mode = mode
        self._account_ids = list(account_ids)
        self._cash = {account_id: self.config.initial_cash for account_id in account_ids}
        self._realized_pnl = {account_id: 0.0 for account_id in account_ids}
        self._positions: dict[str, dict[int, _Position]] = {account_id: {} for account_id in account_ids}
        self._orders: dict[str, _OrderState] = {}
        self._order_ids_by_coid: dict[str, str] = {}
        self._executions: list[Execution] = []
        self._quotes: dict[int, QuoteEvent] = {}
        self._trades: dict[int, TradeEvent] = {}
        self._marks: dict[int, float] = {}
        self._next_order_id = 1
        self._next_execution_id = 1
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._initialized = True

    async def accounts(self) -> list[str]:
        self._require_initialized()
        return list(self._account_ids)

    async def canonical_accounts(self) -> list[CanonicalBrokerAccount]:
        self._require_initialized()
        return [
            CanonicalBrokerAccount(
                provider=BrokerProvider.SIMULATED,
                account_id=account_id,
                base_currency=self.config.base_currency,
                account_type="SIMULATED",
                alias=account_id,
                title="Deterministic simulated account",
                can_view=True,
                can_trade=True,
            )
            for account_id in self._account_ids
        ]

    async def preview_orders(self, account_id: str, orders: list[OrderRequest]) -> list[dict[str, Any]]:
        self._require_account(account_id)
        previews: list[dict[str, Any]] = []
        for order in orders:
            self._require_matching_account(account_id, order)
            mark = self._reference_price(order.conid)
            quantity = self._resolved_quantity(order, mark)
            notional = quantity * mark
            commission = self._commission(quantity)
            warning = "" if mark > 0 else "No market event is available for price estimation."
            previews.append(
                {
                    "amount": {"amount": notional, "currency": self.config.base_currency},
                    "commission": {"amount": commission, "currency": self.config.base_currency},
                    "equity": {"current": (await self.account_summary(account_id)).netliquidation},
                    "initial": {"current": notional},
                    "maintenance": {"current": notional},
                    "warn": warning,
                    "error": "" if mark > 0 else "MARKET_DATA_REQUIRED",
                }
            )
        return previews

    async def place_orders(self, account_id: str, orders: list[OrderRequest]) -> list[dict[str, Any]]:
        self._require_initialized()
        self._require_account(account_id)
        if not orders:
            raise ValueError("orders cannot be empty")
        if len(orders) > 1 and not self._is_supported_group(orders):
            raise ValueError("CPAPI bulk placement is limited to bracket or OCA groups")
        async with self._lock:
            results: list[dict[str, Any]] = []
            for order in orders:
                self._require_matching_account(account_id, order)
                if order.cOID and order.cOID in self._order_ids_by_coid:
                    raise ValueError(f"cOID must be unique: {order.cOID}")
                resolved = self._resolve_cash_quantity(order)
                self._pretrade_validate(resolved)
                order_id = str(self._next_order_id)
                self._next_order_id += 1
                status = OrderStatus.INACTIVE if resolved.parentId else OrderStatus.SUBMITTED
                state = _OrderState(resolved, order_id, status, self._event_time(resolved.conid))
                self._orders[order_id] = state
                if resolved.cOID:
                    self._order_ids_by_coid[resolved.cOID] = order_id
                results.append({"order_id": order_id, "order_status": status.value, "local_order_id": resolved.cOID})
            return results

    async def submit_intents(self, account_id: str, intents: list[OrderIntent]) -> list[BrokerEventEnvelope]:
        if not intents:
            raise ValueError("intents cannot be empty")
        if any(intent.account_id != account_id for intent in intents):
            raise ValueError("Every canonical intent must match the path account")
        rows = await self.place_orders(account_id, [intent_to_ibkr_request(intent) for intent in intents])
        events: list[BrokerEventEnvelope] = []
        for index, intent in enumerate(intents):
            matched = [rows[index]] if index < len(rows) else []
            events.extend(response_events(intent, matched, BrokerProvider.SIMULATED, self.mode))
        return events

    async def reply(self, reply_id: str, confirmed: bool) -> list[dict[str, Any]]:
        raise ValueError(f"Simulated orders do not generate IBKR precautionary reply {reply_id}")

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest) -> list[dict[str, Any]]:
        self._require_account(account_id)
        self._require_matching_account(account_id, order)
        async with self._lock:
            state = self._require_order(account_id, order_id)
            if state.status not in OPEN_ORDER_STATUSES or state.filled > 0:
                raise ValueError("Only open, unfilled orders may be modified")
            if order.conid != state.request.conid or order.side.upper() != state.request.side.upper():
                raise ValueError("Modification must preserve conid and side")
            if order.cOID != state.request.cOID:
                raise ValueError("Modification must preserve cOID")
            if order.quantity is not None and order.quantity < state.filled:
                raise ValueError("Modified quantity cannot be below filled quantity")
            self._pretrade_validate(order)
            state.request = order
            state.status = OrderStatus.INACTIVE if order.parentId and not self._parent_filled(order.parentId) else OrderStatus.SUBMITTED
            return [{"order_id": order_id, "order_status": state.status.value, "local_order_id": order.cOID}]

    async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        async with self._lock:
            state = self._require_order(account_id, order_id)
            if state.status not in OPEN_ORDER_STATUSES:
                raise ValueError(f"Order {order_id} is not open")
            state.status = OrderStatus.CANCELLED
            state.status_description = "Cancelled by client request"
            self._cancel_children(state.request.cOID, "Parent order was cancelled")
            return {"msg": "Request was submitted", "order_id": int(order_id), "conid": state.request.conid, "account": account_id}

    async def cancel(self, account_id: str, broker_order_id: str) -> list[BrokerEventEnvelope]:
        payload = await self.cancel_order(account_id, broker_order_id)
        return [lifecycle_event(event_type=BrokerEventType.ORDER_STATUS_CHANGED, account_id=account_id, broker_order_id=broker_order_id, payload=payload, provider=BrokerProvider.SIMULATED, mode=self.mode)]

    async def replace(self, account_id: str, broker_order_id: str, intent: OrderIntent) -> list[BrokerEventEnvelope]:
        rows = await self.modify_order(account_id, broker_order_id, intent_to_ibkr_request(intent))
        return response_events(intent, rows, BrokerProvider.SIMULATED, self.mode)

    async def live_orders(self) -> list[LiveOrder]:
        self._require_initialized()
        return [state.snapshot() for state in self._sorted_orders()]

    async def canonical_orders(self, account_id: str = "") -> list[CanonicalOrderState]:
        rows = [normalize_order(order.to_cpapi(), account_id) for order in await self.live_orders()]
        return [row for row in rows if not account_id or row.account_id == account_id]

    async def trades(self, days: int = 7) -> list[Execution]:
        if not 1 <= days <= 7:
            raise ValueError("IBKR trade history supports 1 through 7 days")
        if not self._executions:
            return []
        end = max(execution.trade_time for execution in self._executions)
        start = end - timedelta(days=days)
        return [execution for execution in self._executions if execution.trade_time >= start]

    async def canonical_executions(self, account_id: str = "", days: int = 7) -> list[CanonicalExecution]:
        rows = [normalize_execution(execution.to_cpapi(), account_id) for execution in await self.trades(days)]
        return [row for row in rows if not account_id or row.account_id == account_id]

    async def positions(self, account_id: str) -> list[PortfolioPosition]:
        self._require_account(account_id)
        rows: list[PortfolioPosition] = []
        for position in sorted(self._positions[account_id].values(), key=lambda item: (item.ticker, item.conid)):
            if abs(position.quantity) < 1e-12:
                continue
            mark = self._marks.get(position.conid, position.avg_cost)
            unrealized = (mark - position.avg_cost) * position.quantity
            rows.append(
                PortfolioPosition(
                    acctId=account_id,
                    conid=position.conid,
                    contractDesc=position.ticker,
                    position=position.quantity,
                    mktPrice=mark,
                    mktValue=mark * position.quantity,
                    avgCost=position.avg_cost,
                    avgPrice=position.avg_cost,
                    realizedPnl=position.realized_pnl,
                    unrealizedPnl=unrealized,
                    currency=self.config.base_currency,
                )
            )
        return rows

    async def canonical_position_snapshot(self, account_id: str) -> tuple[SnapshotManifest, list[CanonicalPositionState]]:
        rows = [position.to_cpapi() for position in await self.positions(account_id)]
        return normalize_position_snapshot(rows, account_id)

    async def account_summary(self, account_id: str) -> AccountSummary:
        self._require_account(account_id)
        positions = await self.positions(account_id)
        gross = sum(abs(row.mktValue) for row in positions)
        net = self._cash[account_id] + sum(row.mktValue for row in positions)
        return AccountSummary(
            account_id=account_id,
            netliquidation=net,
            totalcashvalue=self._cash[account_id],
            buyingpower=max(0.0, self._cash[account_id]),
            grosspositionvalue=gross,
            availablefunds=max(0.0, self._cash[account_id]),
            excessliquidity=max(0.0, self._cash[account_id]),
            currency=self.config.base_currency,
            timestamp=self._latest_event_time(),
        )

    async def canonical_account_values(self, account_id: str):
        return normalize_account_values((await self.account_summary(account_id)).to_cpapi(), account_id)

    async def account_ledger(self, account_id: str) -> AccountLedger:
        summary = await self.account_summary(account_id)
        positions = await self.positions(account_id)
        return AccountLedger(
            acctId=account_id,
            cashbalance=summary.totalcashvalue,
            settledcash=summary.totalcashvalue,
            stockmarketvalue=sum(row.mktValue for row in positions),
            netliquidationvalue=summary.netliquidation,
            realizedpnl=self._realized_pnl[account_id],
            unrealizedpnl=sum(row.unrealizedPnl for row in positions),
            currency=self.config.base_currency,
            timestamp=summary.timestamp,
        )

    async def canonical_ledger(self, account_id: str):
        return normalize_ledger((await self.account_ledger(account_id)).to_cpapi(), account_id)

    async def on_market_event(self, event: MarketEvent) -> list[Execution]:
        self._require_initialized()
        conid = self._event_conid(event)
        if conid <= 0:
            return []
        if isinstance(event, QuoteEvent):
            self._quotes[conid] = event
            if event.midpoint > 0:
                self._marks[conid] = event.midpoint
        else:
            self._trades[conid] = event
            if event.price > 0:
                self._marks[conid] = event.price
        executions: list[Execution] = []
        async with self._lock:
            for state in self._sorted_orders():
                if state.request.conid != conid or state.status not in {OrderStatus.SUBMITTED, OrderStatus.PRE_SUBMITTED}:
                    continue
                if not self._session_allows(state.request, event.ts):
                    continue
                fill = self._fill_candidate(state, event)
                if fill is None:
                    continue
                price, quantity = fill
                execution = self._apply_fill(state, event.ts, price, quantity)
                executions.append(execution)
        return executions

    async def expire_day_orders(self, account_id: str, at: datetime) -> list[LiveOrder]:
        self._require_account(account_id)
        expired: list[LiveOrder] = []
        async with self._lock:
            for state in self._sorted_orders():
                if state.request.acctId == account_id and state.request.tif.upper() == "DAY" and state.status in OPEN_ORDER_STATUSES:
                    state.status = OrderStatus.CANCELLED
                    state.status_description = f"DAY order expired at {at.astimezone(timezone.utc).isoformat()}"
                    expired.append(state.snapshot())
        return expired

    def _fill_candidate(self, state: _OrderState, event: MarketEvent) -> tuple[float, float] | None:
        request = state.request
        side = request.side.upper()
        order_type = request.orderType.upper()
        market_price = self._executable_price(request.conid, side, event)
        if market_price <= 0:
            return None
        if order_type in {"STP", "STOP_LIMIT"} and not state.stop_triggered:
            stop = float(request.auxPrice or 0)
            state.stop_triggered = market_price >= stop if side == "BUY" else market_price <= stop
            if not state.stop_triggered:
                return None
            if order_type == "STP":
                order_type = "MKT"
        if order_type in {"LMT", "STOP_LIMIT", "TRAILLMT"}:
            limit = float(request.price or 0)
            if (side == "BUY" and market_price > limit) or (side == "SELL" and market_price < limit):
                return None
            market_price = min(market_price, limit) if side == "BUY" else max(market_price, limit)
        elif order_type == "MIDPRICE":
            quote = self._quotes.get(request.conid)
            if quote is None or quote.midpoint <= 0:
                return None
            market_price = quote.midpoint
        elif order_type not in {"MKT", "STP", "TRAIL"}:
            return None
        available = self._event_liquidity(event, side)
        quantity = min(state.remaining, max(0.0, available * self.config.liquidity_participation))
        if quantity <= 0:
            return None
        slippage = self.config.market_slippage_bps / 10_000
        if order_type in {"MKT", "STP", "TRAIL"} and slippage:
            market_price *= 1 + slippage if side == "BUY" else 1 - slippage
        return market_price, quantity

    def _apply_fill(self, state: _OrderState, ts: datetime, price: float, quantity: float) -> Execution:
        prior_value = state.avg_price * state.filled
        state.filled += quantity
        state.avg_price = (prior_value + price * quantity) / state.filled
        state.status = OrderStatus.FILLED if state.remaining <= 1e-12 else OrderStatus.SUBMITTED
        commission = self._commission(quantity)
        execution = Execution(
            execution_id=f"SIM-{self._next_execution_id}",
            symbol=state.request.ticker,
            side="B" if state.request.side.upper() == "BUY" else "S",
            order_ref=state.request.cOID,
            trade_time=ts.astimezone(timezone.utc),
            trade_time_r=int(ts.timestamp() * 1000),
            size=quantity,
            price=price,
            order_id=state.order_id,
            account=state.request.acctId,
            conid=state.request.conid,
            commission=commission,
            currency=self.config.base_currency,
        )
        self._next_execution_id += 1
        self._executions.append(execution)
        self._book_execution(state.request, price, quantity, commission)
        if state.status == OrderStatus.FILLED:
            self._activate_children(state.request.cOID)
            self._cancel_oca_siblings(state)
        return execution

    def _book_execution(self, request: OrderRequest, price: float, quantity: float, commission: float) -> None:
        account_id = request.acctId
        signed = quantity if request.side.upper() == "BUY" else -quantity
        position = self._positions[account_id].setdefault(request.conid, _Position(request.conid, request.ticker))
        old_qty = position.quantity
        new_qty = old_qty + signed
        if old_qty == 0 or old_qty * signed > 0:
            total_cost = position.avg_cost * abs(old_qty) + price * abs(signed)
            position.avg_cost = total_cost / abs(new_qty) if new_qty else 0.0
        else:
            closing = min(abs(old_qty), abs(signed))
            direction = 1.0 if old_qty > 0 else -1.0
            realized = (price - position.avg_cost) * closing * direction
            position.realized_pnl += realized
            self._realized_pnl[account_id] += realized
            if abs(signed) > abs(old_qty):
                position.avg_cost = price
            elif new_qty == 0:
                position.avg_cost = 0.0
        position.quantity = new_qty
        self._cash[account_id] -= signed * price + commission

    def _pretrade_validate(self, order: OrderRequest) -> None:
        if order.quantity is None:
            raise ValueError("Simulation must resolve cashQty before submission")
        if order.side.upper() == "BUY":
            price = order.price or self._reference_price(order.conid)
            if price > 0 and price * order.quantity + self._commission(order.quantity) > self._cash[order.acctId]:
                raise ValueError("Order exceeds available cash")
        elif not self.config.allow_short:
            held = self._positions[order.acctId].get(order.conid, _Position(order.conid, order.ticker)).quantity
            parent_capacity = 0.0
            if order.parentId:
                parent_order_id = self._order_ids_by_coid.get(order.parentId)
                parent = self._orders.get(parent_order_id or "")
                if (
                    parent is not None
                    and parent.request.acctId == order.acctId
                    and parent.request.conid == order.conid
                    and parent.request.side.upper() == "BUY"
                ):
                    parent_capacity = parent.requested_quantity
            open_sells = sum(
                item.remaining
                for item in self._orders.values()
                if item.request.acctId == order.acctId
                and item.request.conid == order.conid
                and item.request.side.upper() == "SELL"
                and item.request.parentId != order.parentId
                and item.status in OPEN_ORDER_STATUSES
            )
            if order.quantity + open_sells > max(0.0, held, parent_capacity):
                raise ValueError("Order would create an unconfigured short position")

    def _resolve_cash_quantity(self, order: OrderRequest) -> OrderRequest:
        if order.cashQty is None:
            return order
        price = self._reference_price(order.conid)
        if price <= 0:
            raise ValueError("cashQty orders require current market data")
        return replace(order, quantity=order.cashQty / price, cashQty=None)

    def _resolved_quantity(self, order: OrderRequest, price: float) -> float:
        if order.quantity is not None:
            return order.quantity
        return float(order.cashQty or 0.0) / price if price > 0 else 0.0

    def _executable_price(self, conid: int, side: str, event: MarketEvent) -> float:
        quote = event if isinstance(event, QuoteEvent) else self._quotes.get(conid)
        if quote is not None:
            price = quote.ask_price if side == "BUY" else quote.bid_price
            if price > 0:
                return price
        trade = event if isinstance(event, TradeEvent) else self._trades.get(conid)
        return trade.price if trade is not None else 0.0

    def _event_liquidity(self, event: MarketEvent, side: str) -> float:
        if isinstance(event, QuoteEvent):
            return max(0.0, event.ask_size if side == "BUY" else event.bid_size)
        return max(0.0, event.size)

    def _session_allows(self, request: OrderRequest, ts: datetime) -> bool:
        local = ts.astimezone(NEW_YORK)
        if local.weekday() >= 5:
            return False
        minute = local.hour * 60 + local.minute
        if request.outsideRTH:
            return 4 * 60 <= minute < 20 * 60
        return 9 * 60 + 30 <= minute < 16 * 60

    def _activate_children(self, parent_coid: str) -> None:
        if not parent_coid:
            return
        for state in self._orders.values():
            if state.request.parentId == parent_coid and state.status == OrderStatus.INACTIVE:
                state.status = OrderStatus.SUBMITTED

    def _cancel_children(self, parent_coid: str, reason: str) -> None:
        if not parent_coid:
            return
        for state in self._orders.values():
            if state.request.parentId == parent_coid and state.status in OPEN_ORDER_STATUSES:
                state.status = OrderStatus.CANCELLED
                state.status_description = reason

    def _cancel_oca_siblings(self, filled: _OrderState) -> None:
        if not filled.request.parentId:
            return
        siblings = [state for state in self._orders.values() if state.request.parentId == filled.request.parentId]
        if not any(state.request.isSingleGroup for state in siblings):
            return
        for state in siblings:
            if state.order_id != filled.order_id and state.status in OPEN_ORDER_STATUSES:
                state.status = OrderStatus.CANCELLED
                state.status_description = f"OCA sibling {filled.order_id} filled"

    def _parent_filled(self, parent_coid: str) -> bool:
        order_id = self._order_ids_by_coid.get(parent_coid)
        return bool(order_id and self._orders[order_id].status == OrderStatus.FILLED)

    def _is_supported_group(self, orders: list[OrderRequest]) -> bool:
        coids = {order.cOID for order in orders if order.cOID}
        is_bracket = any(order.parentId in coids for order in orders if order.parentId)
        return is_bracket or all(order.isSingleGroup for order in orders)

    def _commission(self, quantity: float) -> float:
        return max(self.config.minimum_commission, abs(quantity) * self.config.commission_per_share)

    def _reference_price(self, conid: int) -> float:
        return self._marks.get(conid, 0.0)

    def _event_conid(self, event: MarketEvent) -> int:
        for source in (event.raw,):
            value = source.get("conid") or source.get("con_id")
            if value:
                return int(value)
        matching = {state.request.conid for state in self._orders.values() if state.request.ticker == event.ticker}
        return next(iter(matching)) if len(matching) == 1 else 0

    def _event_time(self, conid: int) -> datetime:
        event = self._trades.get(conid) or self._quotes.get(conid)
        return event.ts if event is not None else datetime.now(timezone.utc)

    def _latest_event_time(self) -> datetime:
        times = [event.ts for event in [*self._trades.values(), *self._quotes.values()]]
        return max(times) if times else datetime.now(timezone.utc)

    def _sorted_orders(self) -> list[_OrderState]:
        return sorted(self._orders.values(), key=lambda state: int(state.order_id))

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("Broker adapter must be initialized before use")

    def _require_account(self, account_id: str) -> None:
        self._require_initialized()
        if account_id not in self._cash:
            raise ValueError(f"Unknown account: {account_id}")

    @staticmethod
    def _require_matching_account(account_id: str, order: OrderRequest) -> None:
        if order.acctId != account_id:
            raise ValueError(f"Path account {account_id} does not match order acctId {order.acctId}")

    def _require_order(self, account_id: str, order_id: str) -> _OrderState:
        state = self._orders.get(str(order_id))
        if state is None or state.request.acctId != account_id:
            raise ValueError(f"OrderID {order_id} doesn't exist for account {account_id}")
        return state
