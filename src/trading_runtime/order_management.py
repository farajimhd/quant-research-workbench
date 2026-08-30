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
from src.request_context import causal_identity, normalize_request_identity
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.control_plane import TradingControlPlane
from src.trading_runtime.domain import OrderLifecycleState, OrderState
from src.trading_runtime.execution_policies import (
    AddProtectionPolicy,
    ExecutionMarketDataProvider,
    ExecutionMarketSnapshot,
    ExecutionPolicyName,
    PartialFillPolicy,
    ProfitPocketTransition,
    TrailingRuleType,
    execution_policy_from_payload,
    protection_profile_from_payload,
)
from src.trading_runtime.ibkr_normalizer import normalize_execution, normalize_order
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, LiveOrder, OrderRequest, OrderStatus
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import CapitalRequest, StrategyIntent
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
    broker_order_id: str = ""
    slice_id: str = ""
    fill_cumulative_quantity: float = 0.0
    fill_incremental_quantity: float = 0.0
    execution_policy: str = ""
    protection_profile: str = ""
    current_limit_price: float | None = None
    internal_reaction_ms: float | None = None
    protection_required_quantity: float = 0.0
    protection_coverage_quantity: float = 0.0
    high_water_price: float = 0.0
    low_water_price: float = 0.0
    protection_task: asyncio.Task[None] | None = None


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
    broker_order_slices: dict[str, str] = field(default_factory=dict)
    broker_order_request_indexes: dict[str, int] = field(default_factory=dict)
    filled_by_broker_order: dict[str, float] = field(default_factory=dict)
    terminal_broker_order_ids: set[str] = field(default_factory=set)
    warning_message_ids: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    submitted_at: datetime | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    decision_to_submit_ms: float | None = None
    reprice_task: asyncio.Task[None] | None = None
    reprice_event: asyncio.Event = field(default_factory=asyncio.Event)
    reprice_count: int = 0
    current_limit_price: float | None = None
    internal_reaction_ms: float | None = None
    protection_task: asyncio.Task[None] | None = None
    high_water_price: float = 0.0
    low_water_price: float = 0.0
    protection_required_quantity: float = 0.0
    protection_coverage_quantity: float = 0.0

    def snapshot(
        self,
        policy_version: int,
        *,
        action: str | None = None,
        fill_role: str = "",
        broker_order_id: str = "",
        slice_id: str = "",
        fill_cumulative_quantity: float = 0.0,
        fill_incremental_quantity: float = 0.0,
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
            broker_order_id=broker_order_id,
            slice_id=slice_id,
            fill_cumulative_quantity=fill_cumulative_quantity,
            fill_incremental_quantity=fill_incremental_quantity,
            execution_policy=self.intent.resolved_execution_policy().identity,
            protection_profile=(
                self.intent.resolved_protection_profile().identity
                if self.intent.resolved_protection_profile() is not None
                else ""
            ),
            current_limit_price=self.current_limit_price,
            internal_reaction_ms=self.internal_reaction_ms,
            protection_required_quantity=self.protection_required_quantity,
            protection_coverage_quantity=self.protection_coverage_quantity,
        )


PlanProvider = Callable[[StrategyIntent, str, MarketEvent | None], StrategyOrderPlan]
FillCallback = Callable[[OrderGroupSnapshot], Awaitable[None]]
StateCallback = Callable[[OrderGroupSnapshot], Awaitable[None]]


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
        state_callback: StateCallback | None = None,
        execution_market_data: ExecutionMarketDataProvider | None = None,
        enforce_wall_clock_quote_freshness: bool = False,
        control_plane: TradingControlPlane | None = None,
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
        self.state_callback = state_callback
        self.execution_market_data = execution_market_data or ExecutionMarketDataProvider()
        self.enforce_wall_clock_quote_freshness = enforce_wall_clock_quote_freshness
        self.control_plane = control_plane
        self._command_lanes: dict[str, asyncio.Lock] = {}
        self._warning_lane = (
            control_plane.warning_reply_lane
            if control_plane is not None
            else asyncio.Lock()
        )
        self._groups: dict[str, _ManagedOrderGroup] = {}
        self._group_by_client_id: dict[str, str] = {}
        self._group_by_broker_id: dict[str, str] = {}
        self._closed = False
        self._broker_connected = True

    def _command_lane(self, account_id: str) -> asyncio.Lock:
        if self.control_plane is not None:
            return self.control_plane.order_lane(account_id)
        return self._command_lanes.setdefault(account_id, asyncio.Lock())

    def on_market_snapshot(self, snapshot: ExecutionMarketSnapshot) -> None:
        self.execution_market_data.update(snapshot)
        for group in self._groups.values():
            if group.intent.ticker.upper() == snapshot.ticker.upper():
                group.reprice_event.set()
                group.high_water_price = max(group.high_water_price, snapshot.bid)
                group.low_water_price = (
                    snapshot.ask
                    if group.low_water_price <= 0
                    else min(group.low_water_price, snapshot.ask)
                )
                if group.protection_task is None or group.protection_task.done():
                    group.protection_task = asyncio.create_task(
                        self._ratchet_dynamic_protection(group, snapshot)
                    )

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

    async def recover(self) -> list[OrderGroupSnapshot]:
        for row in self.journal.order_management_states():
            payload = row["state"]
            persisted_strategy = str(payload.get("strategy_id") or "")
            persisted_revision = int(payload.get("strategy_revision") or 0)
            if persisted_strategy:
                if (
                    persisted_strategy != self.strategy_id
                    or persisted_revision != self.strategy_revision
                ):
                    continue
            elif str(row.get("run_id") or "") != self.run_id:
                # Legacy rows lacked strategy identity and are safe to recover
                # only inside their original run.
                continue
            persisted_state = OrderManagementState(str(payload["state"]))
            if persisted_state in {
                OrderManagementState.CANCELLED,
                OrderManagementState.REJECTED,
                OrderManagementState.POLICY_BLOCKED,
            }:
                continue
            group_id = str(row["group_id"])
            if group_id in self._groups:
                continue
            intent = _intent_from_payload(payload["intent"])
            orders = tuple(
                OrderRequest.from_cpapi(item, account_id=str(row["account_id"]))
                for item in payload.get("orders") or ()
            )
            batch_lengths = tuple(int(value) for value in payload.get("batch_lengths") or ())
            batches: list[tuple[OrderRequest, ...]] = []
            offset = 0
            for length in batch_lengths:
                batches.append(orders[offset : offset + length])
                offset += length
            plan = StrategyOrderPlan(
                orders=orders,
                cancel_strategy_protection=bool(payload.get("cancel_strategy_protection")),
                protection_reconciliation_required=bool(
                    payload.get("protection_reconciliation_required")
                ),
                batches=tuple(batches),
                order_slice_ids=tuple(payload.get("order_slice_ids") or ()),
            )
            quote = self._execution_quote(intent)
            tactic = execution_tactic(
                intent,
                self.policy,
                quote=quote,
                enforce_wall_clock_freshness=False,
            )
            group = _ManagedOrderGroup(
                group_id=group_id,
                intent=intent,
                account_id=str(row["account_id"]),
                plan=plan,
                state=persisted_state,
                created_at=_aware(payload["created_at"]),
                updated_at=_aware(payload["updated_at"]),
                orders=list(orders),
                tactic=tactic,
                broker_order_ids=[str(value) for value in payload.get("broker_order_ids") or ()],
                broker_order_roles={
                    str(key): str(value)
                    for key, value in (payload.get("broker_order_roles") or {}).items()
                },
                broker_order_slices={
                    str(key): str(value)
                    for key, value in (payload.get("broker_order_slices") or {}).items()
                },
                broker_order_request_indexes={
                    str(key): int(value)
                    for key, value in (payload.get("broker_order_request_indexes") or {}).items()
                },
                filled_by_broker_order={
                    str(key): float(value)
                    for key, value in (payload.get("filled_by_broker_order") or {}).items()
                },
                terminal_broker_order_ids={
                    str(value)
                    for value in payload.get("terminal_broker_order_ids") or ()
                },
                filled_quantity=float(payload.get("filled_quantity") or 0),
                remaining_quantity=float(payload.get("remaining_quantity") or 0),
                current_limit_price=(
                    float(payload["current_limit_price"])
                    if payload.get("current_limit_price") is not None
                    else None
                ),
                protection_required_quantity=float(
                    payload.get("protection_required_quantity") or 0
                ),
                protection_coverage_quantity=float(
                    payload.get("protection_coverage_quantity") or 0
                ),
            )
            self._groups[group_id] = group
            for request in group.orders:
                if request.cOID:
                    self._group_by_client_id[request.cOID] = group_id
            for broker_order_id in group.broker_order_ids:
                self._group_by_broker_id[broker_order_id] = group_id
        return await self.reconcile()

    def _require_portfolio_approval(
        self, intent: StrategyIntent, account_id: str
    ) -> None:
        decision_id = str(intent.metadata.get("portfolio_decision_id") or "")
        reservation_id = str(intent.metadata.get("portfolio_reservation_id") or "")
        if not decision_id or not reservation_id:
            raise ValueError(
                "OMS requires a durable Portfolio decision and reservation"
            )
        reservation = self.journal.portfolio_reservation(account_id, reservation_id)
        if reservation is None:
            raise ValueError(
                "OMS cannot verify the Portfolio reservation for this account"
            )
        mismatches = []
        if str(reservation.get("decision_id") or "") != decision_id:
            mismatches.append("decision_id")
        if str(reservation.get("intent_id") or "") != intent.intent_id:
            mismatches.append("intent_id")
        if str(reservation.get("account_id") or "") != account_id:
            mismatches.append("account_id")
        if str(reservation.get("ticker") or "").upper() != intent.ticker.upper():
            mismatches.append("ticker")
        if not math.isclose(
            float(reservation.get("quantity") or 0),
            float(intent.quantity),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            mismatches.append("quantity")
        if str(reservation.get("status") or "") != "reserved":
            mismatches.append("status")
        if mismatches:
            raise ValueError(
                "OMS rejected a stale or mismatched Portfolio reservation: "
                + ", ".join(mismatches)
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
        self._require_portfolio_approval(intent, account_id)
        persisted_intent_ids = {
            str(dict(row.get("state") or {}).get("intent", {}).get("intent_id") or "")
            for row in self.journal.order_management_states()
        }
        if intent.intent_id in persisted_intent_ids or intent.intent_id in (
            group.intent.intent_id for group in self._groups.values()
        ):
            raise ValueError(f"Strategy intent has already been submitted: {intent.intent_id}")
        plan = self.planner(intent, account_id, event)
        if not plan.orders:
            raise ValueError(f"Strategy intent produced no broker order plan: {intent.action}")
        await self._require_shortability(intent, plan)
        quote = self._execution_quote(intent)
        tactic = execution_tactic(
            intent,
            self.policy,
            quote=quote,
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
        now = self._causal_group_time(intent)
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
            current_limit_price=(tactic.steps[0].price if tactic and tactic.steps else None),
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
            intent=intent,
            require_fresh=self.enforce_wall_clock_quote_freshness,
        )
        self.risk.reserve(account_id, group.orders)
        self._transition(group, OrderManagementState.RISK_RESERVED, {"event": "risk_reserved"})
        await self._submit(group)
        if plan.cancel_strategy_protection:
            await self.cancel_strategy_protection(
                account_id=account_id,
                ticker=intent.ticker,
                client_id_prefix=self._protective_order_prefix(),
                event_time=intent.event_time,
                exclude_order_ids=tuple(group.broker_order_ids),
            )
        return group.snapshot(self.policy.version)

    def _execution_quote(self, intent: StrategyIntent) -> ExecutionQuote | None:
        snapshot = self.execution_market_data.snapshot(intent.ticker)
        if snapshot is None:
            bid = float(intent.metadata.get("bid") or 0)
            ask = float(intent.metadata.get("ask") or 0)
            tick_size = float(intent.metadata.get("tick_size") or 0)
            if bid > 0 and ask >= bid and tick_size > 0:
                observed_raw = intent.metadata.get("quote_observed_at") or intent.event_time
                observed_at = (
                    observed_raw
                    if isinstance(observed_raw, datetime)
                    else datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
                )
                snapshot = ExecutionMarketSnapshot(
                    ticker=intent.ticker,
                    bid=bid,
                    ask=ask,
                    tick_size=tick_size,
                    observed_at=observed_at,
                    source=str(intent.metadata.get("execution_quote_source") or "strategy_compatibility"),
                    volatility=float(intent.metadata.get("volatility") or 0),
                )
                self.execution_market_data.update(snapshot)
        if snapshot is None:
            return None
        return ExecutionQuote(
            bid=snapshot.bid,
            ask=snapshot.ask,
            observed_at=snapshot.observed_at,
            tick_size=snapshot.tick_size,
        )

    async def cancel_strategy_protection(
        self,
        *,
        account_id: str,
        ticker: str,
        client_id_prefix: str,
        event_time: datetime,
        exclude_order_ids: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Request cancellation and retain pending state until broker confirmation."""
        responses: list[dict[str, Any]] = []
        live_orders = await self.broker.live_orders()
        candidates = [
            order
            for order in live_orders
            if order.account == account_id
            and order.ticker.upper() == ticker.upper()
            and order.order_status in OPEN_ORDER_STATUSES
            and str(order.orderId) not in set(exclude_order_ids)
            and (
                str(order.parentId or "").startswith(client_id_prefix)
                or str(order.cOID or "").startswith(client_id_prefix)
            )
        ]
        # Cancelling a parent can synchronously cancel its attached children.
        # Cancel children and standalone repair backstops first so the snapshot
        # does not become stale midway through this deterministic batch.
        candidates.sort(
            key=lambda order: (
                0
                if order.parentId
                else 1
                if "repair-" in str(order.cOID or "")
                else 2,
                str(order.orderId),
            )
        )
        async with self._command_lane(account_id):
            for order in candidates:
                self._record(
                    "command",
                    "order_cancel",
                    str(order.orderId),
                    account_id,
                    event_time,
                    {"reason": "replace_strategy_protection", "ticker": ticker.upper()},
                )
                try:
                    response = await self.broker.cancel_order(
                        account_id, str(order.orderId)
                    )
                except Exception:
                    refreshed = next(
                        (
                            row
                            for row in await self.broker.live_orders()
                            if str(row.orderId) == str(order.orderId)
                        ),
                        None,
                    )
                    if (
                        refreshed is not None
                        and refreshed.order_status in OPEN_ORDER_STATUSES
                    ):
                        raise
                    response = {
                        "msg": "Order was already terminal during protection replacement",
                        "order_id": str(order.orderId),
                        "status": (
                            refreshed.order_status.value
                            if refreshed is not None
                            else "not_found"
                        ),
                    }
                responses.append(response)
                group = self._group_for_order(order)
                if group is not None:
                    self._transition(group, OrderManagementState.CANCEL_PENDING, response)
                self._record("broker", "order_cancel_requested", str(order.orderId), account_id, event_time, response)
        return responses

    def _protective_order_prefix(self) -> str:
        return f"{self.strategy_id[:14]}-v{self.strategy_revision}-"

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
            group.broker_order_slices[order.orderId] = _slice_for_live_order(group, order)
            request_index = _request_index_for_identity(group, str(order.cOID or order.parentId or ""))
            if request_index is not None:
                group.broker_order_request_indexes[order.orderId] = request_index
        fill_role = group.broker_order_roles.get(str(order.orderId), "")
        incremental = _apply_cumulative_fill(
            group,
            str(order.orderId),
            float(order.filledQuantity),
            fill_role,
        )
        next_state = _management_state(order)
        if next_state in TERMINAL_MANAGEMENT_STATES:
            group.terminal_broker_order_ids.add(str(order.orderId))
        if fill_role == "entry" and float(order.filledQuantity) > 0:
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
            )
        elif fill_role == "managed_exit" and float(order.filledQuantity) > 0:
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
            )
        elif (
            fill_role in {"profit_target", "protective_stop", "trailing_stop"}
            and incremental <= 0
            and next_state in TERMINAL_MANAGEMENT_STATES
        ):
            # One cancelled/rejected protective sibling does not make the
            # semantic position terminal. Keep the group live and immediately
            # reconcile aggregate broker-held protection.
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
                if group.filled_quantity > 0
                else OrderManagementState.WORKING
            )
        self._transition(group, next_state, {"event": "broker_order_update", "order": order.to_cpapi()})
        if next_state in TERMINAL_MANAGEMENT_STATES and group.reprice_task:
            group.reprice_task.cancel()
        if next_state in TERMINAL_MANAGEMENT_STATES:
            self.risk.release(group.account_id, group.orders)
        snapshot = group.snapshot(
            self.policy.version,
            action=_fill_action(group.intent, fill_role) if incremental > 0 else None,
            fill_role=fill_role,
            broker_order_id=str(order.orderId),
            slice_id=group.broker_order_slices.get(str(order.orderId), ""),
            fill_cumulative_quantity=float(order.filledQuantity),
            fill_incremental_quantity=incremental,
        )
        if self.state_callback is not None:
            await self.state_callback(snapshot)
        if incremental > 0 and self.fill_callback is not None:
            await self.fill_callback(snapshot)
        if (
            incremental > 0
            and fill_role in {"profit_target", "protective_stop", "trailing_stop"}
        ):
            # A child exit filling while its entry root is still working can
            # flatten the partial position and then let the parent reopen it on
            # a later print.  Once risk reduction begins, freeze the acquired
            # quantity by cancelling every unfilled entry remainder first.
            await self._cancel_open_entry_roots(
                group,
                "protective_exit_started_before_entry_complete",
            )
        protective_terminal_without_fill = (
            fill_role in {"profit_target", "protective_stop", "trailing_stop"}
            and incremental <= 0
            and _management_state(order) in TERMINAL_MANAGEMENT_STATES
        )
        if incremental > 0 or protective_terminal_without_fill:
            group.reprice_event.set()
            if group.plan.protection_reconciliation_required or fill_role != "managed_exit":
                await self.reconcile_protection(group)
                if incremental > 0 and str(group.intent.action) in {"take_profit", "reduce_long", "reduce_short", "cover"}:
                    await self.apply_profit_pocket_transition(group)
                if self.state_callback is not None:
                    await self.state_callback(group.snapshot(self.policy.version))
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

    async def kill_entries(self, account_id: str, *, reason: str) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        for group in self._groups.values():
            if group.account_id != account_id or group.remaining_quantity <= 0:
                continue
            for broker_order_id, _ in _open_entry_roots(group):
                try:
                    async with self._command_lane(account_id):
                        response = await self.broker.cancel_order(account_id, broker_order_id)
                except Exception as exc:
                    response = {"error": str(exc)}
                responses.append(response)
                self._record(
                    "risk",
                    "kill_entry_order",
                    broker_order_id,
                    account_id,
                    datetime.now(timezone.utc),
                    {"order_group_id": group.group_id, "reason": reason, "broker_response": response},
                )
            if _open_entry_roots(group):
                self._transition(
                    group,
                    OrderManagementState.CANCEL_PENDING,
                    {"event": "risk_kill_entries", "reason": reason},
                )
        return responses

    async def emergency_flatten(self, account_id: str, *, reason: str) -> list[dict[str, Any]]:
        if not self._broker_connected:
            raise RuntimeError("Emergency flatten requires a connected broker session")
        await self.reconcile()
        await self.kill_entries(account_id, reason=reason)
        responses: list[dict[str, Any]] = []
        for position in await self.broker.positions(account_id):
            quantity = abs(float(position.position))
            if quantity <= 1e-9:
                continue
            ticker = str(position.contractDesc or position.raw.get("ticker") or "").upper()
            snapshot = self.execution_market_data.snapshot(ticker)
            if snapshot is None:
                raise RuntimeError(f"Emergency flatten requires a fresh execution quote for {ticker}")
            age_ms = (
                datetime.now(timezone.utc) - snapshot.observed_at.astimezone(timezone.utc)
            ).total_seconds() * 1_000
            if self.enforce_wall_clock_quote_freshness and age_ms > self.policy.maximum_quote_age_ms:
                raise RuntimeError(f"Emergency flatten quote is stale for {ticker}")
            long_position = float(position.position) > 0
            side = "SELL" if long_position else "BUY"
            limit_price = (
                snapshot.bid - snapshot.tick_size * self.policy.maximum_reprice_ticks
                if long_position
                else snapshot.ask + snapshot.tick_size * self.policy.maximum_reprice_ticks
            )
            stop_price = (
                max(snapshot.tick_size, snapshot.bid * 0.98)
                if long_position
                else snapshot.ask * 1.02
            )
            prefix = f"risk-flatten-{uuid4().hex[:12]}"
            orders = [
                OrderRequest(
                    acctId=account_id,
                    conid=int(position.conid),
                    cOID=f"{prefix}-limit",
                    ticker=ticker,
                    orderType="LMT",
                    side=side,
                    quantity=quantity,
                    price=_round_to_tick(limit_price, snapshot.tick_size, side),
                    tif="DAY",
                    isSingleGroup=True,
                ),
                OrderRequest(
                    acctId=account_id,
                    conid=int(position.conid),
                    cOID=f"{prefix}-stop",
                    ticker=ticker,
                    orderType="STP",
                    side=side,
                    quantity=quantity,
                    auxPrice=_round_to_tick(stop_price, snapshot.tick_size, side),
                    tif="DAY",
                    isSingleGroup=True,
                ),
            ]
            async with self._command_lane(account_id):
                response = await self.broker.place_orders(account_id, orders)
            if _warning_response(response):
                raise RuntimeError(
                    "Emergency flatten returned an unresolved broker warning; existing protection was retained"
                )
            responses.extend(response)
            self._record(
                "risk",
                "emergency_flatten",
                prefix,
                account_id,
                datetime.now(timezone.utc),
                {
                    "reason": reason,
                    "ticker": ticker,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "fallback_stop": stop_price,
                    "broker_response": response,
                },
            )
            await self.cancel_strategy_protection(
                account_id=account_id,
                ticker=ticker,
                client_id_prefix=self._protective_order_prefix(),
                event_time=datetime.now(timezone.utc),
            )
        return responses

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

    def snapshot_for_intent(self, intent_id: str) -> OrderGroupSnapshot | None:
        group = next((row for row in self._groups.values() if row.intent.intent_id == intent_id), None)
        return group.snapshot(self.policy.version) if group is not None else None

    async def close(self) -> None:
        self._closed = True
        tasks = [
            task
            for group in self._groups.values()
            for task in (group.reprice_task, group.protection_task)
            if task
        ]
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
        response: list[dict[str, Any]] = []
        response_request_indexes: list[int] = []
        try:
            offset = 0
            for batch in _group_batches(group):
                async with self._command_lane(group.account_id):
                    batch_response = await self.broker.place_orders(group.account_id, list(batch))
                if _warning_response(batch_response):
                    async with self._warning_lane:
                        batch_response = await self._resolve_warning_chain_locked(group, batch_response)
                response.extend(batch_response)
                response_request_indexes.extend(
                    min(offset + index, len(group.orders) - 1)
                    for index in range(len(batch_response))
                )
                offset += len(batch)
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
                request_index = response_request_indexes[min(index, len(response_request_indexes) - 1)]
                request = group.orders[request_index]
                group.broker_order_roles[order_id] = _order_role(
                    request,
                    str(group.intent.action),
                )
                group.broker_order_request_indexes[order_id] = request_index
                if group.plan.order_slice_ids:
                    group.broker_order_slices[order_id] = group.plan.order_slice_ids[request_index]
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
        # Portfolio admission floors executable quantities to six decimal places.
        # Compare against that same executable quantity so a full-position exit is
        # not misclassified as partial solely because the broker position retained
        # more floating-point precision.
        executable_position_quantity = math.floor(position_quantity * 1_000_000) / 1_000_000
        if intent.quantity + 1e-9 < executable_position_quantity:
            raise ValueError(
                "Partial protected exit is blocked: CPAPI isSingleGroup does not guarantee "
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
            async with self._command_lane(account_id):
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
        execution_policy = group.intent.resolved_execution_policy()
        envelope = execution_policy.envelope
        started = perf_counter()
        interval_ms = _adaptive_interval_ms(execution_policy.name)
        while group.reprice_count < envelope.maximum_reprices:
            elapsed_ms = (perf_counter() - started) * 1_000
            if elapsed_ms >= envelope.deadline_ms:
                break
            timeout = min(interval_ms, max(0.0, envelope.deadline_ms - elapsed_ms)) / 1_000
            try:
                await asyncio.wait_for(group.reprice_event.wait(), timeout=timeout)
            except TimeoutError:
                pass
            group.reprice_event.clear()
            reaction_started = perf_counter()
            if group.filled_quantity >= float(group.intent.quantity):
                return
            if group.state in {
                OrderManagementState.CANCELLED,
                OrderManagementState.REJECTED,
                OrderManagementState.POLICY_BLOCKED,
                OrderManagementState.OUTCOME_UNKNOWN,
            }:
                return
            if group.filled_quantity > 0:
                if execution_policy.partial_fill_policy == PartialFillPolicy.CANCEL_REMAINDER:
                    await self._cancel_open_entry_roots(group, "partial_fill_policy")
                    return
                if execution_policy.partial_fill_policy == PartialFillPolicy.ACCEPT_PARTIAL:
                    await self._cancel_open_entry_roots(group, "accept_partial")
                    return
            quote = self._execution_quote(group.intent) or group.tactic.quote
            age_ms = (
                datetime.now(timezone.utc) - quote.observed_at.astimezone(timezone.utc)
            ).total_seconds() * 1_000
            if self.enforce_wall_clock_quote_freshness and age_ms > self.policy.maximum_quote_age_ms:
                self._record(
                    "order_management",
                    "adaptive_reprice_skipped",
                    group.group_id,
                    group.account_id,
                    self._causal_group_time(
                        group.intent,
                        previous=group.updated_at,
                    ),
                    {"reason": "quote_stale", "quote_age_ms": age_ms},
                )
                continue
            requested_price = _adaptive_price(
                side=group.tactic.side,
                policy_name=execution_policy.name,
                quote=quote,
                reprice_index=group.reprice_count + 1,
                maximum_reprice_ticks=self.policy.maximum_reprice_ticks,
            )
            requested_price = envelope.bound(group.tactic.side, requested_price)
            if group.current_limit_price is not None and math.isclose(
                requested_price,
                group.current_limit_price,
                abs_tol=quote.tick_size / 2,
            ):
                continue
            modified = False
            for root_order_id, request_index in _open_entry_roots(group):
                replacement = replace(group.orders[request_index], price=requested_price)
                try:
                    async with self._command_lane(group.account_id):
                        response = await self.broker.modify_order(
                            group.account_id,
                            root_order_id,
                            replacement,
                        )
                    if _warning_response(response):
                        async with self._warning_lane:
                            response = await self._resolve_warning_chain_locked(group, response)
                except Exception as exc:
                    self._record(
                        "broker",
                        "order_reprice_error",
                        root_order_id,
                        group.account_id,
                        datetime.now(timezone.utc),
                        {
                            "order_group_id": group.group_id,
                            "error": str(exc),
                            "requested_price": requested_price,
                        },
                    )
                    await self.reconcile()
                    continue
                group.orders[request_index] = replacement
                modified = True
                self._record(
                    "broker",
                    "order_repriced",
                    root_order_id,
                    group.account_id,
                    datetime.now(timezone.utc),
                    {
                        "order_group_id": group.group_id,
                        "requested_price": requested_price,
                        "remaining_quantity": group.remaining_quantity,
                        "quote_observed_at": quote.observed_at.isoformat(),
                        "quote_bid": quote.bid,
                        "quote_ask": quote.ask,
                        "broker_response": response,
                    },
                )
            if modified:
                group.reprice_count += 1
                group.current_limit_price = requested_price
                group.internal_reaction_ms = (perf_counter() - reaction_started) * 1_000
        if group.remaining_quantity > 0 and execution_policy.name == ExecutionPolicyName.CANCEL_IF_NOT_FILLED:
            await self._cancel_open_entry_roots(group, "execution_deadline")

    async def _cancel_open_entry_roots(self, group: _ManagedOrderGroup, reason: str) -> bool:
        roots = _open_entry_roots(group)
        if not roots:
            return False
        for broker_order_id, _ in roots:
            async with self._command_lane(group.account_id):
                response = await self.broker.cancel_order(group.account_id, broker_order_id)
            self._record(
                "broker",
                "order_cancel_requested",
                broker_order_id,
                group.account_id,
                datetime.now(timezone.utc),
                {"order_group_id": group.group_id, "reason": reason, "broker_response": response},
            )
        self._transition(group, OrderManagementState.CANCEL_PENDING, {"event": "adaptive_cancel_requested", "reason": reason})
        return True

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
            group.broker_order_slices[order.broker_order_id] = _slice_for_canonical_order(group, order)
            request_index = _request_index_for_identity(
                group,
                str(order.client_order_id or order.parent_order_id or ""),
            )
            if request_index is not None:
                group.broker_order_request_indexes[order.broker_order_id] = request_index
        fill_role = group.broker_order_roles.get(order.broker_order_id, "")
        incremental = _apply_cumulative_fill(
            group,
            order.broker_order_id,
            float(order.filled_quantity),
            fill_role,
        )
        next_state = _canonical_management_state(order)
        if next_state in TERMINAL_MANAGEMENT_STATES:
            group.terminal_broker_order_ids.add(order.broker_order_id)
        if fill_role == "entry" and float(order.filled_quantity) > 0:
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
            )
        elif fill_role == "managed_exit" and float(order.filled_quantity) > 0:
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
            )
        elif (
            fill_role in {"profit_target", "protective_stop", "trailing_stop"}
            and incremental <= 0
            and next_state in TERMINAL_MANAGEMENT_STATES
        ):
            next_state = (
                OrderManagementState.FILLED
                if group.remaining_quantity <= 1e-9
                else OrderManagementState.PARTIALLY_FILLED
                if group.filled_quantity > 0
                else OrderManagementState.WORKING
            )
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
        snapshot = group.snapshot(
            self.policy.version,
            action=_fill_action(group.intent, fill_role) if incremental > 0 else None,
            fill_role=fill_role,
            broker_order_id=order.broker_order_id,
            slice_id=group.broker_order_slices.get(order.broker_order_id, ""),
            fill_cumulative_quantity=float(order.filled_quantity),
            fill_incremental_quantity=incremental,
        )
        if self.state_callback is not None:
            await self.state_callback(snapshot)
        if incremental > 0 and self.fill_callback is not None:
            await self.fill_callback(snapshot)
        if (
            incremental > 0
            and fill_role in {"profit_target", "protective_stop", "trailing_stop"}
        ):
            await self._cancel_open_entry_roots(
                group,
                "protective_exit_started_before_entry_complete",
            )
        protective_terminal_without_fill = (
            fill_role in {"profit_target", "protective_stop", "trailing_stop"}
            and incremental <= 0
            and order.terminal
        )
        if incremental > 0 or protective_terminal_without_fill:
            group.reprice_event.set()
            if group.plan.protection_reconciliation_required or fill_role != "managed_exit":
                await self.reconcile_protection(group)
                if incremental > 0 and str(group.intent.action) in {"take_profit", "reduce_long", "reduce_short", "cover"}:
                    await self.apply_profit_pocket_transition(group)
                if self.state_callback is not None:
                    await self.state_callback(group.snapshot(self.policy.version))
        return snapshot

    async def reconcile_protection(self, group: _ManagedOrderGroup) -> dict[str, Any]:
        positions = await self.broker.positions(group.account_id)
        position = next(
            (
                row
                for row in positions
                if str(row.contractDesc or row.raw.get("ticker") or "").upper()
                == group.intent.ticker.upper()
            ),
            None,
        )
        position_quantity = float(position.position) if position is not None else 0.0
        required = abs(position_quantity)
        live_orders = await self.broker.live_orders()
        protective = [
            order
            for order in live_orders
            if order.account == group.account_id
            and order.ticker.upper() == group.intent.ticker.upper()
            and order.order_status in OPEN_ORDER_STATUSES
            and order.order_status != OrderStatus.INACTIVE
            and order.orderType.upper() in {"STP", "STOP_LIMIT", "TRAIL", "TRAILLMT"}
            and (
                str(order.parentId or "").startswith(self._protective_order_prefix())
                or str(order.cOID or "").startswith(self._protective_order_prefix())
            )
        ]
        by_protection_group: dict[str, float] = {}
        protection_groups: dict[str, list[LiveOrder]] = {}
        for order in protective:
            key = _protection_group_key(order)
            protection_groups.setdefault(key, []).append(order)
            by_protection_group[key] = max(
                by_protection_group.get(key, 0.0),
                float(order.remainingQuantity),
            )
        coverage = sum(by_protection_group.values())
        tolerance = 1e-9
        actions: list[dict[str, Any]] = []
        if coverage > required + tolerance:
            excess = coverage - required
            ordered_groups = sorted(
                protection_groups.items(),
                key=lambda item: (
                    0 if "repair-" in item[0] else 1,
                    item[0],
                ),
            )
            for key, orders in ordered_groups:
                if excess <= tolerance:
                    break
                current_remaining = by_protection_group[key]
                reduction = min(excess, current_remaining)
                target_remaining = max(0.0, current_remaining - reduction)
                for order in orders:
                    managed = self._group_for_order(order)
                    request_index = (
                        managed.broker_order_request_indexes.get(str(order.orderId))
                        if managed is not None
                        else None
                    )
                    if target_remaining <= tolerance:
                        async with self._command_lane(group.account_id):
                            response = await self.broker.cancel_order(
                                group.account_id, str(order.orderId)
                            )
                        actions.append({
                            "action": "cancel_excess",
                            "order_id": str(order.orderId),
                            "response": response,
                        })
                        continue
                    if request_index is None:
                        raise RuntimeError(
                            f"Protection order {order.orderId} is not registered for safe resize"
                        )
                    request = managed.orders[request_index]
                    replacement = replace(
                        request,
                        quantity=float(order.filledQuantity) + min(
                            float(order.remainingQuantity), target_remaining
                        ),
                    )
                    async with self._command_lane(group.account_id):
                        response = await self.broker.modify_order(
                            group.account_id,
                            str(order.orderId),
                            replacement,
                        )
                    managed.orders[request_index] = replacement
                    actions.append({
                        "action": "resize_excess",
                        "order_id": str(order.orderId),
                        "quantity": replacement.quantity,
                        "response": response,
                    })
                excess -= reduction
        elif required > coverage + tolerance:
            profile = group.intent.resolved_protection_profile()
            if profile is None or not group.orders:
                self._transition(
                    group,
                    OrderManagementState.POLICY_BLOCKED,
                    {"event": "protection_repair_failed", "reason": "protection_profile_unavailable"},
                )
                return {"required": required, "coverage": coverage, "status": "failed"}
            position_side = "long" if position_quantity >= 0 else "short"
            volatility = float(group.intent.metadata.get("volatility") or 0)
            stops = [
                item.stop.resolve(
                    reference_price=float(group.intent.reference_price),
                    side=position_side,
                    quantity=max(required, 1e-9) * item.quantity_fraction,
                    volatility=volatility,
                )
                for item in profile.slices
            ]
            stop_price = min(stops) if position_side == "long" else max(stops)
            missing = required - coverage
            existing_repair = next(
                (
                    order
                    for order in protective
                    if "repair-" in str(order.cOID or "")
                    and str(order.orderId) in group.broker_order_request_indexes
                ),
                None,
            )
            if existing_repair is not None:
                order_id = str(existing_repair.orderId)
                request_index = group.broker_order_request_indexes[order_id]
                request = group.orders[request_index]
                replacement = replace(
                    request,
                    quantity=(
                        float(existing_repair.filledQuantity)
                        + float(existing_repair.remainingQuantity)
                        + missing
                    ),
                    auxPrice=stop_price,
                )
                async with self._command_lane(group.account_id):
                    response = await self.broker.modify_order(
                        group.account_id,
                        order_id,
                        replacement,
                    )
                group.orders[request_index] = replacement
                actions.append({
                    "action": "resize_missing_backstop",
                    "order_id": order_id,
                    "quantity": replacement.quantity,
                    "stop_price": stop_price,
                    "response": response,
                })
                missing = 0.0
            if missing <= tolerance:
                repair = None
            else:
                repair = OrderRequest(
                    acctId=group.account_id,
                    conid=group.orders[0].conid,
                    secType=group.orders[0].secType,
                    cOID=f"{self._protective_order_prefix()}repair-{uuid4().hex[:12]}",
                    ticker=group.orders[0].ticker,
                    orderType="STP",
                    side="SELL" if position_side == "long" else "BUY",
                    quantity=missing,
                    tif=group.orders[0].tif,
                    outsideRTH=group.orders[0].outsideRTH,
                    auxPrice=stop_price,
                    listingExchange=group.orders[0].listingExchange,
                    raw=dict(group.orders[0].raw),
                )
            if repair is not None:
                async with self._command_lane(group.account_id):
                    response = await self.broker.place_orders(group.account_id, [repair])
                request_index = len(group.orders)
                group.orders.append(repair)
                group.plan = _extend_managed_plan(
                    group.plan,
                    group.orders,
                    slice_id="repair-backstop",
                )
                if repair.cOID:
                    self._group_by_client_id[repair.cOID] = group.group_id
                for row in response:
                    order_id = str(row.get("order_id") or row.get("orderId") or "")
                    if not order_id:
                        continue
                    if order_id not in group.broker_order_ids:
                        group.broker_order_ids.append(order_id)
                    self._group_by_broker_id[order_id] = group.group_id
                    group.broker_order_roles[order_id] = "protective_stop"
                    group.broker_order_request_indexes[order_id] = request_index
                actions.append(
                    {
                        "action": "place_missing_backstop",
                        "quantity": repair.quantity,
                        "stop_price": stop_price,
                        "response": response,
                    }
                )
        actions.extend(
            await self._apply_add_protection_policy(
                group,
                live_orders=live_orders,
                position_quantity=position_quantity,
            )
        )
        result = {
            "required_quantity": required,
            "protected_quantity": coverage,
            "actions": actions,
            "status": "repaired" if actions else "reconciled",
        }
        group.protection_required_quantity = required
        group.protection_coverage_quantity = required if actions else coverage
        self._record(
            "order_management",
            "protection_reconciliation",
            group.group_id,
            group.account_id,
            self._causal_group_time(
                group.intent,
                previous=group.updated_at,
            ),
            {"order_group_id": group.group_id, **result},
        )
        return result

    async def _apply_add_protection_policy(
        self,
        group: _ManagedOrderGroup,
        *,
        live_orders: list[LiveOrder],
        position_quantity: float,
    ) -> list[dict[str, Any]]:
        if str(group.intent.action) not in {"add_long", "add_short"}:
            return []
        profile = group.intent.resolved_protection_profile()
        if profile is None or profile.add_policy in {
            AddProtectionPolicy.INDEPENDENT_SLICE,
            AddProtectionPolicy.PRESERVE_EXISTING,
        }:
            return []
        long_position = position_quantity >= 0
        volatility = float(group.intent.metadata.get("volatility") or 0)
        inherited = float(group.intent.metadata.get("position_stop_price") or 0)
        by_slice = {item.slice_id: item for item in profile.slices}
        actions: list[dict[str, Any]] = []
        for order in live_orders:
            if (
                order.account != group.account_id
                or order.ticker.upper() != group.intent.ticker.upper()
                or order.order_status not in OPEN_ORDER_STATUSES
                or order.orderType.upper() not in {"STP", "STOP_LIMIT"}
            ):
                continue
            managed = self._group_for_order(order)
            if managed is None:
                continue
            request_index = managed.broker_order_request_indexes.get(str(order.orderId))
            if request_index is None:
                continue
            request = managed.orders[request_index]
            current_stop = float(request.auxPrice or order.auxPrice or 0)
            if current_stop <= 0:
                continue
            if profile.add_policy == AddProtectionPolicy.INHERIT_POSITION_STOP:
                desired_stop = inherited
                if desired_stop <= 0:
                    raise ValueError(
                        "inherit_position_stop add policy requires position_stop_price"
                    )
            else:
                slice_id = managed.broker_order_slices.get(str(order.orderId), "")
                rule = by_slice.get(slice_id, profile.slices[0]).stop
                desired_stop = rule.resolve(
                    reference_price=float(group.intent.reference_price),
                    side="long" if long_position else "short",
                    quantity=max(abs(position_quantity), 1e-9),
                    volatility=volatility,
                )
            if profile.add_policy == AddProtectionPolicy.TIGHTEN_ONLY:
                next_stop = (
                    max(current_stop, desired_stop)
                    if long_position
                    else min(current_stop, desired_stop)
                )
            else:
                next_stop = desired_stop
            if math.isclose(next_stop, current_stop, abs_tol=1e-9):
                continue
            replacement = replace(request, auxPrice=next_stop)
            if replacement.orderType.upper() == "STOP_LIMIT" and replacement.price is not None:
                offset = abs(float(replacement.price) - current_stop)
                replacement = replace(
                    replacement,
                    price=next_stop - offset if long_position else next_stop + offset,
                )
            async with self._command_lane(group.account_id):
                response = await self.broker.modify_order(
                    group.account_id,
                    str(order.orderId),
                    replacement,
                )
            managed.orders[request_index] = replacement
            actions.append(
                {
                    "action": profile.add_policy.value,
                    "order_id": str(order.orderId),
                    "from_stop": current_stop,
                    "to_stop": next_stop,
                    "response": response,
                }
            )
        return actions

    async def apply_profit_pocket_transition(self, group: _ManagedOrderGroup) -> list[dict[str, Any]]:
        profile = group.intent.resolved_protection_profile()
        if profile is None or profile.profit_pocket_transition in {
            ProfitPocketTransition.KEEP_EXISTING,
            ProfitPocketTransition.FULL_EXIT_AND_OPTIONAL_REENTRY,
        }:
            return []
        position = next(
            (
                row
                for row in await self.broker.positions(group.account_id)
                if str(row.contractDesc or row.raw.get("ticker") or "").upper()
                == group.intent.ticker.upper()
            ),
            None,
        )
        if position is None or abs(float(position.position)) <= 1e-9:
            return []
        long_position = float(position.position) > 0
        snapshot = self.execution_market_data.snapshot(group.intent.ticker)
        average_price = float(position.avgPrice or position.avgCost or group.intent.reference_price)
        transition = profile.profit_pocket_transition
        broker_trailing_amount = 0.0
        broker_trailing_type = "amt"
        if transition == ProfitPocketTransition.MOVE_TO_BREAKEVEN:
            desired_stop = average_price * (
                1.0 + (1 if long_position else -1)
                * float(group.intent.metadata.get("breakeven_buffer_bps") or 0)
                / 10_000
            )
        elif transition == ProfitPocketTransition.LOCK_PROFIT_PRICE:
            desired_stop = float(group.intent.metadata.get("profit_lock_price") or average_price)
        elif transition == ProfitPocketTransition.START_BROKER_TRAIL:
            broker_trailing_amount = float(
                group.intent.metadata.get("trailing_amount")
                or group.intent.metadata.get("trailing_percent")
                or 0
            )
            broker_trailing_type = (
                "%"
                if group.intent.metadata.get("trailing_percent") is not None
                else "amt"
            )
            if broker_trailing_amount <= 0:
                raise ValueError(
                    "start_broker_trail transition requires trailing_amount or trailing_percent"
                )
            desired_stop = average_price
        elif transition == ProfitPocketTransition.START_SWING_TRAIL:
            desired_stop = float(group.intent.metadata.get("latest_swing_stop_price") or 0)
            if desired_stop <= 0:
                raise ValueError(
                    "start_swing_trail transition requires latest_swing_stop_price"
                )
        elif snapshot is not None:
            if transition == ProfitPocketTransition.START_VOLATILITY_TRAIL:
                multiple = float(group.intent.metadata.get("trailing_volatility_multiple") or 1.0)
                desired_stop = (
                    snapshot.bid - snapshot.volatility * multiple
                    if long_position
                    else snapshot.ask + snapshot.volatility * multiple
                )
            else:
                resolved = [
                    item.stop.resolve(
                        reference_price=float(group.intent.reference_price),
                        side="long" if long_position else "short",
                        quantity=abs(float(position.position)) * item.quantity_fraction,
                        volatility=snapshot.volatility,
                    )
                    for item in profile.slices
                ]
                desired_stop = max(resolved) if long_position else min(resolved)
        else:
            return []
        if snapshot is not None:
            desired_stop = min(desired_stop, snapshot.bid - snapshot.tick_size) if long_position else max(
                desired_stop,
                snapshot.ask + snapshot.tick_size,
            )
        actions: list[dict[str, Any]] = []
        for order in await self.broker.live_orders():
            if (
                order.account != group.account_id
                or order.ticker.upper() != group.intent.ticker.upper()
                or order.order_status not in OPEN_ORDER_STATUSES
                or order.orderType.upper() not in {"STP", "STOP_LIMIT"}
            ):
                continue
            managed = self._group_for_order(order)
            if managed is None:
                continue
            request_index = managed.broker_order_request_indexes.get(str(order.orderId))
            if request_index is None:
                continue
            request = managed.orders[request_index]
            current_stop = float(request.auxPrice or 0)
            if transition == ProfitPocketTransition.REPLAN_REMAINING_SLICES:
                slice_id = managed.broker_order_slices.get(str(order.orderId), "")
                slice_rule = next(
                    (item.stop for item in profile.slices if item.slice_id == slice_id),
                    profile.slices[0].stop,
                )
                desired_for_order = slice_rule.resolve(
                    reference_price=float(group.intent.reference_price),
                    side="long" if long_position else "short",
                    quantity=abs(float(position.position)),
                    volatility=snapshot.volatility if snapshot is not None else 0.0,
                )
            else:
                desired_for_order = desired_stop
            next_stop = (
                max(current_stop, desired_for_order)
                if long_position
                else min(current_stop, desired_for_order)
            )
            if transition == ProfitPocketTransition.START_BROKER_TRAIL:
                replacement = replace(
                    request,
                    orderType="TRAIL",
                    auxPrice=None,
                    price=None,
                    trailingAmt=broker_trailing_amount,
                    trailingType=broker_trailing_type,
                )
                async with self._command_lane(group.account_id):
                    response = await self.broker.modify_order(
                        group.account_id,
                        str(order.orderId),
                        replacement,
                    )
                managed.orders[request_index] = replacement
                actions.append(
                    {
                        "order_id": str(order.orderId),
                        "from_stop": current_stop,
                        "to_trailing": broker_trailing_amount,
                        "trailing_type": broker_trailing_type,
                        "transition": transition.value,
                        "broker_response": response,
                    }
                )
                continue
            if math.isclose(next_stop, current_stop, abs_tol=1e-9):
                continue
            replacement = replace(request, auxPrice=next_stop)
            if replacement.orderType.upper() == "STOP_LIMIT" and replacement.price is not None:
                distance = abs(float(replacement.price) - current_stop)
                replacement = replace(
                    replacement,
                    price=next_stop - distance if long_position else next_stop + distance,
                )
            async with self._command_lane(group.account_id):
                response = await self.broker.modify_order(
                    group.account_id,
                    str(order.orderId),
                    replacement,
                )
            managed.orders[request_index] = replacement
            actions.append(
                {
                    "order_id": str(order.orderId),
                    "from_stop": current_stop,
                    "to_stop": next_stop,
                    "transition": transition.value,
                    "broker_response": response,
                }
            )
        self._record(
            "order_management",
            "profit_pocket_transition",
            group.group_id,
            group.account_id,
            self._causal_group_time(
                group.intent,
                previous=group.updated_at,
            ),
            {
                "order_group_id": group.group_id,
                "transition": transition.value,
                "actions": actions,
            },
        )
        return actions

    async def _ratchet_dynamic_protection(
        self,
        group: _ManagedOrderGroup,
        snapshot: ExecutionMarketSnapshot,
    ) -> None:
        profile = group.intent.resolved_protection_profile()
        if profile is None:
            return
        dynamic = [
            item
            for item in profile.slices
            if item.trailing.rule_type
            not in {
                TrailingRuleType.NONE,
                TrailingRuleType.BROKER_AMOUNT,
                TrailingRuleType.BROKER_PERCENT,
            }
        ]
        if not dynamic:
            return
        positions = await self.broker.positions(group.account_id)
        position = next(
            (
                row
                for row in positions
                if str(row.contractDesc or row.raw.get("ticker") or "").upper()
                == group.intent.ticker.upper()
            ),
            None,
        )
        if position is None or abs(float(position.position)) <= 1e-9:
            return
        long_position = float(position.position) > 0
        average = float(position.avgPrice or position.avgCost or group.intent.reference_price)
        candidates: list[float] = []
        for item in dynamic:
            trail = item.trailing
            gain = (
                (snapshot.bid / average - 1) * 100
                if long_position
                else (average / snapshot.ask - 1) * 100
            )
            if gain < trail.activation_gain_percent:
                continue
            if trail.rule_type == TrailingRuleType.VOLATILITY_TRAIL:
                distance = snapshot.volatility * float(trail.volatility_multiple or 1.0)
                candidates.append(
                    group.high_water_price - distance
                    if long_position
                    else group.low_water_price + distance
                )
            elif trail.rule_type == TrailingRuleType.CHANDELIER:
                distance = snapshot.volatility * float(trail.volatility_multiple or 3.0)
                candidates.append(
                    group.high_water_price - distance
                    if long_position
                    else group.low_water_price + distance
                )
            elif trail.rule_type == TrailingRuleType.BREAKEVEN_THEN_TRAIL:
                buffer = average * trail.breakeven_buffer_bps / 10_000
                candidates.append(average + buffer if long_position else average - buffer)
            elif trail.rule_type == TrailingRuleType.PROFIT_LOCK_R:
                initial_stop = item.stop.resolve(
                    reference_price=float(group.intent.reference_price),
                    side="long" if long_position else "short",
                    quantity=abs(float(position.position)) * item.quantity_fraction,
                    volatility=snapshot.volatility,
                )
                risk = abs(float(group.intent.reference_price) - initial_stop)
                achieved = (
                    group.high_water_price - average
                    if long_position
                    else average - group.low_water_price
                )
                if achieved > risk:
                    candidates.append(
                        average + risk * 0.5 if long_position else average - risk * 0.5
                    )
            elif trail.rule_type == TrailingRuleType.TIME_TIGHTENING:
                elapsed_minutes = max(
                    0.0,
                    (
                        snapshot.observed_at.astimezone(timezone.utc)
                        - group.intent.event_time.astimezone(timezone.utc)
                    ).total_seconds()
                    / 60.0,
                )
                bps_per_minute = float(
                    group.intent.metadata.get("time_tightening_bps_per_minute") or 1.0
                )
                maximum_bps = float(
                    group.intent.metadata.get("time_tightening_maximum_bps") or 100.0
                )
                distance_bps = min(maximum_bps, elapsed_minutes * bps_per_minute)
                candidates.append(
                    average * (1 - distance_bps / 10_000)
                    if long_position
                    else average * (1 + distance_bps / 10_000)
                )
            else:
                candidates.append(
                    item.stop.resolve(
                        reference_price=float(group.intent.reference_price),
                        side="long" if long_position else "short",
                        quantity=abs(float(position.position)) * item.quantity_fraction,
                        volatility=snapshot.volatility,
                    )
                )
        if not candidates:
            return
        desired = max(candidates) if long_position else min(candidates)
        desired = (
            min(desired, snapshot.bid - snapshot.tick_size)
            if long_position
            else max(desired, snapshot.ask + snapshot.tick_size)
        )
        for order in await self.broker.live_orders():
            if (
                order.account != group.account_id
                or order.ticker.upper() != group.intent.ticker.upper()
                or order.order_status not in OPEN_ORDER_STATUSES
                or order.orderType.upper() not in {"STP", "STOP_LIMIT"}
            ):
                continue
            managed = self._group_for_order(order)
            if managed is None or managed.group_id != group.group_id:
                continue
            index = managed.broker_order_request_indexes.get(str(order.orderId))
            if index is None:
                continue
            request = managed.orders[index]
            current = float(request.auxPrice or 0)
            next_stop = max(current, desired) if long_position else min(current, desired)
            if next_stop <= 0 or math.isclose(next_stop, current, abs_tol=snapshot.tick_size / 2):
                continue
            replacement = replace(request, auxPrice=_round_to_tick(next_stop, snapshot.tick_size, order.side))
            async with self._command_lane(group.account_id):
                response = await self.broker.modify_order(
                    group.account_id,
                    str(order.orderId),
                    replacement,
                )
            managed.orders[index] = replacement
            self._record(
                "order_management",
                "dynamic_stop_ratcheted",
                str(order.orderId),
                group.account_id,
                snapshot.observed_at,
                {
                    "order_group_id": managed.group_id,
                    "from_stop": current,
                    "to_stop": replacement.auxPrice,
                    "quote_source": snapshot.source,
                    "broker_response": response,
                },
            )

    def _causal_group_time(
        self,
        intent: StrategyIntent,
        *,
        previous: datetime | None = None,
    ) -> datetime:
        """Use market event time in historical modes and wall time only live.

        Simulated broker callbacks are emitted while processing a causal market
        event.  Stamping their OMS transitions with wall time places fills and
        order states days after the replay clock, hiding them from as-of UI
        queries.  The execution snapshot is the authoritative current clock for
        historical OMS work; the intent time is the lower-bound fallback.
        """

        if self.enforce_wall_clock_quote_freshness:
            candidate = datetime.now(timezone.utc)
        else:
            market = self.execution_market_data.snapshot(intent.ticker)
            candidate = market.observed_at if market is not None else intent.event_time
        candidate = candidate.astimezone(timezone.utc)
        if previous is not None:
            candidate = max(candidate, previous.astimezone(timezone.utc))
        return candidate

    def _transition(
        self,
        group: _ManagedOrderGroup,
        state: OrderManagementState,
        payload: dict[str, Any],
    ) -> None:
        group.state = state
        group.updated_at = self._causal_group_time(
            group.intent,
            previous=group.updated_at,
        )
        self._record(
            "order_management",
            "order_group_state",
            group.group_id,
            group.account_id,
            group.updated_at,
            {
                **payload,
                "state": state.value,
                "strategy_id": self.strategy_id,
                "strategy_revision": self.strategy_revision,
                "intent_id": group.intent.intent_id,
                "action": group.intent.action,
                "ticker": group.intent.ticker,
                "policy_version": self.policy.version,
            },
        )
        self.journal.save_order_management_state(
            group.group_id,
            run_id=self.run_id,
            account_id=group.account_id,
            state={
                "state": state.value,
                "strategy_id": self.strategy_id,
                "strategy_revision": self.strategy_revision,
                "created_at": group.created_at,
                "updated_at": group.updated_at,
                "intent": group.intent.payload(),
                "orders": [order.to_cpapi() for order in group.orders],
                "batch_lengths": [len(batch) for batch in group.plan.broker_batches],
                "order_slice_ids": list(group.plan.order_slice_ids),
                "cancel_strategy_protection": group.plan.cancel_strategy_protection,
                "protection_reconciliation_required": group.plan.protection_reconciliation_required,
                "broker_order_ids": list(group.broker_order_ids),
                "broker_order_roles": dict(group.broker_order_roles),
                "broker_order_slices": dict(group.broker_order_slices),
                "broker_order_request_indexes": dict(group.broker_order_request_indexes),
                "filled_by_broker_order": dict(group.filled_by_broker_order),
                "terminal_broker_order_ids": sorted(group.terminal_broker_order_ids),
                "filled_quantity": group.filled_quantity,
                "remaining_quantity": group.remaining_quantity,
                "current_limit_price": group.current_limit_price,
                "protection_required_quantity": group.protection_required_quantity,
                "protection_coverage_quantity": group.protection_coverage_quantity,
            },
        )
        if (
            self.state_callback is not None
            and state in {
                OrderManagementState.OUTCOME_UNKNOWN,
                OrderManagementState.REJECTED,
                OrderManagementState.POLICY_BLOCKED,
            }
        ):
            asyncio.create_task(self.state_callback(group.snapshot(self.policy.version)))

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
            lineage = causal_identity(
                correlation_seed=(
                    self.run_id
                    or group.intent.metadata.get("assignment_id")
                    or group.intent.intent_id
                ),
                causation_seed=(
                    group.intent.metadata.get("causation_id")
                    or group.intent.intent_id
                ),
            )
            enriched.setdefault(
                "correlation_id",
                normalize_request_identity(
                    str(group.intent.metadata.get("correlation_id") or "")
                )
                or lineage["correlation_id"],
            )
            enriched.setdefault(
                "causation_id",
                normalize_request_identity(
                    str(group.intent.metadata.get("causation_id") or "")
                )
                or lineage["causation_id"],
            )
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
    quote: ExecutionQuote | None = None,
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
    if quote is None:
        bid = float(intent.metadata.get("bid") or 0)
        ask = float(intent.metadata.get("ask") or 0)
        tick_size = float(intent.metadata.get("tick_size") or 0)
        observed_raw = intent.metadata.get("quote_observed_at") or intent.event_time
        observed_at = observed_raw if isinstance(observed_raw, datetime) else datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
        if bid <= 0 or ask <= 0 or tick_size <= 0:
            return None
        quote = ExecutionQuote(bid=bid, ask=ask, observed_at=observed_at, tick_size=tick_size)
    observed_at = quote.observed_at
    age_ms = max(0.0, (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds() * 1000.0)
    if enforce_wall_clock_freshness and age_ms > policy.maximum_quote_age_ms:
        raise ValueError(f"Execution quote is stale ({age_ms:.0f} ms)")
    urgency = _execution_policy_urgency(intent.resolved_execution_policy().name)
    side = _intent_side(intent)
    steps = tuple(
        PriceStep(step.after_ms, intent.resolved_execution_policy().envelope.bound(side, step.price))
        for step in _price_steps(side, urgency, quote, policy.maximum_reprice_ticks)
    )
    deduplicated: list[PriceStep] = []
    for step in steps:
        if not deduplicated or not math.isclose(deduplicated[-1].price, step.price):
            deduplicated.append(step)
    return ExecutionTactic(urgency, side, tuple(deduplicated), quote, max(step.after_ms for step in deduplicated))


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
    root_limits = [
        index
        for index, order in enumerate(orders)
        if order.orderType == "LMT" and not order.parentId
    ]
    if not root_limits:
        return orders
    result = list(orders)
    for index in root_limits:
        result[index] = replace(result[index], price=tactic.steps[0].price)
    return tuple(result)


def _group_batches(group: _ManagedOrderGroup) -> tuple[tuple[OrderRequest, ...], ...]:
    if not group.plan.batches:
        return (tuple(group.orders),)
    batches: list[tuple[OrderRequest, ...]] = []
    offset = 0
    for planned in group.plan.batches:
        batches.append(tuple(group.orders[offset : offset + len(planned)]))
        offset += len(planned)
    if offset != len(group.orders):
        raise RuntimeError("Order plan batch boundaries do not cover the managed orders")
    return tuple(batches)


def _extend_managed_plan(
    plan: StrategyOrderPlan,
    managed_orders: list[OrderRequest],
    *,
    slice_id: str,
) -> StrategyOrderPlan:
    if len(managed_orders) != len(plan.orders) + 1:
        raise ValueError("Managed order extension must append exactly one order")
    previous_lengths = (
        tuple(len(batch) for batch in plan.batches)
        if plan.batches
        else (len(plan.orders),)
    )
    offset = 0
    batches: list[tuple[OrderRequest, ...]] = []
    for length in previous_lengths:
        batches.append(tuple(managed_orders[offset : offset + length]))
        offset += length
    batches.append((managed_orders[-1],))
    existing_slice_ids = plan.order_slice_ids or tuple("" for _ in plan.orders)
    return replace(
        plan,
        orders=tuple(managed_orders),
        batches=tuple(batches),
        order_slice_ids=(*existing_slice_ids, slice_id),
    )


def _protection_group_key(order: LiveOrder) -> str:
    """Return the mutually exclusive capacity group for a protective order."""

    raw = dict(order.raw or {})
    oca_group = str(raw.get("oca_group") or raw.get("ocaGroup") or "")
    return oca_group or str(order.parentId or order.cOID or order.orderId)


def _warning_response(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("id") and (row.get("message") or row.get("messageIds")) for row in rows)


def _apply_cumulative_fill(
    group: _ManagedOrderGroup,
    broker_order_id: str,
    cumulative_quantity: float,
    role: str,
) -> float:
    previous = group.filled_by_broker_order.get(broker_order_id, 0.0)
    cumulative = max(previous, cumulative_quantity)
    group.filled_by_broker_order[broker_order_id] = cumulative
    incremental = max(0.0, cumulative - previous)
    if role == "entry":
        group.filled_quantity = sum(
            quantity
            for order_id, quantity in group.filled_by_broker_order.items()
            if group.broker_order_roles.get(order_id) == "entry"
        )
        group.remaining_quantity = max(0.0, float(group.intent.quantity) - group.filled_quantity)
    elif role == "managed_exit":
        group.filled_quantity = min(
            float(group.intent.quantity),
            sum(
                quantity
                for order_id, quantity in group.filled_by_broker_order.items()
                if group.broker_order_roles.get(order_id) == "managed_exit"
            ),
        )
        group.remaining_quantity = max(
            0.0, float(group.intent.quantity) - group.filled_quantity
        )
    return incremental


def _slice_for_live_order(group: _ManagedOrderGroup, order: LiveOrder) -> str:
    identity = str(order.cOID or order.parentId or "")
    return _slice_for_client_identity(group, identity)


def _slice_for_canonical_order(group: _ManagedOrderGroup, order: OrderState) -> str:
    identity = str(order.client_order_id or order.parent_order_id or "")
    return _slice_for_client_identity(group, identity)


def _slice_for_client_identity(group: _ManagedOrderGroup, identity: str) -> str:
    if not identity or not group.plan.order_slice_ids:
        return ""
    for index, request in enumerate(group.orders):
        if request.cOID == identity or request.parentId == identity:
            return group.plan.order_slice_ids[index]
    return ""


def _request_index_for_identity(group: _ManagedOrderGroup, identity: str) -> int | None:
    if not identity:
        return None
    return next(
        (
            index
            for index, request in enumerate(group.orders)
            if request.cOID == identity or request.parentId == identity
        ),
        None,
    )


def _open_entry_roots(group: _ManagedOrderGroup) -> tuple[tuple[str, int], ...]:
    rows: list[tuple[str, int]] = []
    for broker_order_id in group.broker_order_ids:
        if group.broker_order_roles.get(broker_order_id) != "entry":
            continue
        if broker_order_id in group.terminal_broker_order_ids:
            continue
        request_index = group.broker_order_request_indexes.get(broker_order_id)
        if request_index is None:
            continue
        requested = float(group.orders[request_index].quantity or 0)
        cumulative = group.filled_by_broker_order.get(broker_order_id, 0.0)
        if cumulative + 1e-9 < requested:
            rows.append((broker_order_id, request_index))
    return tuple(rows)


def _adaptive_interval_ms(policy_name: ExecutionPolicyName) -> int:
    if policy_name == ExecutionPolicyName.ADAPTIVE_VERY_URGENT:
        return 50
    if policy_name in {ExecutionPolicyName.ADAPTIVE_URGENT, ExecutionPolicyName.IMMEDIATE_WITH_LIMIT}:
        return 100
    if policy_name in {ExecutionPolicyName.ADAPTIVE_PATIENT, ExecutionPolicyName.PASSIVE}:
        return 500
    return 250


def _adaptive_price(
    *,
    side: str,
    policy_name: ExecutionPolicyName,
    quote: ExecutionQuote,
    reprice_index: int,
    maximum_reprice_ticks: int,
) -> float:
    buy = side.upper() == "BUY"
    touch = quote.ask if buy else quote.bid
    passive = quote.bid if buy else quote.ask
    direction = 1 if buy else -1
    if policy_name == ExecutionPolicyName.PASSIVE:
        raw = passive
    elif policy_name == ExecutionPolicyName.MIDPOINT:
        raw = quote.midpoint
    elif policy_name == ExecutionPolicyName.ADAPTIVE_PATIENT:
        raw = quote.midpoint if reprice_index > 1 else passive
    elif policy_name in {
        ExecutionPolicyName.ADAPTIVE_URGENT,
        ExecutionPolicyName.IMMEDIATE_WITH_LIMIT,
        ExecutionPolicyName.IBKR_NATIVE_ADAPTIVE,
    }:
        raw = touch
    elif policy_name == ExecutionPolicyName.ADAPTIVE_VERY_URGENT:
        raw = touch + direction * quote.tick_size * min(reprice_index, maximum_reprice_ticks)
    else:
        progress = min(1.0, reprice_index / max(1, maximum_reprice_ticks))
        raw = passive + (touch - passive) * progress
    return _round_to_tick(raw, quote.tick_size, side)


def _intent_from_payload(payload: dict[str, Any]) -> StrategyIntent:
    execution_raw = payload.get("execution_policy")
    protection_raw = payload.get("protection_profile")
    capital_raw = payload.get("capital_request")
    return StrategyIntent(
        intent_id=str(payload["intent_id"]),
        ticker=str(payload["ticker"]),
        event_time=_aware(payload["event_time"]),
        action=str(payload["action"]),  # type: ignore[arg-type]
        quantity=float(payload["quantity"]),
        reference_price=float(payload["reference_price"]),
        schema_version=int(payload.get("schema_version") or 1),
        capital_request=(CapitalRequest(**dict(capital_raw)) if capital_raw else None),
        invalidation_price=(
            float(payload["invalidation_price"])
            if payload.get("invalidation_price") is not None
            else None
        ),
        profit_target_price=(
            float(payload["profit_target_price"])
            if payload.get("profit_target_price") is not None
            else None
        ),
        trailing_amount=(
            float(payload["trailing_amount"])
            if payload.get("trailing_amount") is not None
            else None
        ),
        execution_policy=(
            execution_policy_from_payload(execution_raw) if execution_raw else None
        ),
        protection_profile=(
            protection_profile_from_payload(protection_raw) if protection_raw else None
        ),
        urgency=str(payload.get("urgency") or "regular"),  # type: ignore[arg-type]
        time_in_force=str(payload.get("time_in_force") or "DAY"),
        outside_rth=bool(payload.get("outside_rth")),
        reason=str(payload.get("reason") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("persisted OMS timestamps must include a timezone")
    return parsed


def _intent_side(intent: StrategyIntent) -> str:
    return "BUY" if str(intent.action) in {"enter_long", "add_long", "reduce_short", "cover"} else "SELL"


def _execution_policy_urgency(name: ExecutionPolicyName) -> ExecutionUrgency:
    if name in {ExecutionPolicyName.PASSIVE, ExecutionPolicyName.ADAPTIVE_PATIENT}:
        return ExecutionUrgency.PATIENT
    if name in {
        ExecutionPolicyName.ADAPTIVE_URGENT,
        ExecutionPolicyName.IMMEDIATE_WITH_LIMIT,
        ExecutionPolicyName.IBKR_NATIVE_ADAPTIVE,
    }:
        return ExecutionUrgency.URGENT
    if name == ExecutionPolicyName.ADAPTIVE_VERY_URGENT:
        return ExecutionUrgency.VERY_URGENT
    return ExecutionUrgency.REGULAR


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
