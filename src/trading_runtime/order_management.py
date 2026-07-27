from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from time import perf_counter
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from src.market_engine.events import MarketEvent
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.domain import OrderLifecycleState, OrderState
from src.trading_runtime.ibkr_normalizer import normalize_execution, normalize_order
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, LiveOrder, OrderRequest, OrderStatus
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_orders import StrategyOrderPlan


class OrderManagementState(StrEnum):
    CREATED = "created"
    RISK_RESERVED = "risk_reserved"
    SUBMITTING = "submitting"
    WARNING_PENDING = "warning_pending"
    ACKNOWLEDGED = "acknowledged"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    CANCEL_PENDING = "cancel_pending"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    POLICY_BLOCKED = "policy_blocked"


TERMINAL_MANAGEMENT_STATES = {
    OrderManagementState.FILLED,
    OrderManagementState.CANCELLED,
    OrderManagementState.REJECTED,
    OrderManagementState.POLICY_BLOCKED,
}


class ExecutionUrgency(StrEnum):
    VERY_URGENT = "very_urgent"
    URGENT = "urgent"
    REGULAR = "regular"
    PATIENT = "patient"


@dataclass(frozen=True, slots=True)
class ExecutionQuote:
    bid: float
    ask: float
    observed_at: datetime
    tick_size: float

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("Execution quote requires a positive non-crossed NBBO")
        if self.tick_size <= 0:
            raise ValueError("Execution quote tick_size must be positive")
        if self.observed_at.tzinfo is None:
            raise ValueError("Execution quote observed_at must include a timezone")

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class PriceStep:
    after_ms: int
    price: float


@dataclass(frozen=True, slots=True)
class ExecutionTactic:
    urgency: ExecutionUrgency
    side: str
    steps: tuple[PriceStep, ...]
    quote: ExecutionQuote
    maximum_duration_ms: int


@dataclass(frozen=True, slots=True)
class BrokerCommunicationPolicy:
    """Versioned policy for the one unresolved IBKR order-reply lane."""

    version: int = 1
    suppressed_message_ids: tuple[str, ...] = ()
    auto_confirm_message_ids: tuple[str, ...] = ()
    maximum_reply_chain: int = 8
    maximum_quote_age_ms: int = 750
    unknown_warning_action: str = "decline"
    maximum_reprice_ticks: int = 4

    def __post_init__(self) -> None:
        if len(self.suppressed_message_ids) > 51:
            raise ValueError("IBKR supports at most 51 suppressed order message IDs")
        if self.unknown_warning_action != "decline":
            raise ValueError("Unknown IBKR order warnings must fail closed")
        if self.maximum_reply_chain < 1:
            raise ValueError("maximum_reply_chain must be positive")
        if self.maximum_quote_age_ms < 1:
            raise ValueError("maximum_quote_age_ms must be positive")
        if not 0 <= self.maximum_reprice_ticks <= 6:
            raise ValueError("maximum_reprice_ticks must be between 0 and 6")

    @classmethod
    def from_environment(cls) -> "BrokerCommunicationPolicy":
        suppressed = _csv_env("IBKR_SUPPRESS_ORDER_MESSAGE_IDS")
        confirmed = _csv_env("IBKR_AUTO_CONFIRM_ORDER_MESSAGE_IDS")
        return cls(
            suppressed_message_ids=suppressed,
            auto_confirm_message_ids=confirmed,
            maximum_reply_chain=max(1, int(os.environ.get("IBKR_MAXIMUM_REPLY_CHAIN", "8"))),
            maximum_quote_age_ms=max(50, int(os.environ.get("IBKR_MAXIMUM_EXECUTION_QUOTE_AGE_MS", "750"))),
            maximum_reprice_ticks=max(0, min(6, int(os.environ.get("IBKR_MAXIMUM_REPRICE_TICKS", "4")))),
        )

    def warning_decision(self, message_ids: tuple[str, ...]) -> bool:
        return bool(message_ids) and all(item in self.auto_confirm_message_ids for item in message_ids)


@dataclass(frozen=True, slots=True)
class ShortabilitySnapshot:
    conid: int
    available_shares: float
    classification: str
    observed_at: datetime
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def shortable(self) -> bool:
        normalized = self.classification.strip().lower()
        prohibited = {"", "not shortable", "unavailable", "none", "0", "false"}
        return self.available_shares > 0 and normalized not in prohibited


class ShortabilityProvider(Protocol):
    async def shortability(self, conid: int) -> ShortabilitySnapshot: ...


@dataclass(frozen=True, slots=True)
class OrderGroupSnapshot:
    group_id: str
    intent_id: str
    account_id: str
    ticker: str
    action: str
    state: OrderManagementState
    client_order_ids: tuple[str, ...]
    broker_order_ids: tuple[str, ...]
    submitted_at: datetime | None
    updated_at: datetime
    filled_quantity: float
    remaining_quantity: float
    warning_message_ids: tuple[str, ...]
    rejection_reason: str
    decision_to_submit_ms: float | None
    policy_version: int
    reentry_after_fill: bool
    assignment_id: str
    fill_role: str = ""


@dataclass(slots=True)
class _ManagedOrderGroup:
    group_id: str
    intent: StrategyIntent
    account_id: str
    plan: StrategyOrderPlan
    state: OrderManagementState
    created_at: datetime
    updated_at: datetime
    orders: list[OrderRequest]
    tactic: ExecutionTactic | None = None
    broker_order_ids: list[str] = field(default_factory=list)
    broker_order_roles: dict[str, str] = field(default_factory=dict)
    warning_message_ids: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    submitted_at: datetime | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    decision_to_submit_ms: float | None = None
    reprice_task: asyncio.Task[None] | None = None

    def snapshot(
        self,
        policy_version: int,
        *,
        action: str | None = None,
        fill_role: str = "",
    ) -> OrderGroupSnapshot:
        return OrderGroupSnapshot(
            group_id=self.group_id,
            intent_id=self.intent.intent_id,
            account_id=self.account_id,
            ticker=self.intent.ticker,
            action=action or str(self.intent.action),
            state=self.state,
            client_order_ids=tuple(order.cOID for order in self.orders if order.cOID),
            broker_order_ids=tuple(self.broker_order_ids),
            submitted_at=self.submitted_at,
            updated_at=self.updated_at,
            filled_quantity=self.filled_quantity,
            remaining_quantity=self.remaining_quantity,
            warning_message_ids=tuple(self.warning_message_ids),
            rejection_reason=self.rejection_reason,
            decision_to_submit_ms=self.decision_to_submit_ms,
            policy_version=policy_version,
            reentry_after_fill=bool(self.intent.metadata.get("buy_back") or self.intent.metadata.get("reentry_after_fill")),
            assignment_id=str(self.intent.metadata.get("assignment_id") or ""),
            fill_role=fill_role,
        )


PlanProvider = Callable[[StrategyIntent, str, MarketEvent | None], StrategyOrderPlan]
FillCallback = Callable[[OrderGroupSnapshot], Awaitable[None]]


class OrderManagementEngine:
    """Exclusive authority for translating semantic intents into broker actions.

    Strategies never receive a broker and never emit an ``OrderRequest``.
    This engine owns the single IBKR command/reply lane, durable command
    evidence, warning policy, shortability checks, repricing, and reconciliation.
    """

    def __init__(
        self,
        *,
        broker: BrokerAdapter,
        planner: PlanProvider,
        risk: RiskAuthority,
        journal: TradingJournal,
        run_id: str,
        strategy_id: str,
        strategy_revision: int,
        policy: BrokerCommunicationPolicy | None = None,
        shortability_provider: ShortabilityProvider | None = None,
        fill_callback: FillCallback | None = None,
        enforce_wall_clock_quote_freshness: bool = False,
    ) -> None:
        self.broker = broker
        self.planner = planner
        self.risk = risk
        self.journal = journal
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.strategy_revision = strategy_revision
        self.policy = policy or BrokerCommunicationPolicy.from_environment()
        self.shortability_provider = shortability_provider
        self.fill_callback = fill_callback
        self.enforce_wall_clock_quote_freshness = enforce_wall_clock_quote_freshness
        self._command_lane = asyncio.Lock()
        self._groups: dict[str, _ManagedOrderGroup] = {}
        self._group_by_client_id: dict[str, str] = {}
        self._group_by_broker_id: dict[str, str] = {}
        self._closed = False
        self._broker_connected = True

    async def configure_broker_session(self) -> None:
        suppress = getattr(self.broker, "suppress_order_replies", None)
        if suppress is None or not self.policy.suppressed_message_ids:
            return
        response = await suppress(list(self.policy.suppressed_message_ids))
        self._record(
            "broker_policy",
            "order_reply_suppression",
            f"policy-v{self.policy.version}",
            "",
            datetime.now(timezone.utc),
            {
                "policy_version": self.policy.version,
                "message_ids": list(self.policy.suppressed_message_ids),
                "broker_response": response,
            },
        )

    async def submit_intent(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        event: MarketEvent | None,
    ) -> OrderGroupSnapshot:
        if self._closed:
            raise RuntimeError("Order management engine is closed")
        if not self._broker_connected:
            raise RuntimeError("Broker stream is disconnected; new order intents are frozen")
        if intent.intent_id in (group.intent.intent_id for group in self._groups.values()):
            raise ValueError(f"Strategy intent has already been submitted: {intent.intent_id}")
        plan = self.planner(intent, account_id, event)
        if not plan.orders:
            raise ValueError(f"Strategy intent produced no broker order plan: {intent.action}")
        await self._require_shortability(intent, plan)
        tactic = execution_tactic(
            intent,
            self.policy,
            enforce_wall_clock_freshness=self.enforce_wall_clock_quote_freshness,
        )
        protected_exit = await self._modify_existing_protected_exit(
            intent,
            account_id=account_id,
            plan=plan,
            tactic=tactic,
        )
        if protected_exit is not None:
            return protected_exit
        orders = _apply_initial_tactic(plan.orders, tactic)
        now = datetime.now(timezone.utc)
        group = _ManagedOrderGroup(
            group_id=str(uuid4()),
            intent=intent,
            account_id=account_id,
            plan=plan,
            state=OrderManagementState.CREATED,
            created_at=now,
            updated_at=now,
            orders=list(orders),
            tactic=tactic,
            remaining_quantity=float(intent.quantity),
        )
        self._groups[group.group_id] = group
        for order in group.orders:
            if order.cOID:
                self._group_by_client_id[order.cOID] = group.group_id
        self._transition(group, OrderManagementState.CREATED, {"event": "order_group_created"})
        await self.risk.validate(
            self.broker,
            account_id,
            group.orders,
            require_fresh=self.enforce_wall_clock_quote_freshness,
        )
        self.risk.reserve(account_id, group.orders)
        self._transition(group, OrderManagementState.RISK_RESERVED, {"event": "risk_reserved"})
        await self._submit(group)
        return group.snapshot(self.policy.version)

    async def cancel_strategy_protection(
        self,
        *,
        account_id: str,
        ticker: str,
        client_id_prefix: str,
        event_time: datetime,
    ) -> list[dict[str, Any]]:
        """Request cancellation and retain pending state until broker confirmation."""
        responses: list[dict[str, Any]] = []
        live_orders = await self.broker.live_orders()
        candidates = [
            order
            for order in live_orders
            if order.account == account_id
            and order.ticker.upper() == ticker.upper()
            and order.side.upper() == "SELL"
            and order.order_status in OPEN_ORDER_STATUSES
            and (
                str(order.parentId or "").startswith(client_id_prefix)
                or str(order.cOID or "").startswith(client_id_prefix)
            )
        ]
        async with self._command_lane:
            for order in candidates:
                self._record(
                    "command",
                    "order_cancel",
                    str(order.orderId),
                    account_id,
                    event_time,
                    {"reason": "replace_strategy_protection", "ticker": ticker.upper()},
                )
                response = await self.broker.cancel_order(account_id, str(order.orderId))
                responses.append(response)
                group = self._group_for_order(order)
                if group is not None:
                    self._transition(group, OrderManagementState.CANCEL_PENDING, response)
                self._record("broker", "order_cancel_requested", str(order.orderId), account_id, event_time, response)
        return responses

    async def on_order_update(self, order: LiveOrder) -> OrderGroupSnapshot | None:
        group = self._group_for_order(order)
        if group is None:
            return None
        if order.orderId and order.orderId not in group.broker_order_ids:
            group.broker_order_ids.append(order.orderId)
            self._group_by_broker_id[order.orderId] = group.group_id
            group.broker_order_roles[order.orderId] = _infer_order_role(
                order.orderType,
                bool(order.parentId),
                str(group.intent.action),
            )
        group.filled_quantity = max(group.filled_quantity, float(order.filledQuantity))
        group.remaining_quantity = max(0.0, float(order.remainingQuantity))
        next_state = _management_state(order)
        self._transition(group, next_state, {"event": "broker_order_update", "order": order.to_cpapi()})
        if next_state in TERMINAL_MANAGEMENT_STATES and group.reprice_task:
            group.reprice_task.cancel()
        if next_state in TERMINAL_MANAGEMENT_STATES:
            self.risk.release(group.account_id, group.orders)
        fill_role = group.broker_order_roles.get(str(order.orderId), "")
        snapshot = group.snapshot(
            self.policy.version,
            action=_fill_action(group.intent, fill_role) if next_state == OrderManagementState.FILLED else None,
            fill_role=fill_role if next_state == OrderManagementState.FILLED else "",
        )
        if next_state == OrderManagementState.FILLED and self.fill_callback is not None:
            await self.fill_callback(snapshot)
        return snapshot

    async def on_broker_message(self, message: dict[str, Any]) -> list[OrderGroupSnapshot]:
        """Apply the unsolicited IBKR websocket message to managed order groups."""
        topic = str(message.get("topic") or "").lower()
        self._broker_connected = True
        result = message.get("result")
        rows = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
        snapshots: list[OrderGroupSnapshot] = []
        if topic.startswith("sor"):
            for raw in rows:
                state = normalize_order(raw)
                snapshot = await self._on_canonical_order_state(state)
                if snapshot is not None:
                    snapshots.append(snapshot)
        elif topic.startswith("str"):
            for raw in rows:
                execution = normalize_execution(raw)
                self._record(
                    "execution",
                    "broker_execution",
                    execution.execution_id,
                    execution.account_id,
                    execution.source_event_time,
                    {
                        "broker_order_id": execution.broker_order_id,
                        "client_order_id": execution.client_order_id,
                        "quantity": execution.quantity,
                        "price": execution.price,
                        "side": execution.side,
                        "raw": raw,
                    },
                )
        elif topic.startswith(("sts", "system")):
            self._record(
                "broker",
                "connection_state",
                str(uuid4()),
                "",
                datetime.now(timezone.utc),
                {"topic": topic, "result": result},
            )
        return snapshots

    def set_connection_state(self, connected: bool, *, reason: str = "") -> None:
        self._broker_connected = connected
        self._record(
            "broker",
            "connection_state",
            str(uuid4()),
            "",
            datetime.now(timezone.utc),
            {
                "status": "connected" if connected else "disconnected",
                "reason": reason,
                "entries_frozen": not connected,
            },
        )

    async def reconcile(self) -> list[OrderGroupSnapshot]:
        live_orders = await self.broker.live_orders()
        seen: set[str] = set()
        for order in live_orders:
            snapshot = await self.on_order_update(order)
            if snapshot:
                seen.add(snapshot.group_id)
        for group in self._groups.values():
            if group.state == OrderManagementState.OUTCOME_UNKNOWN and group.group_id not in seen:
                self._transition(
                    group,
                    OrderManagementState.REJECTED,
                    {"event": "reconciliation_missing", "reason": "No broker order matched persisted client ids"},
                )
        return self.snapshots()

    def snapshots(self) -> list[OrderGroupSnapshot]:
        return [
            group.snapshot(self.policy.version)
            for group in sorted(self._groups.values(), key=lambda item: item.created_at)
        ]

    async def close(self) -> None:
        self._closed = True
        tasks = [group.reprice_task for group in self._groups.values() if group.reprice_task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _submit(self, group: _ManagedOrderGroup) -> None:
        started = perf_counter()
        self._transition(group, OrderManagementState.SUBMITTING, {"event": "submission_started"})
        for request in group.orders:
            self._record(
                "command",
                "order",
                request.cOID or f"{request.parentId or group.group_id}:{request.orderType}",
                group.account_id,
                group.intent.event_time,
                {
                    **request.to_cpapi(),
                    "strategy_intent_id": group.intent.intent_id,
                    "order_group_id": group.group_id,
                    "policy_version": self.policy.version,
                },
            )
        try:
            async with self._command_lane:
                response = await self.broker.place_orders(group.account_id, group.orders)
                response = await self._resolve_warning_chain_locked(group, response)
        except Exception as exc:
            group.rejection_reason = str(exc)
            self._transition(
                group,
                OrderManagementState.OUTCOME_UNKNOWN,
                {"event": "submission_outcome_unknown", "error": str(exc)},
            )
            raise
        group.decision_to_submit_ms = (perf_counter() - started) * 1000.0
        group.submitted_at = datetime.now(timezone.utc)
        if group.state == OrderManagementState.POLICY_BLOCKED:
            return
        rejected = next((row for row in response if row.get("error") or row.get("errorCode")), None)
        if rejected is not None:
            self.risk.release(group.account_id, group.orders)
            group.rejection_reason = str(rejected.get("error") or rejected.get("message") or rejected)
            self._transition(group, OrderManagementState.REJECTED, rejected)
            return
        for index, row in enumerate(response):
            order_id = str(row.get("order_id") or row.get("orderId") or "")
            if order_id and order_id not in group.broker_order_ids:
                group.broker_order_ids.append(order_id)
                self._group_by_broker_id[order_id] = group.group_id
            if order_id:
                request = group.orders[min(index, len(group.orders) - 1)]
                group.broker_order_roles[order_id] = _order_role(
                    request,
                    str(group.intent.action),
                )
            self._record(
                "broker",
                "order_acknowledgement",
                order_id or group.group_id,
                group.account_id,
                group.intent.event_time,
                {
                    **row,
                    "order_group_id": group.group_id,
                    "decision_to_submit_ms": group.decision_to_submit_ms,
                },
            )
        self._transition(group, OrderManagementState.ACKNOWLEDGED, {"event": "submission_acknowledged"})
        if group.tactic and len(group.tactic.steps) > 1 and group.broker_order_ids:
            group.reprice_task = asyncio.create_task(self._run_repricing(group))

    async def _modify_existing_protected_exit(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        plan: StrategyOrderPlan,
        tactic: ExecutionTactic | None,
    ) -> OrderGroupSnapshot | None:
        if str(intent.action) not in {"exit", "take_profit", "reduce_long", "cover", "reduce_short"}:
            return None
        position_quantity = float(intent.metadata.get("position_quantity") or intent.quantity)
        if intent.quantity + 1e-9 < position_quantity:
            raise ValueError(
                "Partial profit pocket is blocked: CPAPI isSingleGroup does not guarantee "
                "proportional protection reduction; use a full pocket and optional re-entry"
            )
        candidates = [
            group
            for group in self._groups.values()
            if group.account_id == account_id
            and group.intent.ticker.upper() == intent.ticker.upper()
            and str(group.intent.action) in {"enter_long", "enter_short", "add_long", "add_short"}
            and group.broker_order_ids
        ]
        for protected in reversed(candidates):
            desired_side = _intent_side(intent)
            target_index = next(
                (
                    index
                    for index, order in enumerate(protected.orders)
                    if order.parentId
                    and order.orderType == "LMT"
                    and order.side.upper() == desired_side
                    and index < len(protected.broker_order_ids)
                ),
                None,
            )
            if target_index is None:
                continue
            existing = protected.orders[target_index]
            initial_price = tactic.steps[0].price if tactic else float(plan.orders[0].price or intent.reference_price)
            replacement = replace(
                existing,
                quantity=float(intent.quantity),
                price=initial_price,
            )
            now = datetime.now(timezone.utc)
            group = _ManagedOrderGroup(
                group_id=str(uuid4()),
                intent=intent,
                account_id=account_id,
                plan=StrategyOrderPlan((replacement,)),
                state=OrderManagementState.CREATED,
                created_at=now,
                updated_at=now,
                orders=[replacement],
                tactic=tactic,
                broker_order_ids=[protected.broker_order_ids[target_index]],
                broker_order_roles={
                    protected.broker_order_ids[target_index]: "managed_exit",
                },
                remaining_quantity=float(intent.quantity),
            )
            self._groups[group.group_id] = group
            broker_order_id = group.broker_order_ids[0]
            self._group_by_broker_id[broker_order_id] = group.group_id
            self._transition(
                group,
                OrderManagementState.SUBMITTING,
                {
                    "event": "protected_exit_reprice_started",
                    "reused_order_id": broker_order_id,
                    "preserved_oca_protection": True,
                },
            )
            started = perf_counter()
            async with self._command_lane:
                response = await self.broker.modify_order(account_id, broker_order_id, replacement)
                response = await self._resolve_warning_chain_locked(group, response)
            group.decision_to_submit_ms = (perf_counter() - started) * 1000.0
            group.submitted_at = datetime.now(timezone.utc)
            if group.state == OrderManagementState.POLICY_BLOCKED:
                return group.snapshot(self.policy.version)
            rejected = next((row for row in response if row.get("error") or row.get("errorCode")), None)
            if rejected:
                group.rejection_reason = str(rejected.get("error") or rejected.get("message") or rejected)
                self._transition(group, OrderManagementState.REJECTED, rejected)
                return group.snapshot(self.policy.version)
            self._record(
                "broker",
                "protected_exit_modified",
                broker_order_id,
                account_id,
                intent.event_time,
                {
                    "order_group_id": group.group_id,
                    "price": replacement.price,
                    "quantity": replacement.quantity,
                    "preserved_oca_protection": True,
                    "broker_response": response,
                    "decision_to_submit_ms": group.decision_to_submit_ms,
                },
            )
            self._transition(group, OrderManagementState.ACKNOWLEDGED, {"event": "protected_exit_acknowledged"})
            if tactic and len(tactic.steps) > 1:
                group.reprice_task = asyncio.create_task(self._run_repricing(group))
            return group.snapshot(self.policy.version)
        return None

    async def _resolve_warning_chain_locked(
        self,
        group: _ManagedOrderGroup,
        response: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        current = response
        seen: set[str] = set()
        for _ in range(self.policy.maximum_reply_chain):
            warning_rows = [row for row in current if row.get("id") and row.get("message")]
            if not warning_rows:
                return current
            if len(warning_rows) != 1:
                raise RuntimeError("IBKR returned more than one unresolved warning in the order lane")
            warning = warning_rows[0]
            reply_id = str(warning["id"])
            if reply_id in seen:
                raise RuntimeError("IBKR repeated an unresolved order warning reply id")
            seen.add(reply_id)
            message_ids = tuple(str(item) for item in warning.get("messageIds") or ())
            group.warning_message_ids.extend(item for item in message_ids if item not in group.warning_message_ids)
            self._transition(
                group,
                OrderManagementState.WARNING_PENDING,
                {"event": "order_warning", "warning": warning},
            )
            confirmed = self.policy.warning_decision(message_ids)
            self._record(
                "broker_policy",
                "order_warning_decision",
                reply_id,
                group.account_id,
                datetime.now(timezone.utc),
                {
                    "order_group_id": group.group_id,
                    "message_ids": list(message_ids),
                    "confirmed": confirmed,
                    "policy_version": self.policy.version,
                    "known": bool(message_ids) and all(item in self.policy.auto_confirm_message_ids for item in message_ids),
                },
            )
            current = await self.broker.reply(reply_id, confirmed)
            if not confirmed:
                self.risk.release(group.account_id, group.orders)
                group.rejection_reason = "IBKR warning was not approved by broker communication policy"
                self._transition(group, OrderManagementState.POLICY_BLOCKED, {"event": "warning_declined"})
                return current
        raise RuntimeError("IBKR order warning chain exceeded the configured maximum")

    async def _run_repricing(self, group: _ManagedOrderGroup) -> None:
        assert group.tactic is not None
        root_index = next((index for index, order in enumerate(group.orders) if order.orderType == "LMT"), None)
        if root_index is None or not group.broker_order_ids:
            return
        root_order_id = group.broker_order_ids[0]
        previous_ms = 0
        for step in group.tactic.steps[1:]:
            await asyncio.sleep(max(0, step.after_ms - previous_ms) / 1000.0)
            previous_ms = step.after_ms
            if group.state in TERMINAL_MANAGEMENT_STATES or group.filled_quantity >= float(group.intent.quantity):
                return
            replacement = replace(group.orders[root_index], price=step.price)
            try:
                async with self._command_lane:
                    response = await self.broker.modify_order(group.account_id, root_order_id, replacement)
                    response = await self._resolve_warning_chain_locked(group, response)
            except Exception as exc:
                self._record(
                    "broker",
                    "order_reprice_error",
                    root_order_id,
                    group.account_id,
                    datetime.now(timezone.utc),
                    {"order_group_id": group.group_id, "error": str(exc), "requested_price": step.price},
                )
                return
            group.orders[root_index] = replacement
            self._record(
                "broker",
                "order_repriced",
                root_order_id,
                group.account_id,
                datetime.now(timezone.utc),
                {
                    "order_group_id": group.group_id,
                    "requested_price": step.price,
                    "elapsed_ms": step.after_ms,
                    "broker_response": response,
                },
            )

    async def _require_shortability(self, intent: StrategyIntent, plan: StrategyOrderPlan) -> None:
        if str(intent.action) not in {"enter_short", "add_short"}:
            return
        if self.shortability_provider is None:
            self._record(
                "broker_policy",
                "short_order_skipped",
                intent.intent_id,
                "",
                intent.event_time,
                {"reason": "shortability_provider_unavailable", "ticker": intent.ticker},
            )
            raise ValueError("Short order skipped: IBKR shortability is unavailable")
        conid = plan.orders[0].conid
        snapshot = await self.shortability_provider.shortability(conid)
        if not snapshot.shortable or snapshot.available_shares < intent.quantity:
            self._record(
                "broker_policy",
                "short_order_skipped",
                intent.intent_id,
                plan.orders[0].acctId,
                intent.event_time,
                {
                    "reason": "insufficient_or_unavailable_borrow",
                    "ticker": intent.ticker,
                    "required_shares": intent.quantity,
                    "available_shares": snapshot.available_shares,
                    "classification": snapshot.classification,
                    "ibkr_fields": {"7636": snapshot.available_shares, "7644": snapshot.classification},
                },
            )
            raise ValueError("Short order skipped: IBKR does not report sufficient shortable shares")

    def _group_for_order(self, order: LiveOrder) -> _ManagedOrderGroup | None:
        group_id = self._group_by_broker_id.get(str(order.orderId))
        if not group_id and order.cOID:
            group_id = self._group_by_client_id.get(str(order.cOID))
        if not group_id and order.parentId:
            group_id = self._group_by_client_id.get(str(order.parentId))
        return self._groups.get(group_id) if group_id else None

    async def _on_canonical_order_state(self, order: OrderState) -> OrderGroupSnapshot | None:
        group_id = self._group_by_broker_id.get(order.broker_order_id)
        if not group_id and order.client_order_id:
            group_id = self._group_by_client_id.get(order.client_order_id)
        group = self._groups.get(group_id) if group_id else None
        if group is None:
            return None
        if order.broker_order_id and order.broker_order_id not in group.broker_order_ids:
            group.broker_order_ids.append(order.broker_order_id)
            self._group_by_broker_id[order.broker_order_id] = group.group_id
            group.broker_order_roles[order.broker_order_id] = _infer_order_role(
                order.order_type,
                bool(order.parent_order_id),
                str(group.intent.action),
            )
        group.filled_quantity = max(group.filled_quantity, float(order.filled_quantity))
        group.remaining_quantity = max(0.0, float(order.remaining_quantity))
        next_state = _canonical_management_state(order)
        self._transition(
            group,
            next_state,
            {
                "event": "broker_websocket_order_update",
                "broker_order_id": order.broker_order_id,
                "broker_status": order.broker_status_raw,
                "filled_quantity": order.filled_quantity,
                "remaining_quantity": order.remaining_quantity,
                "raw": order.raw,
            },
        )
        if next_state in TERMINAL_MANAGEMENT_STATES:
            if group.reprice_task:
                group.reprice_task.cancel()
            self.risk.release(group.account_id, group.orders)
        fill_role = group.broker_order_roles.get(order.broker_order_id, "")
        snapshot = group.snapshot(
            self.policy.version,
            action=_fill_action(group.intent, fill_role) if next_state == OrderManagementState.FILLED else None,
            fill_role=fill_role if next_state == OrderManagementState.FILLED else "",
        )
        if next_state == OrderManagementState.FILLED and self.fill_callback is not None:
            await self.fill_callback(snapshot)
        return snapshot

    def _transition(self, group: _ManagedOrderGroup, state: OrderManagementState, payload: dict[str, Any]) -> None:
        group.state = state
        group.updated_at = datetime.now(timezone.utc)
        self._record(
            "order_management",
            "order_group_state",
            group.group_id,
            group.account_id,
            group.updated_at,
            {
                **payload,
                "state": state.value,
                "intent_id": group.intent.intent_id,
                "action": group.intent.action,
                "ticker": group.intent.ticker,
                "policy_version": self.policy.version,
            },
        )

    def _record(
        self,
        category: str,
        entity_type: str,
        entity_id: str,
        account_id: str,
        event_time: datetime,
        payload: dict[str, Any],
    ) -> None:
        enriched = dict(payload)
        group_id = str(enriched.get("order_group_id") or "")
        group = self._groups.get(group_id) if group_id else None
        if group is not None:
            enriched.setdefault("ticker", group.intent.ticker)
            enriched.setdefault("action", group.intent.action)
            enriched.setdefault("intent_id", group.intent.intent_id)
        self.journal.append(
            run_id=self.run_id,
            category=category,
            entity_type=entity_type,
            entity_id=entity_id,
            account_id=account_id,
            event_time=event_time,
            payload={
                **enriched,
                "strategy_id": self.strategy_id,
                "strategy_revision": self.strategy_revision,
            },
        )


def execution_tactic(
    intent: StrategyIntent,
    policy: BrokerCommunicationPolicy,
    *,
    enforce_wall_clock_freshness: bool = True,
) -> ExecutionTactic | None:
    if str(intent.action) not in {
        "enter_long",
        "add_long",
        "reduce_long",
        "take_profit",
        "exit",
        "enter_short",
        "add_short",
        "reduce_short",
        "cover",
    }:
        return None
    bid = float(intent.metadata.get("bid") or 0)
    ask = float(intent.metadata.get("ask") or 0)
    tick_size = float(intent.metadata.get("tick_size") or 0)
    observed_raw = intent.metadata.get("quote_observed_at") or intent.event_time
    observed_at = observed_raw if isinstance(observed_raw, datetime) else datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
    if bid <= 0 or ask <= 0 or tick_size <= 0:
        return None
    quote = ExecutionQuote(bid=bid, ask=ask, observed_at=observed_at, tick_size=tick_size)
    age_ms = max(0.0, (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds() * 1000.0)
    if enforce_wall_clock_freshness and age_ms > policy.maximum_quote_age_ms:
        raise ValueError(f"Execution quote is stale ({age_ms:.0f} ms)")
    urgency = _normalize_urgency(intent.urgency)
    side = _intent_side(intent)
    steps = _price_steps(side, urgency, quote, policy.maximum_reprice_ticks)
    return ExecutionTactic(urgency, side, steps, quote, max(step.after_ms for step in steps))


def _price_steps(
    side: str,
    urgency: ExecutionUrgency,
    quote: ExecutionQuote,
    maximum_reprice_ticks: int,
) -> tuple[PriceStep, ...]:
    touch = quote.ask if side == "BUY" else quote.bid
    passive = quote.bid if side == "BUY" else quote.ask
    if urgency == ExecutionUrgency.URGENT:
        raw = ((0, touch),)
    elif urgency == ExecutionUrgency.VERY_URGENT:
        direction = 1 if side == "BUY" else -1
        raw = tuple(
            (index * 150, touch + direction * quote.tick_size * index)
            for index in range(maximum_reprice_ticks + 1)
        )
    elif urgency == ExecutionUrgency.PATIENT:
        raw = ((0, passive), (500, quote.midpoint))
    else:
        quarter = passive + (touch - passive) * 0.5
        three_quarter = passive + (touch - passive) * 0.75
        raw = ((0, quote.midpoint), (250, quarter), (500, three_quarter), (750, touch))
    deduplicated: list[PriceStep] = []
    for after_ms, value in raw:
        price = _round_to_tick(value, quote.tick_size, side)
        if not deduplicated or not math.isclose(deduplicated[-1].price, price):
            deduplicated.append(PriceStep(after_ms, price))
    return tuple(deduplicated)


def _apply_initial_tactic(
    orders: tuple[OrderRequest, ...],
    tactic: ExecutionTactic | None,
) -> tuple[OrderRequest, ...]:
    if tactic is None:
        return orders
    first_limit = next((index for index, order in enumerate(orders) if order.orderType == "LMT"), None)
    if first_limit is None:
        return orders
    result = list(orders)
    result[first_limit] = replace(result[first_limit], price=tactic.steps[0].price)
    return tuple(result)


def _intent_side(intent: StrategyIntent) -> str:
    return "BUY" if str(intent.action) in {"enter_long", "add_long", "reduce_short", "cover"} else "SELL"


def _order_role(order: OrderRequest, intent_action: str) -> str:
    if not order.parentId:
        return "entry" if intent_action in {"enter_long", "enter_short", "add_long", "add_short"} else "managed_exit"
    normalized_type = order.orderType.upper()
    if normalized_type == "LMT":
        return "profit_target"
    if normalized_type in {"STP", "STOP_LIMIT"}:
        return "protective_stop"
    if normalized_type in {"TRAIL", "TRAILLMT"}:
        return "trailing_stop"
    return "protective_exit"


def _infer_order_role(order_type: str, has_parent: bool, intent_action: str) -> str:
    if not has_parent:
        return "entry" if intent_action in {"enter_long", "enter_short", "add_long", "add_short"} else "managed_exit"
    normalized_type = order_type.upper()
    if normalized_type == "LMT":
        return "profit_target"
    if normalized_type in {"STP", "STOP_LIMIT"}:
        return "protective_stop"
    if normalized_type in {"TRAIL", "TRAILLMT"}:
        return "trailing_stop"
    return "protective_exit"


def _fill_action(intent: StrategyIntent, fill_role: str) -> str:
    if fill_role in {"profit_target", "protective_stop", "trailing_stop", "protective_exit"}:
        return "exit"
    return str(intent.action)


def _normalize_urgency(value: str) -> ExecutionUrgency:
    normalized = str(value or "").strip().lower()
    aliases = {
        "market": ExecutionUrgency.VERY_URGENT,
        "very_urgent": ExecutionUrgency.VERY_URGENT,
        "aggressive_limit": ExecutionUrgency.URGENT,
        "urgent": ExecutionUrgency.URGENT,
        "regular": ExecutionUrgency.REGULAR,
        "passive_limit": ExecutionUrgency.PATIENT,
        "patient": ExecutionUrgency.PATIENT,
    }
    if normalized not in aliases:
        raise ValueError(f"Unsupported execution urgency: {value}")
    return aliases[normalized]


def _round_to_tick(value: float, tick: float, side: str) -> float:
    units = value / tick
    rounded = math.ceil(units - 1e-9) if side == "BUY" else math.floor(units + 1e-9)
    decimals = max(0, min(8, int(math.ceil(-math.log10(tick))) if tick < 1 else 0))
    return round(rounded * tick, decimals)


def _management_state(order: LiveOrder) -> OrderManagementState:
    if order.order_status == OrderStatus.FILLED:
        return OrderManagementState.FILLED
    if order.order_status == OrderStatus.CANCELLED:
        return OrderManagementState.CANCELLED
    if order.order_status in {OrderStatus.PENDING_CANCEL, OrderStatus.PRE_CANCELLED}:
        return OrderManagementState.CANCEL_PENDING
    if order.filledQuantity > 0:
        return OrderManagementState.PARTIALLY_FILLED
    if order.order_status in OPEN_ORDER_STATUSES:
        return OrderManagementState.WORKING
    return OrderManagementState.OUTCOME_UNKNOWN


def _canonical_management_state(order: OrderState) -> OrderManagementState:
    mapping = {
        OrderLifecycleState.FILLED: OrderManagementState.FILLED,
        OrderLifecycleState.CANCELLED: OrderManagementState.CANCELLED,
        OrderLifecycleState.REJECTED: OrderManagementState.REJECTED,
        OrderLifecycleState.CANCEL_PENDING: OrderManagementState.CANCEL_PENDING,
        OrderLifecycleState.PARTIALLY_FILLED: OrderManagementState.PARTIALLY_FILLED,
        OrderLifecycleState.WORKING: OrderManagementState.WORKING,
        OrderLifecycleState.TRIGGER_PENDING: OrderManagementState.WORKING,
        OrderLifecycleState.PENDING_SUBMISSION: OrderManagementState.SUBMITTING,
    }
    return mapping.get(order.lifecycle_state, OrderManagementState.OUTCOME_UNKNOWN)


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in os.environ.get(name, "").split(",") if item.strip()))
