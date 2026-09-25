from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import floor, isclose, isfinite
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
from src.trading_runtime.execution_policies import utc_from_epoch_microseconds
from src.trading_runtime.domain import BrokerAccount as CanonicalBrokerAccount
from src.trading_runtime.domain import BrokerProvider
from src.trading_runtime.domain import Execution as CanonicalExecution
from src.trading_runtime.domain import OrderState as CanonicalOrderState
from src.trading_runtime.domain import PositionState as CanonicalPositionState
from src.trading_runtime.domain import SnapshotManifest, TradingStateSnapshot
from src.trading_runtime.domain import BrokerEventEnvelope, BrokerEventType, OrderIntent, TradingMode
from src.trading_runtime.canonical_commands import intent_to_ibkr_request, lifecycle_event, response_events
from src.trading_runtime.ibkr_normalizer import (
    normalize_account_values,
    normalize_execution,
    normalize_ledger,
    normalize_order,
    normalize_position,
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
from src.trading_runtime.order_management import ShortabilitySnapshot


NEW_YORK = ZoneInfo("America/New_York")


def _checkpoint_time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("Simulator checkpoint timestamps must be timezone-aware")
    return parsed


def _checkpoint_order_request(request: OrderRequest) -> dict[str, Any]:
    """Keep simulator lineage that the outbound CPAPI serializer must omit."""
    payload = request.to_cpapi()
    payload.update({key: deepcopy(value) for key, value in request.raw.items()
                    if key.startswith("canonical_")})
    return payload


def _market_event_checkpoint(event: MarketEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["kind"] = event.kind
    for key in ("ingest_ts", "participant_ts", "trf_ts", "ts"):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload


def _quote_from_checkpoint(payload: dict[str, Any]) -> QuoteEvent:
    payload.pop("kind", None)
    payload["conditions"] = tuple(int(value) for value in payload.get("conditions") or ())
    payload["indicators"] = tuple(int(value) for value in payload.get("indicators") or ())
    payload["ingest_ts"] = _checkpoint_time(payload["ingest_ts"])
    payload["ts"] = _checkpoint_time(payload["ts"])
    return QuoteEvent(**payload)


def _trade_from_checkpoint(payload: dict[str, Any]) -> TradeEvent:
    payload.pop("kind", None)
    payload["conditions"] = tuple(int(value) for value in payload.get("conditions") or ())
    for key in ("ingest_ts", "ts"):
        payload[key] = _checkpoint_time(payload[key])
    for key in ("participant_ts", "trf_ts"):
        if payload.get(key) is not None:
            payload[key] = _checkpoint_time(payload[key])
    return TradeEvent(**payload)


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    initial_cash: float = 100_000.0
    base_currency: str = "USD"
    commission_per_share: float = 0.005
    minimum_commission: float = 1.0
    liquidity_participation: float = 0.25
    marketable_liquidity_participation: float | None = None
    market_slippage_bps: float = 0.0
    allow_short: bool = False
    new_order_activation_delay_ms: float = 0.0

    def __post_init__(self) -> None:
        if (not isfinite(self.new_order_activation_delay_ms)
                or not 0 <= self.new_order_activation_delay_ms <= 60_000):
            raise ValueError('New order activation delay must be finite and in [0,60000] ms')
        if self.initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        if not 0 < self.liquidity_participation <= 1:
            raise ValueError("liquidity_participation must be in (0, 1]")
        if (
            self.marketable_liquidity_participation is not None
            and not 0 < self.marketable_liquidity_participation <= 1
        ):
            raise ValueError(
                "marketable_liquidity_participation must be in (0, 1]"
            )
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
    oca_group: str = ""
    filled: float = 0.0
    avg_price: float = 0.0
    commission_paid: float = 0.0
    stop_triggered: bool = False
    trailing_reference: float = 0.0
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
            # CPAPI uses this field as the order-state timestamp. Preserve the
            # causal simulated submission time so completed-run hydration does
            # not make every historical order appear to have been created at
            # review time.
            lastExecutionTime=self.submitted_at,
            statusDescription=self.status_description,
            raw={
                "oca_group": self.oca_group,
                "submitted_at": self.submitted_at.isoformat(),
                "canonical_strategy_id": self.request.raw.get(
                    "canonical_strategy_id", ""
                ),
                "canonical_strategy_revision": self.request.raw.get(
                    "canonical_strategy_revision", 0
                ),
                "canonical_metadata": self.request.raw.get(
                    "canonical_metadata", {}
                ),
            },
        )


class SimulatedBrokerAdapter:
    """Deterministic, event-driven implementation of the CPAPI broker contract.

    Orders, executions, positions, summary, and ledger are exposed with the
    same field names and lifecycle used by the live adapter. It intentionally
    does not emulate IBKR transport/session failure; those belong to live
    integration tests, while market and order semantics belong here.
    """

    requires_fresh_execution_state = False

    def __init__(self, account_ids: list[str], config: SimulationConfig | None = None, *, mode: TradingMode = TradingMode.REPLAY, initial_time: datetime | None = None) -> None:
        if not account_ids or any(not item.strip() for item in account_ids):
            raise ValueError("At least one non-empty simulated account id is required")
        if len(set(account_ids)) != len(account_ids):
            raise ValueError("Simulated account ids must be unique")
        self.config = config or SimulationConfig()
        self.mode = mode
        if initial_time is not None and initial_time.tzinfo is None:
            raise ValueError("Simulated initial time requires a timezone")
        self.initial_time = initial_time
        self._account_ids = list(account_ids)
        self._cash = {account_id: self.config.initial_cash for account_id in account_ids}
        self._realized_pnl = {account_id: 0.0 for account_id in account_ids}
        self._positions: dict[str, dict[int, _Position]] = {account_id: {} for account_id in account_ids}
        self._orders: dict[str, _OrderState] = {}
        # Bar matching touches every active ticker boundary. These are
        # derived indexes, rebuilt on restore, never checkpoint authorities.
        self._orders_by_ticker: dict[str, list[_OrderState]] = {}
        self._position_conids_by_ticker: dict[str, set[int]] = {}
        self._order_ids_by_coid: dict[str, str] = {}
        self._executions: list[Execution] = []
        self._quotes: dict[int, QuoteEvent] = {}
        self._trades: dict[int, TradeEvent] = {}
        self._quotes_by_ticker: dict[str, QuoteEvent] = {}
        self._trades_by_ticker: dict[str, TradeEvent] = {}
        self._marks: dict[int, float] = {}
        self._performance_marks: dict[int, tuple[float, float]] = {}
        self._performance = dict(complete=True, as_of='', unrealized=0., market_value=0.,
            peak_unrealized=0., worst_unrealized=0., equity_peak=0., maximum_drawdown=0.)
        self._liquidity_consumed: dict[str, tuple[str, float]] = {}
        self._bar_mode = False
        self._bar_boundaries: dict[str, datetime] = {}
        self._bar_marks_by_ticker: dict[str, float] = {}
        self._next_order_id = 1
        self._next_execution_id = 1
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._initialized = True

    def checkpoint_state(self) -> dict[str, Any]:
        """Return the complete deterministic broker state required for restart."""
        return {
            "schema_version": 4,
            "initial_time": self.initial_time.isoformat() if self.initial_time is not None else None,
            "performance_extrema": dict(self._performance),
            "performance_marks": {str(k): list(v) for k,v in self._performance_marks.items()},
            "liquidity_consumed": dict(self._liquidity_consumed),
            "bar_mode": self._bar_mode,
            "bar_boundaries": {key: value.isoformat() for key, value in self._bar_boundaries.items()},
            "bar_marks_by_ticker": dict(self._bar_marks_by_ticker),
            "account_ids": list(self._account_ids),
            "cash": dict(self._cash),
            "realized_pnl": dict(self._realized_pnl),
            "positions": {
                account_id: [asdict(position) for position in positions.values()]
                for account_id, positions in self._positions.items()
            },
            "orders": [
                {
                    "request": _checkpoint_order_request(state.request),
                    "order_id": state.order_id,
                    "status": state.status.value,
                    "submitted_at": state.submitted_at.isoformat(),
                    "oca_group": state.oca_group,
                    "filled": state.filled,
                    "avg_price": state.avg_price,
                    "commission_paid": state.commission_paid,
                    "stop_triggered": state.stop_triggered,
                    "trailing_reference": state.trailing_reference,
                    "status_description": state.status_description,
                }
                for state in self._orders.values()
            ],
            "executions": [
                {**asdict(execution), "trade_time": execution.trade_time.isoformat()}
                for execution in self._executions
            ],
            "quotes": {
                str(conid): _market_event_checkpoint(event)
                for conid, event in self._quotes.items()
            },
            "trades": {
                str(conid): _market_event_checkpoint(event)
                for conid, event in self._trades.items()
            },
            "quotes_by_ticker": {
                ticker: _market_event_checkpoint(event)
                for ticker, event in self._quotes_by_ticker.items()
            },
            "trades_by_ticker": {
                ticker: _market_event_checkpoint(event)
                for ticker, event in self._trades_by_ticker.items()
            },
            "marks": {str(conid): value for conid, value in self._marks.items()},
            "next_order_id": self._next_order_id,
            "next_execution_id": self._next_execution_id,
        }

    def restore_checkpoint_state(self, payload: dict[str, Any]) -> None:
        """Restore only an exact, complete simulator checkpoint."""
        schema_version = int(payload.get("schema_version") or 0)
        if schema_version not in {1, 2, 3, 4}:
            raise ValueError("Unsupported simulated broker checkpoint schema")
        if payload.get('initial_time'):
            self.initial_time = _checkpoint_time(payload['initial_time'])
        account_ids = [str(value) for value in payload.get("account_ids") or ()]
        if account_ids != self._account_ids:
            raise ValueError("Simulated broker checkpoint account identity changed")
        cash = {str(key): float(value) for key, value in dict(payload.get("cash") or {}).items()}
        realized = {
            str(key): float(value)
            for key, value in dict(payload.get("realized_pnl") or {}).items()
        }
        if set(cash) != set(self._account_ids) or set(realized) != set(self._account_ids):
            raise ValueError("Simulated broker checkpoint omitted account balances")
        positions: dict[str, dict[int, _Position]] = {account_id: {} for account_id in self._account_ids}
        source_positions = dict(payload.get("positions") or {})
        if set(source_positions) != set(self._account_ids):
            raise ValueError("Simulated broker checkpoint omitted account positions")
        for account_id, rows in source_positions.items():
            for row in rows or ():
                position = _Position(**dict(row))
                positions[str(account_id)][position.conid] = position
        orders: dict[str, _OrderState] = {}
        order_ids_by_coid: dict[str, str] = {}
        for row in payload.get("orders") or ():
            values = dict(row)
            request = OrderRequest.from_cpapi(dict(values.pop("request")))
            state = _OrderState(
                request=request,
                order_id=str(values["order_id"]),
                status=OrderStatus(str(values["status"])),
                submitted_at=_checkpoint_time(values["submitted_at"]),
                oca_group=str(values.get("oca_group") or ""),
                filled=float(values.get("filled") or 0),
                avg_price=float(values.get("avg_price") or 0),
                commission_paid=float(values.get("commission_paid") or 0),
                stop_triggered=bool(values.get("stop_triggered")),
                trailing_reference=float(values.get("trailing_reference") or 0),
                status_description=str(values.get("status_description") or ""),
            )
            orders[state.order_id] = state
            if request.cOID:
                if request.cOID in order_ids_by_coid:
                    raise ValueError("Simulated broker checkpoint contains duplicate cOID")
                order_ids_by_coid[request.cOID] = state.order_id
        executions = []
        for row in payload.get("executions") or ():
            values = dict(row)
            values["trade_time"] = _checkpoint_time(values["trade_time"])
            executions.append(Execution(**values))
        self._cash = cash
        self._liquidity_consumed = {
            str(key): (str(value[0]), float(value[1]))
            for key, value in dict(payload.get("liquidity_consumed") or {}).items()
        }
        self._bar_mode = bool(payload.get("bar_mode", False))
        self._bar_boundaries = {
            str(key): _checkpoint_time(value)
            for key, value in dict(payload.get("bar_boundaries") or {}).items()
        }
        self._bar_marks_by_ticker = {
            str(key): float(value)
            for key, value in dict(payload.get("bar_marks_by_ticker") or {}).items()
        }
        self._realized_pnl = realized
        self._positions = positions
        self._orders = orders
        self._orders_by_ticker = {}
        for state in orders.values():
            if state.status in OPEN_ORDER_STATUSES:
                self._orders_by_ticker.setdefault(state.request.ticker.upper(), []).append(state)
        self._position_conids_by_ticker = {}
        for account_positions in positions.values():
            for position in account_positions.values():
                if position.quantity:
                    self._position_conids_by_ticker.setdefault(
                        position.ticker.upper(), set()).add(position.conid)
        self._order_ids_by_coid = order_ids_by_coid
        self._executions = executions
        self._quotes = {
            int(conid): _quote_from_checkpoint(dict(row))
            for conid, row in dict(payload.get("quotes") or {}).items()
        }
        self._trades = {
            int(conid): _trade_from_checkpoint(dict(row))
            for conid, row in dict(payload.get("trades") or {}).items()
        }
        self._quotes_by_ticker = {
            str(ticker).upper(): _quote_from_checkpoint(dict(row))
            for ticker, row in dict(payload.get("quotes_by_ticker") or {}).items()
        }
        self._trades_by_ticker = {
            str(ticker).upper(): _trade_from_checkpoint(dict(row))
            for ticker, row in dict(payload.get("trades_by_ticker") or {}).items()
        }
        if schema_version == 1:
            self._quotes_by_ticker = {
                event.ticker.upper(): event for event in self._quotes.values()
            }
            self._trades_by_ticker = {
                event.ticker.upper(): event for event in self._trades.values()
            }
        self._marks = {
            int(conid): float(value)
            for conid, value in dict(payload.get("marks") or {}).items()
        }
        if schema_version >= 4:
            performance = dict(payload['performance_extrema'])
            if any(not isfinite(float(performance[k])) for k in
                ('unrealized','market_value','peak_unrealized','worst_unrealized','equity_peak','maximum_drawdown')):
                raise ValueError('Invalid performance checkpoint')
            self._performance = performance
            self._performance_marks = {int(k): tuple(v) for k,v in payload['performance_marks'].items()}
        else:
            # Older checkpoints did not retain the intra-position path. Do not
            # invent historical extrema from the final (possibly flat) state.
            self._performance_marks = {}
            self._performance = dict(complete=False, as_of='', unrealized=0., market_value=0.,
                peak_unrealized=0., worst_unrealized=0., equity_peak=0., maximum_drawdown=0.)
            for conid in self._marks:
                self._observe_performance(conid, None)
        self._next_order_id = int(payload.get("next_order_id") or 0)
        self._next_execution_id = int(payload.get("next_execution_id") or 0)
        if self._next_order_id < 1 or self._next_execution_id < 1:
            raise ValueError("Simulated broker checkpoint contains invalid counters")

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
                valid_at=self._latest_event_time(),
            )
            for account_id in self._account_ids
        ]

    async def preview_orders(self, account_id: str, orders: list[OrderRequest]) -> list[dict[str, Any]]:
        self._require_account(account_id)
        previews: list[dict[str, Any]] = []
        for order in orders:
            self._require_matching_account(account_id, order)
            mark = self._reference_price(order.conid, order.ticker)
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
        standalone_oca_group = (
            f"sim-oca-{self._next_order_id}"
            if len(orders) > 1
            and all(order.isSingleGroup and not order.parentId for order in orders)
            else ""
        )
        replaced_protection_identity = self._strategy_protection_replacement_identity(
            orders,
            oca_group=standalone_oca_group,
        )
        async with self._lock:
            results: list[dict[str, Any]] = []
            # Funding denial is a known broker rejection, not an unknown
            # transport outcome. Check the entire batch before accepting any
            # parent or protection leg so OMS can release its reservation.
            for order in orders:
                self._require_matching_account(account_id, order)
            resolved_orders = [self._resolve_cash_quantity(order) for order in orders]
            # Supported batches are brackets or OCA alternatives, not an
            # arbitrary basket of independent buys. Preserve their existing
            # per-leg funding requirement rather than adding OCA siblings.
            required_cash = max((
                (order.price or self._reference_price(order.conid, order.ticker)) * order.quantity
                + self._commission(order.quantity)
                for order in resolved_orders if order.side.upper() == 'BUY'), default=0.)
            if required_cash > self._cash[account_id]:
                return [{'error': 'Order exceeds available cash', 'errorCode': 201,
                         'required_cash': required_cash, 'available_cash': self._cash[account_id]}]
            for order, resolved in zip(orders, resolved_orders):
                self._require_matching_account(account_id, order)
                if order.cOID and order.cOID in self._order_ids_by_coid:
                    raise ValueError(f"cOID must be unique: {order.cOID}")
                self._pretrade_validate(
                    resolved,
                    oca_group=standalone_oca_group,
                    replaced_protection_identity=replaced_protection_identity,
                )
                order_id = str(self._next_order_id)
                self._next_order_id += 1
                status = OrderStatus.INACTIVE if resolved.parentId else OrderStatus.SUBMITTED
                state = _OrderState(
                    resolved,
                    order_id,
                    status,
                    self._order_submission_time(resolved),
                    oca_group=standalone_oca_group,
                )
                self._orders[order_id] = state
                self._orders_by_ticker.setdefault(resolved.ticker.upper(), []).append(state)
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

    async def suppress_order_replies(self, message_ids: list[str]) -> dict[str, Any]:
        return {"status": "submitted", "messageIds": list(dict.fromkeys(message_ids))}

    async def shortability(self, conid: int) -> ShortabilitySnapshot:
        self._require_initialized()
        return ShortabilitySnapshot(
            conid=conid,
            available_shares=1_000_000.0,
            classification="easy_to_borrow",
            observed_at=self._latest_event_time(),
            raw={"7636": 1_000_000.0, "7644": "easy_to_borrow"},
        )

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest) -> list[dict[str, Any]]:
        self._require_account(account_id)
        self._require_matching_account(account_id, order)
        async with self._lock:
            state = self._require_order(account_id, order_id)
            if state.status not in OPEN_ORDER_STATUSES:
                raise ValueError("Only open orders may be modified")
            if order.conid != state.request.conid or order.side.upper() != state.request.side.upper():
                raise ValueError("Modification must preserve conid and side")
            if order.cOID != state.request.cOID:
                raise ValueError("Modification must preserve cOID")
            if order.quantity is not None and order.quantity < state.filled:
                raise ValueError("Modified quantity cannot be below filled quantity")
            self._pretrade_validate(
                order,
                exclude_order_id=order_id,
                existing_order_quantity=state.requested_quantity,
                existing_order_filled=state.filled,
                oca_group=state.oca_group,
            )
            previous_ticker = state.request.ticker.upper()
            next_ticker = order.ticker.upper()
            if previous_ticker != next_ticker:
                self._orders_by_ticker[previous_ticker].remove(state)
                if not self._orders_by_ticker[previous_ticker]:
                    del self._orders_by_ticker[previous_ticker]
                self._orders_by_ticker.setdefault(next_ticker, []).append(state)
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
        return self._position_rows(account_id)

    def _position_rows(self, account_id: str) -> list[PortfolioPosition]:
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
        manifest, positions = normalize_position_snapshot(rows, account_id)
        at = self._latest_event_time()
        return (replace(manifest, provider=BrokerProvider.SIMULATED, started_at=at,
                        completed_at=at, source_watermark=at.isoformat()),
                [replace(row, source_event_time=at) for row in positions])

    async def account_summary(self, account_id: str) -> AccountSummary:
        self._require_account(account_id)
        positions = await self.positions(account_id)
        return self._summary_from_positions(account_id, positions, self._latest_event_time())

    def _summary_from_positions(self, account_id: str, positions: list[PortfolioPosition], at: datetime) -> AccountSummary:
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
            timestamp=at,
        )

    async def canonical_account_values(self, account_id: str):
        return normalize_account_values((await self.account_summary(account_id)).to_cpapi(), account_id)

    async def account_ledger(self, account_id: str) -> AccountLedger:
        summary = await self.account_summary(account_id)
        positions = await self.positions(account_id)
        return self._ledger_from_positions(account_id, positions, summary)

    def _ledger_from_positions(self, account_id: str, positions: list[PortfolioPosition], summary: AccountSummary) -> AccountLedger:
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

    def financial_projection(self, snapshot: TradingStateSnapshot, *, as_of: datetime) -> TradingStateSnapshot:
        """Freeze current simulator financial state without reconciliation or writes.

        Called only at an engine publication boundary. Reuse the broker's
        valuation formulas; never scan market history or mutate execution state.
        """
        if as_of.tzinfo is None or as_of < snapshot.as_of:
            raise ValueError("Financial projection requires a causal timezone-aware boundary")
        positions, values, ledger = [], [], []
        for account_id in snapshot.account_ids:
            rows = self._position_rows(account_id)
            summary = self._summary_from_positions(account_id, rows, as_of)
            values.extend(normalize_account_values(summary.to_cpapi(), account_id))
            ledger.extend(normalize_ledger(self._ledger_from_positions(account_id, rows, summary).to_cpapi(), account_id))
            snapshot_id = f"{account_id}:{as_of.isoformat()}"
            positions.extend(replace(normalize_position(row.to_cpapi(), account_id, snapshot_id),
                source_event_time=as_of) for row in rows)
        return replace(snapshot, as_of=as_of, positions=tuple(positions),
            account_values=tuple(values), ledger=tuple(ledger))

    @property
    def has_orders(self) -> bool:
        return bool(self._orders)

    def performance_extrema(self) -> dict[str, Any]:
        return dict(self._performance)

    def _observe_performance(self, conid: int, at: datetime | None) -> None:
        # Update only the changed instrument, not every position on every tick.
        unrealized = market_value = 0.
        for positions in self._positions.values():
            position = positions.get(conid)
            if position is not None and position.quantity:
                mark = self._marks.get(conid, position.avg_cost)
                unrealized += (mark-position.avg_cost)*position.quantity
                market_value += mark*position.quantity
        previous = self._performance_marks.get(conid, (0.,0.))
        if unrealized or market_value:
            self._performance_marks[conid] = (unrealized,market_value)
        else:
            self._performance_marks.pop(conid,None)
        p = self._performance
        p['unrealized'] += unrealized-previous[0]
        p['market_value'] += market_value-previous[1]
        if not self._performance_marks:
            p['unrealized'] = p['market_value'] = 0.
        equity = sum(self._cash.values())+p['market_value']-self.config.initial_cash*len(self._account_ids)
        p['peak_unrealized'] = max(p['peak_unrealized'],p['unrealized'])
        p['worst_unrealized'] = min(p['worst_unrealized'],p['unrealized'])
        p['equity_peak'] = max(p['equity_peak'],equity)
        p['maximum_drawdown'] = max(p['maximum_drawdown'],p['equity_peak']-equity)
        if at is not None:p['as_of'] = at.isoformat()

    def observe_market_event(self, event: MarketEvent) -> int:
        """Update causal quote/trade marks without running order matching."""

        self._require_initialized()
        if isinstance(event, TradeEvent) and not event.price_eligible:
            return 0
        ticker = event.ticker.upper()
        if isinstance(event, QuoteEvent):
            self._quotes_by_ticker[ticker] = event
        else:
            self._trades_by_ticker[ticker] = event
        conid = self._event_conid(event)
        if conid <= 0:
            return 0
        if isinstance(event, QuoteEvent):
            self._quotes[conid] = event
            if event.midpoint > 0:
                self._marks[conid] = event.midpoint
        else:
            self._trades[conid] = event
            if event.price > 0:
                self._marks[conid] = event.price
        self._observe_performance(conid,event.ts)
        return conid

    async def on_market_event(self, event: MarketEvent) -> list[Execution]:
        if self._bar_mode:
            raise RuntimeError("Liquidity-bar broker mode cannot mix with market events")
        conid = self.observe_market_event(event)
        if conid <= 0:
            return []
        if not self._orders:
            return []
        return await self._match_orders(event, fill_time=event.ts)

    def completed_liquidity_quote(self, ticker: str) -> QuoteEvent | None:
        """Latest valid completed-boundary quote, never a replayed tape event."""
        if not self._bar_mode:
            raise RuntimeError("Completed liquidity quotes require bar-mode execution")
        return self._quotes_by_ticker.get(ticker.upper())

    def validate_liquidity_bar(self, row: Mapping[str, Any], *, at: datetime) -> None:
        """Reject malformed source rows before OMS or broker state changes."""
        if at.tzinfo is None or int(row.get("resolution_ms") or 0) != 100:
            raise ValueError("Broker requires a completed, timezone-aware 100ms liquidity bar")
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or int(row.get("event_count") or 0) <= 0:
            raise ValueError("Broker liquidity bar requires a ticker and source events")
        bucket_start = at - timedelta(milliseconds=100)
        boundary_us = int(at.timestamp() * 1_000_000)
        last_us = int(row.get("last_event_us") or 0)
        if last_us < int(bucket_start.timestamp() * 1_000_000) or last_us >= boundary_us:
            raise ValueError("Broker liquidity events must belong to the completed bucket")
        first_us = int(row.get("first_event_us") or last_us)
        if first_us > last_us or first_us < int(bucket_start.timestamp() * 1_000_000):
            raise ValueError("Broker liquidity bucket has invalid source event bounds")
        previous_boundary = self._bar_boundaries.get(ticker)
        if previous_boundary is not None and at <= previous_boundary:
            raise ValueError("Broker liquidity buckets must advance by ticker")
        # A cached quote is a completed-boundary snapshot for order submission,
        # not a synthetic market event. match_current_orders is disabled below.
        quote_us = int(row.get("quote_timestamp_us") or 0)
        if int(row.get("quote_valid") or 0) and not 0 < quote_us <= last_us:
            raise ValueError("Broker liquidity bar has invalid quote provenance")
        valid_quote = bool(int(row.get("quote_valid") or 0)) and (
            0 < quote_us <= last_us and boundary_us - quote_us <= 1_000_000
        )
        bid = float(row.get("bid_int") or 0) / 10_000
        ask = float(row.get("ask_int") or 0) / 10_000
        bid_size = float(row.get("bid_size") or 0)
        ask_size = float(row.get("ask_size") or 0)
        if valid_quote and (bid <= 0 or ask < bid or min(bid_size, ask_size) < 0):
            raise ValueError("Broker liquidity bar contains an invalid quote")
        extremes_valid = bool(int(row.get("extremes_valid") or 0))
        low = float(row.get("low_int") or 0) / 10_000 if extremes_valid else 0.0
        high = float(row.get("high_int") or 0) / 10_000 if extremes_valid else 0.0
        execution_volume = float(row.get("execution_volume") or 0)
        if execution_volume < 0 or (extremes_valid and (low <= 0 or high < low)):
            raise ValueError("Broker liquidity bar contains invalid trade aggregates")

    async def on_liquidity_bar(
        self, row: Mapping[str, Any], *, at: datetime,
    ) -> list[Execution]:
        """Match against a completed 100 ms liquidity bucket, never an invented tape order.

        A decision/order from this bucket first becomes eligible in the next
        bucket. Stops triggered by an intrabucket range also wait for the next
        bucket, because the aggregate cannot establish trigger/quote ordering.
        """
        self.validate_liquidity_bar(row, at=at)
        return await self._on_validated_liquidity_bar(row, at=at)

    async def _on_validated_liquidity_bar(
        self, row: Mapping[str, Any], *, at: datetime,
    ) -> list[Execution]:
        """Runtime-only continuation after validation, before any OMS side effect."""
        ticker = str(row["ticker"]).strip().upper()
        bucket_start = at - timedelta(milliseconds=100)
        boundary_us = int(at.timestamp() * 1_000_000)
        quote_us = int(row.get("quote_timestamp_us") or 0)
        valid_quote = bool(int(row.get("quote_valid") or 0)) and (
            boundary_us - quote_us <= 1_000_000)
        bid = float(row.get("bid_int") or 0) / 10_000
        ask = float(row.get("ask_int") or 0) / 10_000
        bid_size = float(row.get("bid_size") or 0)
        ask_size = float(row.get("ask_size") or 0)
        quote = None
        if valid_quote:
            observed_at = utc_from_epoch_microseconds(quote_us)
            quote = QuoteEvent(
                ask_exchange=0, ask_price=ask, ask_size=ask_size,
                bid_exchange=0, bid_price=bid, bid_size=bid_size,
                conditions=(), indicators=(), ingest_ts=at.astimezone(timezone.utc),
                raw={"liquidity_bar_snapshot": True, "quote_timestamp_us": quote_us},
                source="arte.liquidity_100ms_v1", ticker=ticker, ts=observed_at,
            )
        self._trades_by_ticker.pop(ticker, None)
        price_valid = bool(int(row.get("price_valid") or 0))
        extremes_valid = bool(int(row.get("extremes_valid") or 0))
        close = float(row.get("close_int") or 0) / 10_000 if price_valid else 0.0
        low = float(row.get("low_int") or 0) / 10_000 if extremes_valid else 0.0
        high = float(row.get("high_int") or 0) / 10_000 if extremes_valid else 0.0
        execution_volume = float(row.get("execution_volume") or 0)
        self._bar_mode = True
        self._bar_boundaries[ticker] = at
        mark = close or (quote.midpoint if quote is not None else 0.0)
        if mark > 0:
            self._bar_marks_by_ticker[ticker] = mark
        else:
            self._bar_marks_by_ticker.pop(ticker, None)
        self._quotes_by_ticker.pop(ticker, None)
        if quote is not None:
            self._quotes_by_ticker[ticker] = quote
        # An empty broker book has no conid-level quote, mark, performance, or
        # fill consumers. Keep the completed ticker snapshot above for order
        # admission and checkpoint recovery, then avoid per-ticker book scans.
        # The authoritative order map retains completed/cancelled orders for
        # audit. Its derived matching index must not rescan them on every
        # subsequent 100 ms bucket for the rest of the session.
        ticker_orders = self._orders_by_ticker.get(ticker, ())
        if ticker_orders:
            active = [state for state in ticker_orders
                      if state.status in OPEN_ORDER_STATUSES]
            if len(active) != len(ticker_orders):
                if active:
                    self._orders_by_ticker[ticker] = active
                else:
                    del self._orders_by_ticker[ticker]
                ticker_orders = active
        position_conids = self._position_conids_by_ticker.get(ticker, ())
        if not ticker_orders and not position_conids:
            return []
        conids = {state.request.conid for state in ticker_orders}
        conids.update(position_conids)
        for conid in conids:
            if quote is not None:
                self._quotes[conid] = quote
            else:
                self._quotes.pop(conid, None)
            self._trades.pop(conid, None)
            if mark > 0:
                self._marks[conid] = mark
                self._observe_performance(conid, at)
        if not conids:
            return []
        identity = f"{at.isoformat()}:{int(row.get('bucket_index') or 0)}"
        executions: list[Execution] = []
        async with self._lock:
            eligible = [state for state in sorted(ticker_orders, key=lambda item: int(item.order_id))
                        if state.status in {OrderStatus.SUBMITTED, OrderStatus.PRE_SUBMITTED}
                        and state.submitted_at <= bucket_start]
            for state in eligible:
                if state.status not in {OrderStatus.SUBMITTED, OrderStatus.PRE_SUBMITTED}:
                    continue
                if (self.config.new_order_activation_delay_ms and
                    at < state.submitted_at + timedelta(
                        milliseconds=self.config.new_order_activation_delay_ms)):
                    continue
                if not self._session_allows(state.request, at):
                    continue
                side = state.request.side.upper()
                order_type = state.request.orderType.upper()
                touch = (ask if side == "BUY" else bid) if quote is not None else 0.0
                if order_type in {"STP", "STOP_LIMIT", "TRAIL"} and not state.stop_triggered:
                    if order_type == "TRAIL":
                        reference = state.trailing_reference or touch or close
                        amount = float(state.request.trailingAmt or 0)
                        if reference <= 0 or amount <= 0:
                            continue
                        threshold = (reference * (1 - amount / 100)
                                     if str(state.request.trailingType or "").strip() == "%"
                                     else reference - amount) if side == "SELL" else (
                            reference * (1 + amount / 100)
                            if str(state.request.trailingType or "").strip() == "%"
                            else reference + amount)
                        triggered = (low > 0 and low <= threshold) if side == "SELL" else (
                            high > 0 and high >= threshold)
                        state.trailing_reference = (max(reference, high) if side == "SELL"
                                                    else min(reference, low or reference))
                    else:
                        stop = float(state.request.auxPrice or 0)
                        triggered = stop > 0 and (
                            high >= stop if side == "BUY" else 0 < low <= stop)
                    if triggered:
                        state.stop_triggered = True
                    continue
                if order_type in {"STP", "TRAIL"}:
                    order_type = "MKT"
                elif order_type == "STOP_LIMIT":
                    order_type = "LMT"
                marketable = False
                available = 0.0
                price = 0.0
                if order_type == "MKT" and touch > 0:
                    marketable, price = True, touch
                    available = ask_size if side == "BUY" else bid_size
                elif order_type == "LMT":
                    limit = float(state.request.price or 0)
                    if limit <= 0:
                        continue
                    if touch > 0 and (touch <= limit if side == "BUY" else touch >= limit):
                        marketable, price = True, touch
                        available = ask_size if side == "BUY" else bid_size
                    elif execution_volume > 0 and (
                        0 < low <= limit if side == "BUY" else high >= limit):
                        price, available = limit, execution_volume
                elif order_type == "MIDPRICE" and quote is not None:
                    price, available = quote.midpoint, min(bid_size, ask_size)
                elif order_type not in {"MKT", "LMT", "MIDPRICE"}:
                    raise ValueError(f"Unsupported liquidity-bar order type: {order_type}")
                if price <= 0 or available <= 0:
                    continue
                slot = f"{ticker}:bar:{side}"
                fill = self._size_candidate(state, side=side, order_type=order_type,
                    marketable=marketable, market_price=price, available=available,
                    slot=slot, identity=identity)
                if fill is None:
                    continue
                fill_price, quantity = fill
                executions.append(self._apply_fill(state, at, fill_price, quantity))
                previous = self._liquidity_consumed.get(slot, (identity, 0.0))
                self._liquidity_consumed[slot] = (
                    identity, (previous[1] if previous[0] == identity else 0.0) + quantity)
        return executions

    async def match_current_orders(
        self,
        ticker: str,
        event_time: datetime,
        broker_order_ids: tuple[str, ...] = (),
        decision_bid: float | None = None,
        decision_ask: float | None = None,
        quote_observed_at: datetime | None = None,
    ) -> list[Execution]:
        """Match new historical orders against the latest causal market event.

        A marketable order sent between persisted market events can execute
        against a quote that was already visible when the strategy decided.
        Waiting for the next database event introduces artificial latency and
        can fill an entry after its strategy evidence has expired.
        """

        if self.mode not in {
            TradingMode.REPLAY,
            TradingMode.BACKTEST,
            TradingMode.BACKTEST_DEBUG,
        }:
            return []
        if self._bar_mode:
            return []
        at = event_time.astimezone(timezone.utc)
        requested_ids = set(broker_order_ids)
        conids = {
            state.request.conid
            for order_id, state in self._orders.items()
            if state.request.ticker.upper() == ticker.upper()
            and (not requested_ids or order_id in requested_ids)
            and state.status in {OrderStatus.SUBMITTED, OrderStatus.PRE_SUBMITTED}
        }
        executions: list[Execution] = []
        for conid in sorted(conids):
            quote = self._quotes.get(conid) or self._quotes_by_ticker.get(
                ticker.upper()
            )
            if quote is None or quote.ts.astimezone(timezone.utc) > at:
                continue
            if (
                decision_bid is not None
                and decision_bid > 0
                and decision_ask is not None
                and decision_ask >= decision_bid
            ):
                observed_at = (quote_observed_at or event_time).astimezone(timezone.utc)
                if observed_at > at:
                    continue
                quote = replace(
                    quote,
                    bid_price=float(decision_bid),
                    ask_price=float(decision_ask),
                    ts=observed_at,
                )
            executions.extend(
                await self._match_orders(
                    quote,
                    fill_time=at,
                    broker_order_ids=requested_ids or None,
                )
            )
        return executions

    async def _match_orders(
        self,
        event: MarketEvent,
        *,
        fill_time: datetime,
        broker_order_ids: set[str] | None = None,
    ) -> list[Execution]:
        conid = self._event_conid(event)
        if conid <= 0:
            return []
        executions: list[Execution] = []
        async with self._lock:
            # Freeze event eligibility before applying any fills. A child that
            # becomes active because its parent fills on this event may only
            # observe the next market event; otherwise it can trigger on data
            # that causally preceded its activation.
            eligible = [
                state
                for state in self._sorted_orders()
                if state.request.conid == conid
                and (broker_order_ids is None or state.order_id in broker_order_ids)
                and state.status in {OrderStatus.SUBMITTED, OrderStatus.PRE_SUBMITTED}
            ]
            for state in eligible:
                if (self.config.new_order_activation_delay_ms
                        and fill_time < state.submitted_at + timedelta(
                            milliseconds=self.config.new_order_activation_delay_ms)):
                    continue
                # Eligibility is frozen to prevent a newly activated child
                # from seeing the parent's market event. OCA cancellation is
                # different: a sibling cancelled by an earlier fill on this
                # same event must not execute from the frozen candidate list.
                if state.status not in {
                    OrderStatus.SUBMITTED,
                    OrderStatus.PRE_SUBMITTED,
                }:
                    continue
                if not self._session_allows(state.request, event.ts):
                    continue
                fill = self._fill_candidate(state, event)
                if fill is None:
                    continue
                price, quantity = fill
                execution = self._apply_fill(state, fill_time, price, quantity)
                slot, identity = self._liquidity_identity(event, state.request.side.upper())
                previous = self._liquidity_consumed.get(slot, (identity, 0.0))
                self._liquidity_consumed[slot] = (
                    identity, (previous[1] if previous[0] == identity else 0.0) + quantity,
                )
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
        marketable = order_type == "MKT"
        market_price = self._executable_price(request.conid, side, event)
        if market_price <= 0:
            return None
        if order_type in {"STP", "STOP_LIMIT"} and not state.stop_triggered:
            stop = float(request.auxPrice or 0)
            trade_trigger = (request.raw.get("canonical_metadata") or {}).get("stop_trigger_source") == "eligible_trade"
            if trade_trigger and (not isinstance(event, TradeEvent) or not event.price_eligible):
                return None
            trigger_price = event.price if trade_trigger else market_price
            state.stop_triggered = trigger_price >= stop if side == "BUY" else trigger_price <= stop
            if not state.stop_triggered:
                return None
            if order_type == "STP":
                order_type = "MKT"
                marketable = True
        if order_type == "TRAIL":
            trailing_amount = float(request.trailingAmt or 0)
            if trailing_amount <= 0:
                return None
            if side == "SELL":
                state.trailing_reference = max(
                    state.trailing_reference or market_price,
                    market_price,
                )
                trigger_price = (
                    state.trailing_reference * (1 - trailing_amount / 100)
                    if str(request.trailingType or "").strip() == "%"
                    else state.trailing_reference - trailing_amount
                )
                if market_price > trigger_price:
                    return None
            else:
                state.trailing_reference = min(
                    state.trailing_reference or market_price,
                    market_price,
                )
                trigger_price = (
                    state.trailing_reference * (1 + trailing_amount / 100)
                    if str(request.trailingType or "").strip() == "%"
                    else state.trailing_reference + trailing_amount
                )
                if market_price < trigger_price:
                    return None
            order_type = "MKT"
            marketable = True
        if order_type in {"LMT", "STOP_LIMIT", "TRAILLMT"}:
            limit = float(request.price or 0)
            if isinstance(event, TradeEvent):
                # Preserve the distinction between an aggressive limit that
                # still crosses the latest causal touch and a genuinely
                # resting limit. Treating every tape-driven continuation fill
                # as passive applies the queue-participation haircut to an
                # already marketable entry/exit and creates artificial
                # multi-second fills in Replay/Backtest.
                trade_price = float(event.price or 0)
                crossed = (
                    trade_price <= limit
                    if side == "BUY"
                    else trade_price >= limit
                )
                if not crossed:
                    return None
                quote = self._quotes.get(
                    request.conid
                ) or self._quotes_by_ticker.get(request.ticker.upper())
                opposite_touch = (
                    float(quote.ask_price)
                    if quote is not None and side == "BUY"
                    else float(quote.bid_price)
                    if quote is not None
                    else 0.0
                )
                marketable = bool(
                    opposite_touch > 0
                    and (
                        opposite_touch <= limit
                        if side == "BUY"
                        else opposite_touch >= limit
                    )
                )
                if marketable:
                    market_price = (
                        min(opposite_touch, limit)
                        if side == "BUY"
                        else max(opposite_touch, limit)
                    )
                else:
                    # A tape print proves executable quantity, not price
                    # priority or improvement for a resting order. Fill it
                    # conservatively at the submitted limit and apply passive
                    # participation to the print size below.
                    market_price = limit
            else:
                if (side == "BUY" and market_price > limit) or (
                    side == "SELL" and market_price < limit
                ):
                    return None
                marketable = True
                market_price = (
                    min(market_price, limit)
                    if side == "BUY"
                    else max(market_price, limit)
                )
        elif order_type == "MIDPRICE":
            quote = self._quotes.get(request.conid)
            if quote is None or quote.midpoint <= 0:
                return None
            market_price = quote.midpoint
        elif order_type not in {"MKT", "STP", "TRAIL"}:
            return None
        available = self._event_liquidity(event, side)
        slot, identity = self._liquidity_identity(event, side)
        return self._size_candidate(state, side=side, order_type=order_type,
            marketable=marketable, market_price=market_price, available=available,
            slot=slot, identity=identity)

    def _size_candidate(
        self, state: _OrderState, *, side: str, order_type: str,
        marketable: bool, market_price: float, available: float,
        slot: str, identity: str,
    ) -> tuple[float, float] | None:
        request = state.request
        if side == "SELL" and not self.config.allow_short:
            held = max(
                0.0,
                self._positions[request.acctId]
                .get(request.conid, _Position(request.conid, request.ticker))
                .quantity,
            )
            # Execution is the final no-short authority. Order reconciliation
            # can observe a batch of partial sibling fills after the broker has
            # already booked them, so a temporarily stale order quantity must
            # never be allowed to cross a long position through zero.
            if held < state.remaining:
                state.request = replace(
                    state.request,
                    quantity=state.filled + held,
                )
        participation = self.config.liquidity_participation
        if (
            marketable
            and self.config.marketable_liquidity_participation is not None
        ):
            # An aggressive routed order consumes executable displayed
            # liquidity; the passive queue-participation haircut is not an
            # appropriate proxy once the limit crosses the touch. Deeper and
            # hidden liquidity remain unobserved, so this still caps the fill
            # at the causal quote/trade event rather than granting an instant
            # full fill.
            participation = self.config.marketable_liquidity_participation
        prior_identity, consumed = self._liquidity_consumed.get(slot, (identity, 0.0))
        available_quantity = min(
            state.remaining,
            max(0.0, available * participation - (consumed if prior_identity == identity else 0.0)),
        )
        quantity = (
            float(floor(available_quantity + 1e-9))
            if isclose(state.requested_quantity, round(state.requested_quantity), abs_tol=1e-9)
            else available_quantity
        )
        if quantity <= 0:
            return None
        slippage = self.config.market_slippage_bps / 10_000
        if order_type in {"MKT", "STP", "TRAIL"} and slippage:
            market_price *= 1 + slippage if side == "BUY" else 1 - slippage
        if side == "BUY" and market_price > 0:
            # Final broker cash fence: other tickers/accounts' order matching
            # cannot spend this account's cash twice, including commissions.
            budget = self._cash[state.request.acctId] + state.commission_paid
            rate = self.config.commission_per_share
            affordable = min(
                (budget - self.config.minimum_commission) / market_price,
                (budget - state.filled * rate) / (market_price + rate),
            )
            if isclose(state.requested_quantity, round(state.requested_quantity), abs_tol=1e-9):
                affordable = float(floor(affordable + 1e-9))
            quantity = min(quantity, max(0.0, affordable))
            if quantity <= 0:
                return None
        return market_price, quantity

    @staticmethod
    def _liquidity_identity(event: MarketEvent, side: str) -> tuple[str, str]:
        kind = "quote" if isinstance(event, QuoteEvent) else "trade"
        values = ((event.bid_price, event.ask_price, event.bid_size, event.ask_size)
                  if isinstance(event, QuoteEvent) else (event.price, event.size))
        return (f"{event.ticker.upper()}:{kind}:{side}",
                repr((event.ts.isoformat(), event.sequence, values)))

    def _apply_fill(self, state: _OrderState, ts: datetime, price: float, quantity: float) -> Execution:
        prior_value = state.avg_price * state.filled
        state.filled += quantity
        state.avg_price = (prior_value + price * quantity) / state.filled
        state.status = OrderStatus.FILLED if state.remaining <= 1e-12 else OrderStatus.SUBMITTED
        if self._is_single_exit_group_member(state) and state.status != OrderStatus.FILLED:
            self._reduce_single_exit_sibling_capacity(state, quantity)
        commission = self._incremental_order_commission(state)
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
            raw={
                "strategy_id": state.request.raw.get("canonical_strategy_id", ""),
                "canonical_strategy_revision": state.request.raw.get("canonical_strategy_revision", 0),
                "canonical_run_id": state.request.raw.get("canonical_run_id", ""),
                "canonical_metadata": state.request.raw.get("canonical_metadata", {}),
            },
        )
        self._next_execution_id += 1
        self._executions.append(execution)
        self._book_execution(state.request, price, quantity, commission)
        self._observe_performance(state.request.conid,ts)
        if state.status == OrderStatus.FILLED:
            self._activate_children(state.request.cOID)
            self._cancel_oca_siblings(state)
        return execution

    def _reduce_single_exit_sibling_capacity(
        self, filled: _OrderState, incremental_quantity: float
    ) -> None:
        """Reduce alternatives after a partial fill in an OCA or bracket."""

        for sibling in self._orders.values():
            if sibling.order_id == filled.order_id:
                continue
            same_group = (
                bool(filled.oca_group)
                and sibling.oca_group == filled.oca_group
            ) or (
                not filled.oca_group
                and bool(filled.request.parentId)
                and sibling.request.parentId == filled.request.parentId
            )
            if not same_group:
                continue
            if sibling.status not in OPEN_ORDER_STATUSES:
                continue
            next_remaining = max(0.0, sibling.remaining - incremental_quantity)
            sibling.request = replace(
                sibling.request,
                quantity=sibling.filled + next_remaining,
            )
            if next_remaining <= 1e-12:
                sibling.status = OrderStatus.CANCELLED
                sibling.status_description = "OCA capacity was filled by a sibling"

    def _is_single_exit_group_member(self, state: _OrderState) -> bool:
        if not state.request.isSingleGroup:
            return False
        return bool(state.oca_group or state.request.parentId)

    def _book_execution(self, request: OrderRequest, price: float, quantity: float, commission: float) -> None:
        account_id = request.acctId
        signed = quantity if request.side.upper() == "BUY" else -quantity
        position = self._positions[account_id].setdefault(request.conid, _Position(request.conid, request.ticker))
        self._position_conids_by_ticker.setdefault(request.ticker.upper(), set()).add(request.conid)
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
        if new_qty == 0 and not any(
                account_positions.get(request.conid) is not None
                and account_positions[request.conid].quantity
                for account_positions in self._positions.values()):
            conids = self._position_conids_by_ticker.get(request.ticker.upper())
            if conids is not None:
                conids.discard(request.conid)
                if not conids:
                    del self._position_conids_by_ticker[request.ticker.upper()]
        self._cash[account_id] -= signed * price + commission

    def _pretrade_validate(
        self,
        order: OrderRequest,
        *,
        exclude_order_id: str = "",
        existing_order_quantity: float | None = None,
        existing_order_filled: float = 0.0,
        oca_group: str = "",
        replaced_protection_identity: tuple[str, int] | None = None,
    ) -> None:
        if order.quantity is None:
            raise ValueError("Simulation must resolve cashQty before submission")
        if order.side.upper() == "BUY":
            price = order.price or self._reference_price(order.conid, order.ticker)
            # A modification retains the order's total-quantity contract. Cash
            # already spent on partial fills is reflected in account cash, so
            # only the unfilled remainder must still be affordable. Validating
            # the original total again double-counts filled shares and rejects
            # legitimate reprices for whole-account entries.
            quantity_to_fund = order.quantity
            if existing_order_quantity is not None:
                quantity_to_fund = max(0.0, order.quantity - existing_order_filled)
            if (
                price > 0
                and price * quantity_to_fund + self._commission(quantity_to_fund)
                > self._cash[order.acctId]
            ):
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
                    if not self._parent_filled(order.parentId):
                        if order.quantity > parent_capacity:
                            raise ValueError(
                                "Attached sell quantity exceeds its parent buy quantity"
                            )
                        # The child is contingent and cannot execute while the
                        # parent is incomplete. Active partial-fill backstops
                        # are accounted independently against shares held.
                        return
            open_sell_orders = [
                item
                for item in self._orders.values()
                if item.order_id != exclude_order_id
                if not oca_group or item.oca_group != oca_group
                if not self._is_replaced_strategy_protection(
                    item,
                    replaced_protection_identity,
                )
                if item.request.acctId == order.acctId
                and item.request.conid == order.conid
                and item.request.side.upper() == "SELL"
                and (
                    not order.parentId
                    or item.request.parentId != order.parentId
                )
                and item.status in OPEN_ORDER_STATUSES
                # An attached child remains non-executable until its parent is
                # complete. During a partial parent fill the OMS must be able
                # to place an active backstop for the shares already held.
                and item.status != OrderStatus.INACTIVE
            ]
            grouped_open_sells: dict[tuple[str, str], float] = {}
            for item in open_sell_orders:
                if item.oca_group:
                    capacity_key = ("oca", item.oca_group)
                elif item.request.parentId and item.request.isSingleGroup:
                    capacity_key = ("parent", item.request.parentId)
                else:
                    capacity_key = ("order", item.order_id)
                grouped_open_sells[capacity_key] = max(
                    grouped_open_sells.get(capacity_key, 0.0),
                    item.remaining,
                )
            open_sells = sum(grouped_open_sells.values())
            proposed_remaining = max(0.0, order.quantity - existing_order_filled)
            executable_capacity = max(0.0, held, parent_capacity)
            if proposed_remaining + open_sells > executable_capacity + 1e-9:
                if (
                    existing_order_quantity is not None
                    and order.quantity <= existing_order_quantity + 1e-12
                ):
                    # Permit a modification that does not increase an already
                    # open sell. This lets reconciliation monotonically reduce
                    # transient over-coverage after contingent children activate.
                    return
                raise ValueError(
                    "Order would create an unconfigured short position: "
                    f"cOID={order.cOID!r} side={order.side} quantity={order.quantity} "
                    f"filled={existing_order_filled} proposed_remaining={proposed_remaining} "
                    "open_sells="
                    f"{[(item.order_id, item.request.cOID, item.remaining, item.oca_group) for item in open_sell_orders]} "
                    f"grouped_open_sell_capacity={grouped_open_sells} "
                    f"held={held} "
                    f"parent_capacity={parent_capacity} oca_group={oca_group!r}"
                )

    @staticmethod
    def _strategy_protection_replacement_identity(
        orders: list[OrderRequest],
        *,
        oca_group: str,
    ) -> tuple[str, int] | None:
        """Identify a full-exit OCA that atomically supersedes strategy protection.

        The OMS submits the mutually exclusive full-exit alternatives, waits for
        acknowledgement, and then cancels the entry's protective orders before
        another market event can be processed.  The simulator must therefore
        permit only that same-strategy replacement window; unrelated open sells
        remain part of the no-short capacity check.
        """
        if not oca_group or not orders:
            return None
        identities: set[tuple[str, int]] = set()
        for order in orders:
            metadata = dict(order.raw.get("canonical_metadata") or {})
            if str(metadata.get("action") or "") != "exit":
                return None
            strategy_id = str(order.raw.get("canonical_strategy_id") or "")
            strategy_revision = int(order.raw.get("canonical_strategy_revision") or 0)
            if not strategy_id or strategy_revision <= 0:
                return None
            identities.add((strategy_id, strategy_revision))
        return next(iter(identities)) if len(identities) == 1 else None

    @staticmethod
    def _is_replaced_strategy_protection(
        state: _OrderState,
        identity: tuple[str, int] | None,
    ) -> bool:
        if identity is None:
            return False
        raw = state.request.raw
        metadata = dict(raw.get("canonical_metadata") or {})
        action = str(metadata.get("action") or "")
        return (
            action in {"enter_long", "add_long"}
            and str(raw.get("canonical_strategy_id") or "") == identity[0]
            and int(raw.get("canonical_strategy_revision") or 0) == identity[1]
        )

    def _resolve_cash_quantity(self, order: OrderRequest) -> OrderRequest:
        if order.cashQty is None:
            return order
        price = self._reference_price(order.conid, order.ticker)
        if price <= 0:
            raise ValueError("cashQty orders require current market data")
        return replace(order, quantity=order.cashQty / price, cashQty=None)

    def _resolved_quantity(self, order: OrderRequest, price: float) -> float:
        if order.quantity is not None:
            return order.quantity
        return float(order.cashQty or 0.0) / price if price > 0 else 0.0

    def _executable_price(self, conid: int, side: str, event: MarketEvent) -> float:
        quote = (
            event
            if isinstance(event, QuoteEvent)
            else self._quotes.get(conid)
            or self._quotes_by_ticker.get(event.ticker.upper())
        )
        if quote is not None:
            price = quote.ask_price if side == "BUY" else quote.bid_price
            if price > 0:
                return price
        trade = (
            event
            if isinstance(event, TradeEvent)
            else self._trades.get(conid)
            or self._trades_by_ticker.get(event.ticker.upper())
        )
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
        if filled.oca_group:
            siblings = [
                state for state in self._orders.values()
                if state.oca_group == filled.oca_group
            ]
        elif filled.request.parentId:
            siblings = [
                state for state in self._orders.values()
                if state.request.parentId == filled.request.parentId
            ]
        else:
            return
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

    def _incremental_order_commission(self, state: _OrderState) -> float:
        cumulative = self._commission(state.filled)
        incremental = max(0.0, cumulative - state.commission_paid)
        state.commission_paid = cumulative
        return incremental

    def _reference_price(self, conid: int, ticker: str = "") -> float:
        direct = self._marks.get(conid, 0.0)
        if direct > 0 or not ticker:
            return direct
        if self._bar_mode:
            return self._bar_marks_by_ticker.get(ticker.upper(), 0.0)
        event = self._trades_by_ticker.get(ticker.upper()) or self._quotes_by_ticker.get(
            ticker.upper()
        )
        if isinstance(event, TradeEvent):
            return float(event.price)
        if isinstance(event, QuoteEvent):
            return float(event.midpoint)
        return 0.0

    def _event_conid(self, event: MarketEvent) -> int:
        for source in (event.raw,):
            value = source.get("conid") or source.get("con_id")
            if value:
                return int(value)
        matching = {state.request.conid for state in self._orders.values() if state.request.ticker == event.ticker}
        return next(iter(matching)) if len(matching) == 1 else 0

    def _event_time(self, conid: int, ticker: str = "") -> datetime:
        if self._bar_mode and ticker.upper() in self._bar_boundaries:
            return self._bar_boundaries[ticker.upper()]
        event = self._trades.get(conid) or self._quotes.get(conid)
        if event is None and ticker:
            event = self._trades_by_ticker.get(
                ticker.upper()
            ) or self._quotes_by_ticker.get(ticker.upper())
        return event.ts if event is not None else self._latest_event_time()

    def _order_submission_time(self, request: OrderRequest) -> datetime:
        """Bound simulated submission by both market and decision clocks."""

        events = (self._trades.get(request.conid), self._quotes.get(request.conid),
                  self._trades_by_ticker.get(request.ticker.upper()),
                  self._quotes_by_ticker.get(request.ticker.upper()))
        market_time = max((event.ts.astimezone(timezone.utc) for event in events if event is not None),
                          default=self._event_time(request.conid, request.ticker).astimezone(timezone.utc))
        if self._bar_mode and request.ticker.upper() in self._bar_boundaries:
            market_time = max(market_time, self._bar_boundaries[request.ticker.upper()].astimezone(timezone.utc))
        metadata = dict(request.raw.get("canonical_metadata") or {})
        decision_raw = metadata.get("decision_event_time")
        if not decision_raw:
            return market_time
        try:
            decision_time = datetime.fromisoformat(
                str(decision_raw).replace("Z", "+00:00")
            )
        except ValueError:
            return market_time
        if decision_time.tzinfo is None:
            return market_time
        return max(market_time, decision_time.astimezone(timezone.utc))

    def _latest_event_time(self) -> datetime:
        times = [
            event.ts
            for event in [
                *self._trades.values(),
                *self._quotes.values(),
                *self._trades_by_ticker.values(),
                *self._quotes_by_ticker.values(),
            ]
        ]
        times.extend(self._bar_boundaries.values())
        return max(times) if times else self.initial_time or datetime.now(timezone.utc)

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
