from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
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
    DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS,
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


def _is_terminal_modify_race(exc: ValueError) -> bool:
    """Identify the narrow broker race where a snapshotted order became terminal."""

    return str(exc).strip().lower() == "only open orders may be modified"


def _protective_repair_raw(parent_raw: dict[str, Any]) -> dict[str, Any]:
    """Retain entry lineage while assigning the repair fill its true role."""

    raw = dict(parent_raw)
    return {
        **raw,
        "canonical_metadata": {
            **dict(raw.get("canonical_metadata") or {}),
            "execution_role": "protective_stop",
            "reason": "protective_stop_filled",
        },
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
    maximum_reprice_ticks: int = DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS

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
            maximum_reprice_ticks=max(
                0,
                min(
                    6,
                    int(
                        os.environ.get(
                            "IBKR_MAXIMUM_REPRICE_TICKS",
                            str(DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS),
                        )
                    ),
                ),
            ),
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
    protection_delegated: bool = False
    entry_submission_closed: bool = False
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
    broker_order_state_fingerprints: dict[str, tuple[Any, ...]] = field(default_factory=dict)
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
    last_reprice_at: datetime | None = None
    current_limit_price: float | None = None
    deferred_reprice: tuple[float, float] | None = None
    failed_reprice_at: datetime | None = None
    internal_reaction_ms: float | None = None
    protection_task: asyncio.Task[None] | None = None
    high_water_price: float = 0.0
    low_water_price: float = 0.0
    protection_required_quantity: float = 0.0
    protection_coverage_quantity: float = 0.0
    protection_delegated: bool = False

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
            protection_delegated=self.protection_delegated,
            entry_submission_closed=bool(
                self.filled_quantity > 0 and not _open_entry_roots(self)
            ),
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
        causal_execution_clock: bool = False,
        control_plane: TradingControlPlane | None = None,
        reprice_authorizer: Callable[[StrategyIntent, str, float, float], Awaitable[bool]] | None = None,
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
        self.causal_execution_clock = causal_execution_clock
        self.control_plane = control_plane
        self.reprice_authorizer = reprice_authorizer
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

    async def expire_entry_deadlines(self, event_time: datetime) -> tuple[OrderGroupSnapshot, ...]:
        """Cancel entry quantity that survived its causal execution deadline.

        Historical engines can traverse many seconds of event time in less than
        one wall-clock millisecond.  ExecutionEnvelope.deadline_ms therefore
        cannot be enforced by the repricing task's monotonic timer in Replay or
        Backtest.  The runtime calls this before the broker sees each market
        event so an entry can never fill on an event after its deadline.
        """

        at = event_time.astimezone(timezone.utc)
        expired: list[_ManagedOrderGroup] = []
        for group in tuple(self._groups.values()):
            if str(group.intent.action) not in {
                "enter_long",
                "enter_short",
                "add_long",
                "add_short",
            }:
                continue
            if (
                group.state in TERMINAL_MANAGEMENT_STATES
                or group.state == OrderManagementState.CANCEL_PENDING
            ):
                continue
            deadline_ms = group.intent.resolved_execution_policy().envelope.deadline_ms
            if group.intent.resolved_execution_policy().envelope.persist_until_cancelled:
                continue
            if deadline_ms <= 0:
                continue
            deadline = group.intent.event_time.astimezone(timezone.utc) + timedelta(
                milliseconds=deadline_ms
            )
            if at < deadline or not _open_entry_roots(group):
                continue
            # Anchor all cancellation records and the terminal transition to
            # the causal event that crossed the deadline.  Runtime normally
            # has an equally fresh quote snapshot, but the deadline contract
            # must remain correct even when invoked without one.
            group.updated_at = max(group.updated_at.astimezone(timezone.utc), at)
            if await self._cancel_open_entry_roots(group, "execution_deadline"):
                expired.append(group)
        if expired:
            await self.reconcile()
        return tuple(group.snapshot(self.policy.version) for group in expired)

    async def advance_adaptive_execution(
        self,
        event_time: datetime,
    ) -> tuple[OrderGroupSnapshot, ...]:
        """Advance every adaptive root order on the causal historical clock.

        Replay and Backtest can traverse seconds of market time before an
        asyncio wall-clock timer is scheduled. Reprice a due entry or managed
        exit from the latest already-observed quote before the broker matches
        the current event. At most one modification is attempted per market
        event, so a sparse stream never applies a later quote retroactively.

        Managed exits are intentionally included. Leaving them on the
        wall-clock repricer made a fast historical run keep a partially filled
        sell at its original bid for minutes of event time, blocking the next
        strategy campaign even though fresh executable quotes were available.
        """

        if not self.causal_execution_clock:
            return ()
        at = event_time.astimezone(timezone.utc)
        advanced: list[_ManagedOrderGroup] = []
        for group in tuple(self._groups.values()):
            if group.tactic is None:
                continue
            if (
                group.state in TERMINAL_MANAGEMENT_STATES
                or group.state == OrderManagementState.CANCEL_PENDING
                or not _open_adaptive_roots(group)
            ):
                continue
            execution_policy = group.intent.resolved_execution_policy()
            envelope = execution_policy.envelope
            persistent = envelope.persist_until_cancelled
            if not persistent and group.reprice_count >= envelope.maximum_reprices:
                continue
            elapsed_ms = (
                at - group.intent.event_time.astimezone(timezone.utc)
            ).total_seconds() * 1_000
            reprice_interval_ms = (
                envelope.minimum_reprice_interval_ms
                if envelope.minimum_reprice_interval_ms > 0
                else _adaptive_interval_ms(execution_policy.name)
            )
            last_reprice_at = group.last_reprice_at or group.intent.event_time
            if (
                at - last_reprice_at.astimezone(timezone.utc)
            ).total_seconds() * 1_000 < reprice_interval_ms:
                continue
            if (
                not persistent
                and envelope.deadline_ms > 0
                and elapsed_ms >= envelope.deadline_ms
            ):
                continue
            if await self._attempt_reprice(group, record_time=at):
                advanced.append(group)
        if advanced:
            await self.reconcile()
        return tuple(group.snapshot(self.policy.version) for group in advanced)

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
                protection_delegated=bool(payload.get("protection_delegated")),
            )
            self._groups[group_id] = group
            for request in group.orders:
                if request.cOID:
                    self._group_by_client_id[request.cOID] = group_id
            for broker_order_id in group.broker_order_ids:
                self._group_by_broker_id[broker_order_id] = group_id
        await self._restore_managed_exit_delegation()
        return await self.reconcile()

    async def _restore_managed_exit_delegation(self) -> None:
        """Restore full-exit ownership before replaying broker order updates.

        A process can stop after a full-position managed exit is accepted but
        before the source entry's delegated flag is durably recorded. Replaying
        the exit OCA first must not cause that entry to create an overlapping
        repair stop. Broker-held position and open-order state are authoritative
        for this recovery-only relationship repair.
        """

        live_by_id = {
            str(order.orderId): order
            for order in await self.broker.live_orders()
            if order.order_status in OPEN_ORDER_STATUSES
        }
        positions_by_account_ticker = {
            (account_id, str(position.contractDesc or position.raw.get("ticker") or "").upper()): abs(
                float(position.position)
            )
            for account_id in {group.account_id for group in self._groups.values()}
            for position in await self.broker.positions(account_id)
        }
        tolerance = 1e-6
        for managed_exit in self._groups.values():
            if str(managed_exit.intent.action) not in {
                "exit",
                "take_profit",
                "reduce_long",
                "reduce_short",
                "cover",
            }:
                continue
            ticker = managed_exit.intent.ticker.upper()
            held = positions_by_account_ticker.get((managed_exit.account_id, ticker), 0.0)
            if held <= tolerance or float(managed_exit.intent.quantity) + tolerance < held:
                continue
            if not any(
                broker_order_id in live_by_id
                for broker_order_id in managed_exit.broker_order_ids
            ):
                continue
            for protected in self._groups.values():
                if protected.group_id == managed_exit.group_id:
                    continue
                if protected.account_id != managed_exit.account_id:
                    continue
                if protected.intent.ticker.upper() != ticker:
                    continue
                if str(protected.intent.action) not in {
                    "enter_long",
                    "enter_short",
                    "add_long",
                    "add_short",
                }:
                    continue
                protected.protection_delegated = True
                self._transition(
                    protected,
                    protected.state,
                    {
                        "event": "protection_delegation_recovered",
                        "managed_exit_group_id": managed_exit.group_id,
                    },
                )

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
        await self._cancel_pending_acquisition_before_exit(
            intent,
            account_id=account_id,
        )
        if str(intent.action) == "replace_protective_stop":
            return await self._replace_protective_stop(intent, account_id=account_id)
        working_intent = intent
        plan = self.planner(working_intent, account_id, event)
        if intent.metadata.get("incremental_exit_reconciliation"):
            plan = replace(plan, cancel_strategy_protection=False)
        if not plan.orders:
            raise ValueError(f"Strategy intent produced no broker order plan: {working_intent.action}")
        if str(working_intent.action) == "replace_profit_target":
            return await self._replace_existing_profit_targets(
                working_intent,
                account_id=account_id,
                plan=plan,
            )
        await self._require_shortability(working_intent, plan)
        quote = self._execution_quote(working_intent)
        tactic = execution_tactic(
            working_intent,
            self.policy,
            quote=quote,
            enforce_wall_clock_freshness=self.enforce_wall_clock_quote_freshness,
        )
        protected_exit: OrderGroupSnapshot | None = None
        for stale_attempt in range(2):
            try:
                protected_exit = await self._modify_existing_protected_exit(
                    working_intent,
                    account_id=account_id,
                    plan=plan,
                    tactic=tactic,
                )
                break
            except ValueError as exc:
                if not _is_terminal_modify_race(exc):
                    raise
                reconciled = await self._reconcile_stale_protected_exit(
                    working_intent,
                    account_id=account_id,
                    attempt=stale_attempt + 1,
                )
                if reconciled is None:
                    return self._satisfied_exit_snapshot(
                        working_intent,
                        account_id=account_id,
                        reason="protected_target_filled_before_exit_reprice",
                    )
                working_intent = reconciled
                plan = self.planner(working_intent, account_id, event)
                quote = self._execution_quote(working_intent)
                tactic = execution_tactic(
                    working_intent,
                    self.policy,
                    quote=quote,
                    enforce_wall_clock_freshness=self.enforce_wall_clock_quote_freshness,
                )
        else:
            # The broker changed the same protected order twice while it was
            # being repriced. Stop reusing that stale order identity. Cancel
            # only the strategy's remaining protection, then submit a fresh
            # full-exit OCA group for the causally refreshed position quantity.
            reconciled = await self._reconcile_stale_protected_exit(
                working_intent,
                account_id=account_id,
                attempt=3,
            )
            if reconciled is None:
                return self._satisfied_exit_snapshot(
                    working_intent,
                    account_id=account_id,
                    reason="protected_target_filled_during_exit_reconciliation",
                )
            working_intent = reconciled
            plan = self.planner(working_intent, account_id, event)
            quote = self._execution_quote(working_intent)
            tactic = execution_tactic(
                working_intent,
                self.policy,
                quote=quote,
                enforce_wall_clock_freshness=self.enforce_wall_clock_quote_freshness,
            )
            await self.cancel_strategy_protection(
                account_id=account_id,
                ticker=working_intent.ticker,
                client_id_prefix=self._protective_order_prefix(),
                event_time=working_intent.event_time,
            )
        if protected_exit is not None:
            return await self._match_current_historical_group(
                protected_exit,
                intent=working_intent,
                quote=quote,
            )
        orders = _apply_initial_tactic(plan.orders, tactic)
        now = self._causal_group_time(working_intent)
        group = _ManagedOrderGroup(
            group_id=str(uuid4()),
            intent=working_intent,
            account_id=account_id,
            plan=plan,
            state=OrderManagementState.CREATED,
            created_at=now,
            updated_at=now,
            orders=list(orders),
            tactic=tactic,
            remaining_quantity=float(working_intent.quantity),
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
            intent=working_intent,
            require_fresh=self.enforce_wall_clock_quote_freshness,
        )
        delegated_sources: list[_ManagedOrderGroup] = []
        if plan.cancel_strategy_protection:
            # A semantic full exit replaces every older protective sell path.
            # Delegate the source groups first so synchronous cancellation
            # callbacks cannot manufacture repair stops, then cancel the old
            # children before submitting the new exit.  Keeping both sets
            # live at once over-reserves the held position and is rejected by
            # both the simulator and real broker short-sale protection.
            for protected in self._groups.values():
                if protected.group_id == group.group_id:
                    continue
                if protected.account_id != account_id:
                    continue
                if protected.intent.ticker.upper() != working_intent.ticker.upper():
                    continue
                if str(protected.intent.action) not in {
                    "enter_long",
                    "enter_short",
                    "add_long",
                    "add_short",
                }:
                    continue
                protected.protection_delegated = True
                delegated_sources.append(protected)
                self._transition(
                    protected,
                    protected.state,
                    {
                        "event": "protection_delegated_to_fresh_managed_exit",
                        "managed_exit_group_id": group.group_id,
                    },
                )
            await self.cancel_strategy_protection(
                account_id=account_id,
                ticker=working_intent.ticker,
                client_id_prefix=self._protective_order_prefix(),
                event_time=working_intent.event_time,
            )
        try:
            self.risk.reserve(account_id, group.orders)
            self._transition(group, OrderManagementState.RISK_RESERVED, {"event": "risk_reserved"})
            await self._submit(group)
        except Exception:
            # If the replacement never became live, restore broker-held
            # protection before surfacing the failure. If a fresh exit member
            # is already working, restoring old children would over-sell.
            live_order_ids = {
                str(order.orderId)
                for order in await self.broker.live_orders()
                if order.order_status in OPEN_ORDER_STATUSES
            }
            fresh_exit_is_live = any(
                str(order_id) in live_order_ids for order_id in group.broker_order_ids
            )
            if not fresh_exit_is_live:
                for protected in delegated_sources:
                    protected.protection_delegated = False
                    await self.reconcile_protection(protected)
            self.risk.release(account_id, group.orders)
            raise
        return await self._match_current_historical_group(
            group.snapshot(self.policy.version),
            intent=working_intent,
            quote=quote,
        )

    async def _match_current_historical_group(
        self,
        snapshot: OrderGroupSnapshot,
        *,
        intent: StrategyIntent,
        quote: ExecutionQuote | None,
    ) -> OrderGroupSnapshot:
        """Match a new or modified marketable group at its causal decision quote."""

        match_current = getattr(self.broker, "match_current_orders", None)
        if match_current is None:
            return snapshot
        executions = await match_current(
            intent.ticker,
            intent.event_time,
            tuple(snapshot.broker_order_ids),
            quote.bid if quote is not None else None,
            quote.ask if quote is not None else None,
            quote.observed_at if quote is not None else None,
        )
        for execution in executions:
            self.journal.append(
                run_id=self.run_id,
                category="execution",
                entity_type="fill",
                entity_id=execution.execution_id,
                account_id=execution.account,
                event_time=execution.trade_time,
                payload=execution.to_cpapi(),
            )
        if not executions:
            return snapshot
        await self.reconcile()
        return self.snapshot_for_intent(intent.intent_id) or snapshot

    async def _reconcile_stale_protected_exit(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        attempt: int,
    ) -> StrategyIntent | None:
        positions = await self.broker.positions(account_id)
        ticker = intent.ticker.upper()
        position_side = str(intent.metadata.get("position_side") or "long").lower()
        signed_quantity = sum(
            float(position.position)
            for position in positions
            if str(position.contractDesc or "").upper() == ticker
        )
        remaining = (
            max(0.0, signed_quantity)
            if position_side != "short"
            else max(0.0, -signed_quantity)
        )
        executable_remaining = math.floor(remaining * 1_000_000) / 1_000_000
        self._record(
            "order_management",
            "protected_exit_snapshot_reconciled",
            intent.intent_id,
            account_id,
            intent.event_time,
            {
                "ticker": ticker,
                "attempt": attempt,
                "requested_quantity": float(intent.quantity),
                "broker_position_quantity": signed_quantity,
                "executable_remaining_quantity": executable_remaining,
                "reason": "protected_order_became_terminal_before_modify",
            },
        )
        if executable_remaining <= 1e-9:
            return None
        if executable_remaining > float(intent.quantity) + 1e-9:
            raise ValueError(
                "Protected exit reconciliation found a larger position than the "
                "Portfolio-approved exit quantity; refusing to expand execution authority"
            )
        return replace(
            intent,
            quantity=executable_remaining,
            metadata={
                **dict(intent.metadata),
                "position_quantity": executable_remaining,
                "exit_quantity_reconciled_from": float(intent.quantity),
                "exit_quantity_reconciliation_attempt": attempt,
            },
        )

    def _satisfied_exit_snapshot(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        reason: str,
    ) -> OrderGroupSnapshot:
        now = self._causal_group_time(intent)
        group = _ManagedOrderGroup(
            group_id=str(uuid4()),
            intent=intent,
            account_id=account_id,
            plan=StrategyOrderPlan(()),
            state=OrderManagementState.FILLED,
            created_at=now,
            updated_at=now,
            orders=[],
            remaining_quantity=0.0,
        )
        self._groups[group.group_id] = group
        self._record(
            "order_management",
            "protected_exit_already_satisfied",
            group.group_id,
            account_id,
            intent.event_time,
            {
                "order_group_id": group.group_id,
                "ticker": intent.ticker.upper(),
                "reason": reason,
                "requested_quantity": float(intent.quantity),
            },
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
        order_id = str(order.orderId)
        fill_role = group.broker_order_roles.get(str(order.orderId), "")
        fingerprint = _live_order_state_fingerprint(order)
        if group.broker_order_state_fingerprints.get(order_id) == fingerprint:
            return group.snapshot(
                self.policy.version,
                fill_role=fill_role,
                broker_order_id=order_id,
                slice_id=group.broker_order_slices.get(order_id, ""),
            )
        group.broker_order_state_fingerprints[order_id] = fingerprint
        incremental = _apply_cumulative_fill(
            group,
            order_id,
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
        if (
            not self.causal_execution_clock
            and group.tactic
            and group.broker_order_ids
            and group.intent.resolved_execution_policy().envelope.maximum_reprices > 0
        ):
            # Adaptive execution is an intent-level contract, not an entry-only
            # facility. In particular, an extended-hours exit must remain a
            # limit order at the latest bid while its remainder is working.
            group.reprice_task = asyncio.create_task(self._run_repricing(group))

    async def _replace_existing_profit_targets(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        plan: StrategyOrderPlan,
    ) -> OrderGroupSnapshot:
        """Reprice every live target child without disturbing its OCA protection."""

        target_price = float(plan.orders[0].price or 0)
        if target_price <= 0:
            raise ValueError("Profit-target replacement requires a positive limit price")
        live_by_id = {
            str(order.orderId): order
            for order in await self.broker.live_orders()
            if order.order_status in OPEN_ORDER_STATUSES
        }
        candidates: list[tuple[_ManagedOrderGroup, str, int, OrderRequest, LiveOrder]] = []
        for group in self._groups.values():
            if group.account_id != account_id:
                continue
            if group.intent.ticker.upper() != intent.ticker.upper():
                continue
            if (intent.metadata.get("assignment_id")
                    and group.intent.metadata.get("assignment_id") != intent.metadata["assignment_id"]):
                continue
            if str(group.intent.action) not in {
                "enter_long", "enter_short", "add_long", "add_short",
            }:
                continue
            for broker_order_id in group.broker_order_ids:
                if group.broker_order_roles.get(broker_order_id) != "profit_target":
                    continue
                live = live_by_id.get(str(broker_order_id))
                request_index = group.broker_order_request_indexes.get(
                    str(broker_order_id)
                )
                if live is None or request_index is None:
                    continue
                candidates.append(
                    (group, str(broker_order_id), request_index, group.orders[request_index], live)
                )
        capacity = sum(float(live.remainingQuantity) for *_, live in candidates)
        if not candidates or capacity + 1e-9 < float(intent.quantity):
            raise ValueError(
                "Cannot replace profit target: live target protection does not cover "
                "the current strategy position"
            )
        responses: list[dict[str, Any]] = []
        touched: dict[str, _ManagedOrderGroup] = {}
        async with self._command_lane(account_id):
            for group, broker_order_id, request_index, request, _ in candidates:
                replacement = replace(
                    request,
                    price=target_price,
                    raw={
                        **dict(request.raw or {}),
                        "canonical_metadata": {
                            **dict(request.raw.get("canonical_metadata") or {}),
                            "reason": str(
                                intent.metadata.get("reason_code")
                                or "structural_profit_target_advanced"
                            ),
                            "replacement_intent_id": intent.intent_id,
                            "target_price": target_price,
                        },
                    },
                )
                response = await self.broker.modify_order(
                    account_id,
                    broker_order_id,
                    replacement,
                )
                if _warning_response(response):
                    async with self._warning_lane:
                        response = await self._resolve_warning_chain_locked(group, response)
                _require_modify_acknowledgement(response)
                responses.extend(response)
                group.orders[request_index] = replacement
                profile = group.intent.resolved_protection_profile()
                if profile is not None:
                    profile = replace(profile, slices=tuple(
                        replace(item, profit_target_price=target_price)
                        if item.profit_target_price is not None else item for item in profile.slices
                    ))
                group.intent = replace(group.intent, profit_target_price=target_price, protection_profile=profile)
                touched[group.group_id] = group
        if not touched:
            raise ValueError("Profit-target replacement changed no live order")
        for group in touched.values():
            self._transition(
                group,
                group.state,
                {
                    "event": "profit_target_replaced",
                    "replacement_intent_id": intent.intent_id,
                    "target_price": target_price,
                },
            )
            if self.state_callback is not None:
                await self.state_callback(group.snapshot(self.policy.version))
        self._record(
            "broker",
            "profit_target_replaced",
            intent.intent_id,
            account_id,
            intent.event_time,
            {
                "ticker": intent.ticker,
                "quantity": intent.quantity,
                "target_price": target_price,
                "source_group_ids": sorted(touched),
                "broker_response": responses,
            },
        )
        return next(iter(touched.values())).snapshot(self.policy.version)

    async def _modify_existing_protected_exit(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        plan: StrategyOrderPlan,
        tactic: ExecutionTactic | None,
    ) -> OrderGroupSnapshot | None:
        action = str(intent.action)
        if action not in {"exit", "take_profit", "reduce_long", "cover", "reduce_short"}:
            return None
        if action == "exit":
            # A semantic full exit must retain its own immutable command and
            # execution identity.  Repricing an entry's profit-target child
            # made the resulting sell executions inherit the entry action,
            # reason, and client-order identity (or no identity at all), and
            # allowed the entry group to appear to fill again during exit.
            # The normal fresh-exit path below delegates and cancels superseded
            # children, then submits a dedicated marketable exit OCA.
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
        desired_side = _intent_side(intent)
        live_by_id = {
            str(order.orderId): order
            for order in await self.broker.live_orders()
            if order.order_status in OPEN_ORDER_STATUSES
        }
        sliced_targets: list[
            tuple[_ManagedOrderGroup, str, int, OrderRequest, LiveOrder]
        ] = []
        for protected in candidates:
            for broker_order_id in protected.broker_order_ids:
                if protected.broker_order_roles.get(broker_order_id) != "profit_target":
                    continue
                live = live_by_id.get(str(broker_order_id))
                request_index = protected.broker_order_request_indexes.get(
                    str(broker_order_id)
                )
                if live is None or request_index is None:
                    continue
                request = protected.orders[request_index]
                if (
                    request.orderType.upper() == "LMT"
                    and request.side.upper() == desired_side
                ):
                    sliced_targets.append(
                        (
                            protected,
                            str(broker_order_id),
                            request_index,
                            request,
                            live,
                        )
                    )
        sliced_capacity = sum(
            float(live.remainingQuantity)
            for _, _, _, _, live in sliced_targets
        )
        if len(sliced_targets) > 1 and math.isclose(
            sliced_capacity,
            float(intent.quantity),
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            initial_price = (
                tactic.steps[0].price
                if tactic
                else float(plan.orders[0].price or intent.reference_price)
            )
            replacements: list[OrderRequest] = []
            broker_order_ids: list[str] = []
            source_groups: dict[str, _ManagedOrderGroup] = {}
            responses: list[dict[str, Any]] = []
            started = perf_counter()
            async with self._command_lane(account_id):
                for protected, broker_order_id, request_index, request, _ in sliced_targets:
                    canonical_metadata = {
                        **dict(request.raw.get("canonical_metadata") or {}),
                        "execution_role": "managed_exit",
                        "reason": str(intent.metadata.get("reason_code") or "strategy_exit"),
                    }
                    replacement = replace(
                        request,
                        price=initial_price,
                        raw={
                            **dict(request.raw or {}),
                            "canonical_metadata": canonical_metadata,
                        },
                    )
                    response = await self.broker.modify_order(
                        account_id,
                        broker_order_id,
                        replacement,
                    )
                    responses.extend(response)
                    protected.orders[request_index] = replacement
                    replacements.append(replacement)
                    broker_order_ids.append(broker_order_id)
                    source_groups[protected.group_id] = protected
            now = self._causal_group_time(intent)
            group = _ManagedOrderGroup(
                group_id=str(uuid4()),
                intent=intent,
                account_id=account_id,
                plan=StrategyOrderPlan(tuple(replacements)),
                state=OrderManagementState.SUBMITTING,
                created_at=now,
                updated_at=now,
                orders=replacements,
                tactic=tactic,
                broker_order_ids=broker_order_ids,
                broker_order_roles={
                    broker_order_id: "managed_exit"
                    for broker_order_id in broker_order_ids
                },
                broker_order_request_indexes={
                    broker_order_id: index
                    for index, broker_order_id in enumerate(broker_order_ids)
                },
                # These target children may already have cumulative fills.
                # Seed the adopted managed-exit group at that broker baseline
                # so subsequent order updates count only new exit shares.
                filled_by_broker_order={
                    broker_order_id: float(live.filledQuantity)
                    for _, broker_order_id, _, _, live in sliced_targets
                },
                remaining_quantity=float(intent.quantity),
                current_limit_price=initial_price,
            )
            group.decision_to_submit_ms = (perf_counter() - started) * 1000.0
            group.submitted_at = datetime.now(timezone.utc)
            self._groups[group.group_id] = group
            for broker_order_id in broker_order_ids:
                self._group_by_broker_id[broker_order_id] = group.group_id
            for protected in source_groups.values():
                protected.protection_delegated = True
                self._transition(
                    protected,
                    protected.state,
                    {
                        "event": "protection_delegated_to_sliced_managed_exit",
                        "managed_exit_group_id": group.group_id,
                    },
                )
            self._record(
                "broker",
                "protected_sliced_exit_modified",
                group.group_id,
                account_id,
                intent.event_time,
                {
                    "order_group_id": group.group_id,
                    "price": initial_price,
                    "quantity": intent.quantity,
                    "broker_order_ids": broker_order_ids,
                    "preserved_slice_protection": True,
                    "broker_response": responses,
                    "decision_to_submit_ms": group.decision_to_submit_ms,
                },
            )
            self._transition(
                group,
                OrderManagementState.ACKNOWLEDGED,
                {"event": "protected_sliced_exit_acknowledged"},
            )
            return group.snapshot(self.policy.version)
        for protected in reversed(candidates):
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
            broker_order_id = protected.broker_order_ids[target_index]
            live = live_by_id.get(str(broker_order_id))
            if live is None or not math.isclose(
                float(live.remainingQuantity),
                float(intent.quantity),
                rel_tol=0.0,
                abs_tol=1e-6,
            ):
                # Reuse is safe only when the open child owns exactly the
                # remaining position. Otherwise the caller must use the fresh
                # managed-exit path and reconcile the old protection.
                continue
            initial_price = tactic.steps[0].price if tactic else float(plan.orders[0].price or intent.reference_price)
            canonical_metadata = {
                **dict(existing.raw.get("canonical_metadata") or {}),
                "execution_role": "managed_exit",
                "reason": str(intent.metadata.get("reason_code") or "strategy_exit"),
            }
            replacement = replace(
                existing,
                # Broker modifications use total cumulative order quantity,
                # not remaining quantity. Preserve shares already filled and
                # add the current position that this managed exit must close.
                quantity=float(live.filledQuantity) + float(intent.quantity),
                price=initial_price,
                raw={
                    **dict(existing.raw or {}),
                    "canonical_metadata": canonical_metadata,
                },
            )
            now = self._causal_group_time(intent)
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
                    broker_order_id: "managed_exit",
                },
                broker_order_request_indexes={broker_order_id: 0},
                filled_by_broker_order={
                    broker_order_id: float(live.filledQuantity),
                },
                remaining_quantity=float(intent.quantity),
            )
            self._groups[group.group_id] = group
            self._group_by_broker_id[broker_order_id] = group.group_id
            # The replacement can fill synchronously (the simulator does this,
            # and a live broker may publish the fill before modify_order
            # returns). Delegate reconciliation before sending the command so
            # cancellation of the sibling stop cannot make the source entry
            # manufacture a second full-position repair backstop while this
            # managed exit already owns the quantity.
            protected.protection_delegated = True
            self._transition(
                protected,
                protected.state,
                {
                    "event": "protection_delegated_to_managed_exit",
                    "managed_exit_group_id": group.group_id,
                },
            )
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
            try:
                async with self._command_lane(account_id):
                    response = await self.broker.modify_order(account_id, broker_order_id, replacement)
                    response = await self._resolve_warning_chain_locked(group, response)
            except Exception:
                protected.protection_delegated = False
                raise
            group.decision_to_submit_ms = (perf_counter() - started) * 1000.0
            group.submitted_at = datetime.now(timezone.utc)
            if group.state == OrderManagementState.POLICY_BLOCKED:
                protected.protection_delegated = False
                return group.snapshot(self.policy.version)
            rejected = next((row for row in response if row.get("error") or row.get("errorCode")), None)
            if rejected:
                protected.protection_delegated = False
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
            if (
                tactic
                and intent.resolved_execution_policy().envelope.maximum_reprices > 0
            ):
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
        interval_ms = (
            envelope.minimum_reprice_interval_ms
            if envelope.minimum_reprice_interval_ms > 0
            else _adaptive_interval_ms(execution_policy.name)
        )
        persistent = envelope.persist_until_cancelled
        while persistent or group.reprice_count < envelope.maximum_reprices:
            if (
                group.state in TERMINAL_MANAGEMENT_STATES
                or group.state == OrderManagementState.CANCEL_PENDING
                or group.filled_quantity >= float(group.intent.quantity)
                or not _open_adaptive_roots(group)
            ):
                break
            elapsed_ms = (perf_counter() - started) * 1_000
            if (
                not persistent
                and envelope.deadline_ms > 0
                and elapsed_ms >= envelope.deadline_ms
            ):
                break
            timeout_ms = (
                interval_ms
                if persistent or envelope.deadline_ms <= 0
                else min(interval_ms, max(0.0, envelope.deadline_ms - elapsed_ms))
            )
            try:
                await asyncio.wait_for(
                    group.reprice_event.wait(), timeout=timeout_ms / 1_000
                )
            except TimeoutError:
                pass
            group.reprice_event.clear()
            await self._attempt_reprice(
                group,
                record_time=datetime.now(timezone.utc),
            )
        if group.remaining_quantity > 0 and (
            execution_policy.name == ExecutionPolicyName.CANCEL_IF_NOT_FILLED
            or (
                self.enforce_wall_clock_quote_freshness
                and str(group.intent.action)
                in {"enter_long", "enter_short", "add_long", "add_short"}
            )
        ):
            await self._cancel_open_entry_roots(group, "execution_deadline")

    async def _attempt_reprice(
        self,
        group: _ManagedOrderGroup,
        *,
        record_time: datetime,
    ) -> bool:
        """Apply one adaptive modification from the latest allowed quote."""

        assert group.tactic is not None
        reaction_started = perf_counter()
        execution_policy = group.intent.resolved_execution_policy()
        envelope = execution_policy.envelope
        if group.filled_quantity >= float(group.intent.quantity):
            return False
        if group.state in {
            OrderManagementState.CANCEL_PENDING,
            OrderManagementState.CANCELLED,
            OrderManagementState.REJECTED,
            OrderManagementState.POLICY_BLOCKED,
            OrderManagementState.OUTCOME_UNKNOWN,
        }:
            return False
        if group.filled_quantity > 0:
            if execution_policy.partial_fill_policy == PartialFillPolicy.CANCEL_REMAINDER:
                await self._cancel_open_entry_roots(group, "partial_fill_policy")
                return False
            if execution_policy.partial_fill_policy == PartialFillPolicy.ACCEPT_PARTIAL:
                await self._cancel_open_entry_roots(group, "accept_partial")
                return False
        quote = self._execution_quote(group.intent) or group.tactic.quote
        if quote.observed_at.astimezone(timezone.utc) > record_time.astimezone(timezone.utc):
            raise RuntimeError("Adaptive repricing cannot consume a future execution quote")
        if (group.intent.metadata.get("entry_completion_quote") in {"bid", "ask"}
                and (record_time - quote.observed_at).total_seconds() * 1000 > self.policy.maximum_quote_age_ms):
            return False
        age_ms = (
            datetime.now(timezone.utc) - quote.observed_at.astimezone(timezone.utc)
        ).total_seconds() * 1_000
        if self.enforce_wall_clock_quote_freshness and age_ms > self.policy.maximum_quote_age_ms:
            self._record(
                "order_management",
                "adaptive_reprice_skipped",
                group.group_id,
                group.account_id,
                self._causal_group_time(group.intent, previous=group.updated_at),
                {"reason": "quote_stale", "quote_age_ms": age_ms},
            )
            return False
        requested_price = envelope.bound(
            group.tactic.side,
            _adaptive_price(
                side=group.tactic.side,
                policy_name=execution_policy.name,
                quote=quote,
                reprice_index=group.reprice_count + 1,
                maximum_reprice_ticks=self.policy.maximum_reprice_ticks,
            ),
        )
        if group.intent.metadata.get("entry_completion_quote") == "bid" and group.tactic.side.upper() == "BUY":
            requested_price = envelope.bound("BUY", _round_to_tick(quote.bid, quote.tick_size, "BUY"))
        elif group.intent.metadata.get("entry_completion_quote") == "ask":
            side = group.tactic.side.upper()
            requested_price = envelope.bound(side, _round_to_tick(
                quote.ask if side == "BUY" else quote.bid, quote.tick_size, side,
            ))
        stop = float(group.intent.invalidation_price or 0)
        if stop > 0 and (
            (group.intent.action in {"enter_long", "add_long"} and requested_price <= stop)
            or (group.intent.action in {"enter_short", "add_short"} and requested_price >= stop)
        ):
            # A tighter point support can be crossed during partial acquisition.
            # Cancel the remainder; never move the structural stop to fund it.
            await self._cancel_open_entry_roots(group, "entry_stop_crossed")
            return False
        if group.current_limit_price is not None and math.isclose(
            requested_price,
            group.current_limit_price,
            abs_tol=quote.tick_size / 2,
        ):
            return False
        modified = False
        # Failed/deferred attempts are attempts too; do not hammer the broker
        # on every historical event or every wake-up at an unchanged quote.
        group.last_reprice_at = record_time
        for root_order_id, request_index in _open_adaptive_roots(group):
            replacement = replace(group.orders[request_index], price=requested_price)
            remaining = max(0.0, float(replacement.quantity or 0) - group.filled_by_broker_order.get(root_order_id, 0.0))
            fingerprint = (requested_price, remaining)
            if (group.intent.metadata.get("entry_completion_quote") in {"bid", "ask"}
                    and group.deferred_reprice == fingerprint and group.failed_reprice_at is not None
                    and (record_time - group.failed_reprice_at).total_seconds() < 1.0):
                continue
            if self.reprice_authorizer is not None and group.intent.metadata.get("entry_completion_quote") in {"bid", "ask"}:
                if not await self.reprice_authorizer(group.intent, group.account_id, requested_price, remaining):
                    if group.deferred_reprice != fingerprint:
                        self._record("order_management", "entry_reprice_deferred", root_order_id,
                                     group.account_id, record_time,
                                     {"reason": "portfolio_allocation_capacity", "requested_price": requested_price,
                                      "remaining_quantity": remaining})
                    group.deferred_reprice = fingerprint
                    continue
            try:
                async with self._command_lane(group.account_id):
                    if (group.state in TERMINAL_MANAGEMENT_STATES
                            or group.state in {OrderManagementState.CANCEL_PENDING, OrderManagementState.OUTCOME_UNKNOWN}
                            or root_order_id in group.terminal_broker_order_ids):
                        continue
                    response = await self.broker.modify_order(
                        group.account_id,
                        root_order_id,
                        replacement,
                    )
                if _warning_response(response):
                    async with self._warning_lane:
                        response = await self._resolve_warning_chain_locked(group, response)
                _require_modify_acknowledgement(response)
            except Exception as exc:
                group.deferred_reprice = fingerprint
                group.failed_reprice_at = record_time
                self._record(
                    "broker",
                    "order_reprice_error",
                    root_order_id,
                    group.account_id,
                    record_time,
                    {
                        "order_group_id": group.group_id,
                        "error": str(exc),
                        "requested_price": requested_price,
                    },
                )
                await self.reconcile()
                continue
            group.orders[request_index] = replacement
            group.deferred_reprice = None
            group.failed_reprice_at = None
            modified = True
            self._record(
                "broker",
                "order_repriced",
                root_order_id,
                group.account_id,
                record_time,
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
            group.last_reprice_at = record_time.astimezone(timezone.utc)
            group.current_limit_price = requested_price
            group.internal_reaction_ms = (perf_counter() - reaction_started) * 1_000
        return modified

    async def _cancel_open_entry_roots(
        self,
        group: _ManagedOrderGroup,
        reason: str,
        *,
        eligible_order_ids: set[str] | None = None,
    ) -> bool:
        roots = _open_entry_roots(group)
        if eligible_order_ids is not None:
            roots = tuple(
                row for row in roots if str(row[0]) in eligible_order_ids
            )
        if not roots:
            return False
        # Freeze every path that can modify or match the entry before yielding
        # to the broker cancellation call.  In live trading the adaptive
        # repricer is a separate task; leaving it active allows a reprice to
        # race the risk-reduction command and make the cancelled buy executable
        # again at a newer ask.
        if (
            group.reprice_task is not None
            and not group.reprice_task.done()
            and group.reprice_task is not asyncio.current_task()
        ):
            group.reprice_task.cancel()
        self._transition(
            group,
            OrderManagementState.CANCEL_PENDING,
            {"event": "adaptive_cancel_requested", "reason": reason},
        )
        for broker_order_id, _ in roots:
            async with self._command_lane(group.account_id):
                response = await self.broker.cancel_order(group.account_id, broker_order_id)
            self._record(
                "broker",
                "order_cancel_requested",
                broker_order_id,
                group.account_id,
                self._causal_group_time(group.intent, previous=group.updated_at),
                {"order_group_id": group.group_id, "reason": reason, "broker_response": response},
            )
        return True

    async def cancel_entry_acquisition(self, intent: StrategyIntent, *, account_id: str) -> None:
        """Cancel acquisition even when no share has filled yet."""
        await self._cancel_pending_acquisition_before_exit(intent, account_id=account_id)

    def working_exit_quantity(self, ticker: str, account_id: str) -> float:
        """Use acknowledged OMS state without a broker query on every trade."""
        return sum(
            max(0.0, group.remaining_quantity)
            for group in self._groups.values()
            if group.account_id == account_id and group.intent.ticker.upper() == ticker.upper()
            and str(group.intent.action) in {"exit", "cover"}
            and group.state not in TERMINAL_MANAGEMENT_STATES
        )

    async def pending_exit_quantity(self, intent: StrategyIntent, *, account_id: str) -> float:
        """Count a working exit OCA once, including unresolved submissions."""
        live = {str(order.orderId): order for order in await self.broker.live_orders()
                if order.order_status in OPEN_ORDER_STATUSES}
        pending = 0.0
        for group in self._groups.values():
            if (group.account_id != account_id or group.intent.ticker != intent.ticker
                    or group.intent.metadata.get("assignment_id") != intent.metadata.get("assignment_id")
                    or str(group.intent.action) not in {"exit", "cover"}):
                continue
            if group.state == OrderManagementState.OUTCOME_UNKNOWN:
                pending += group.remaining_quantity
                continue
            pending += max((float(live[key].remainingQuantity) for key in group.broker_order_ids
                            if key in live), default=0.0)
        return pending

    async def _replace_protective_stop(self, intent: StrategyIntent, *, account_id: str) -> OrderGroupSnapshot:
        desired = float(intent.invalidation_price or 0)
        if desired <= 0 or desired >= intent.reference_price:
            raise ValueError("Long support stop must be positive and below the market")
        assignment_id = str(intent.metadata.get("assignment_id") or "")
        changed: list[_ManagedOrderGroup] = []
        for order in await self.broker.live_orders():
            group = self._group_for_order(order)
            if (group is None or group.account_id != account_id
                    or group.intent.ticker != intent.ticker
                    or str(group.intent.metadata.get("assignment_id") or "") != assignment_id
                    or order.order_status not in OPEN_ORDER_STATUSES
                    or order.orderType.upper() not in {"STP", "STOP_LIMIT"}):
                continue
            index = group.broker_order_request_indexes.get(str(order.orderId))
            if index is None:
                continue
            request = group.orders[index]
            if desired <= max(float(request.auxPrice or 0), float(order.auxPrice or 0)):
                # Recover an amendment whose acknowledgement was lost. The
                # repair contract must retain the stop actually held by the
                # broker instead of later restoring the previous lower stop.
                confirmed_stop = float(order.auxPrice or 0)
                if confirmed_stop >= desired:
                    group.orders[index] = replace(request, auxPrice=confirmed_stop, price=order.price)
                    profile = group.intent.resolved_protection_profile()
                    if profile is not None:
                        profile = replace(profile, slices=tuple(
                            replace(item, stop=replace(item.stop, price=confirmed_stop)) for item in profile.slices
                        ))
                    group.intent = replace(group.intent, invalidation_price=confirmed_stop, protection_profile=profile,
                                           metadata={**group.intent.metadata, "confirmed_support_stop": confirmed_stop})
                changed.append(group)
                continue
            replacement = replace(request, auxPrice=desired,
                                  price=(float(request.price) + desired - float(request.auxPrice or 0)
                                         if request.orderType == "STOP_LIMIT" and request.price is not None
                                         else request.price))
            async with self._command_lane(account_id):
                response = await self.broker.modify_order(account_id, str(order.orderId), replacement)
            if _warning_response(response):
                async with self._warning_lane:
                    response = await self._resolve_warning_chain_locked(group, response)
            _require_modify_acknowledgement(response)
            group.orders[index] = replacement
            profile = group.intent.resolved_protection_profile()
            if profile is not None:
                profile = replace(profile, slices=tuple(
                    replace(item, stop=replace(item.stop, price=desired)) for item in profile.slices
                ))
            group.intent = replace(group.intent, invalidation_price=desired, protection_profile=profile,
                                   metadata={**group.intent.metadata, "confirmed_support_stop": desired})
            group.updated_at = intent.event_time
            self._transition(group, group.state, {"event": "support_stop_replaced", "stop": desired})
            self._record("broker", "protective_stop_replaced", str(order.orderId), account_id,
                         intent.event_time, {"stop": desired, "intent_id": intent.intent_id,
                                             "selection": intent.metadata.get("protective_stop_selection", {})})
            changed.append(group)
        if not changed:
            raise ValueError("No broker-held support stop is available for replacement")
        return changed[-1].snapshot(self.policy.version)

    async def _cancel_pending_acquisition_before_exit(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
    ) -> None:
        """Request cancellation of every compatible entry root before reducing risk.

        A partially filled parent remains a live acquisition order.  Submitting
        a sell while that buy can still fill creates a race that can reopen the
        position behind the exit.  The broker command lane therefore observes
        one strict order: cancel acquisition roots first, then modify or submit
        the managed exit.  Protective children are reconciled by the existing
        managed-exit path after the parent cancellation.
        """

        action = str(intent.action)
        if action not in {
            "exit",
            "take_profit",
            "reduce_long",
            "cover",
            "reduce_short",
        }:
            return
        desired_exit_side = _intent_side(intent)
        live_open_order_ids = {
            str(order.orderId)
            for order in await self.broker.live_orders()
            if order.order_status in OPEN_ORDER_STATUSES
        }
        cancelled_group_ids: list[str] = []
        for group in tuple(self._groups.values()):
            if group.account_id != account_id:
                continue
            if group.intent.ticker.upper() != intent.ticker.upper():
                continue
            if (intent.metadata.get("assignment_id")
                    and group.intent.metadata.get("assignment_id") != intent.metadata["assignment_id"]):
                continue
            # A zero-fill entry has not created the position being reduced.
            # Preserve its abstract protection contract for callers that are
            # only replacing protection, while actual partial acquisitions are
            # frozen before their held shares are sold.
            if group.filled_quantity <= 1e-9 and not intent.metadata.get("cancel_entry_acquisition"):
                continue
            roots = [
                (broker_order_id, request_index)
                for broker_order_id, request_index in _open_entry_roots(group)
                if str(broker_order_id) in live_open_order_ids
                if group.orders[request_index].side.upper() != desired_exit_side
            ]
            if not roots:
                continue
            if await self._cancel_open_entry_roots(
                group,
                "risk_reduction_authorized",
                eligible_order_ids={str(row[0]) for row in roots},
            ):
                cancelled_group_ids.append(group.group_id)
        if not cancelled_group_ids:
            return
        # Do not reconcile the source group between cancellation and exit
        # submission. Reconciliation may repair temporarily missing
        # protection; doing it in this narrow transition would manufacture a
        # fresh sell OCA before the managed exit owns the position. The normal
        # managed-exit path delegates protection immediately after submission,
        # and later reconciliation then observes that authority.
        self._record(
            "order_management",
            "entry_acquisition_frozen_before_exit",
            intent.intent_id,
            account_id,
            intent.event_time,
            {
                "ticker": intent.ticker.upper(),
                "exit_action": action,
                "source_group_ids": cancelled_group_ids,
                "command_order": "cancel_entries_then_submit_exit",
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
            group.broker_order_slices[order.broker_order_id] = _slice_for_canonical_order(group, order)
            request_index = _request_index_for_identity(
                group,
                str(order.client_order_id or order.parent_order_id or ""),
            )
            if request_index is not None:
                group.broker_order_request_indexes[order.broker_order_id] = request_index
        fill_role = group.broker_order_roles.get(order.broker_order_id, "")
        fingerprint = _canonical_order_state_fingerprint(order)
        if group.broker_order_state_fingerprints.get(order.broker_order_id) == fingerprint:
            return group.snapshot(
                self.policy.version,
                fill_role=fill_role,
                broker_order_id=order.broker_order_id,
                slice_id=group.broker_order_slices.get(order.broker_order_id, ""),
            )
        group.broker_order_state_fingerprints[order.broker_order_id] = fingerprint
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
        if group.protection_delegated:
            return {
                "required_quantity": 0.0,
                "protected_quantity": 0.0,
                "actions": [],
                "status": "delegated_to_managed_exit",
            }
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
        initial_entry_group = str(group.intent.action) in {"enter_long", "enter_short"}
        group_exit_quantity = sum(
            quantity
            for order_id, quantity in group.filled_by_broker_order.items()
            if group.broker_order_roles.get(order_id)
            in {"profit_target", "protective_stop", "trailing_stop"}
        )
        group_open_quantity = max(
            0.0,
            float(group.filled_quantity) - group_exit_quantity,
        )
        # Broker position snapshots can already contain fills whose order-state
        # messages have not yet been consumed. During a sliced entry, reconcile
        # only this group's causally processed, still-open quantity. Otherwise
        # the first slice can protect future fills, or a completed campaign can
        # mistake a later re-entry position for its own and create a stale stop.
        required = (
            min(abs(position_quantity), group_open_quantity)
            if initial_entry_group
            else abs(position_quantity)
        )
        live_orders = await self.broker.live_orders()
        processed_entry_parent_quantities = {
            group.orders[request_index].cOID: float(filled)
            for broker_order_id, filled in group.filled_by_broker_order.items()
            if filled > 0
            and group.broker_order_roles.get(broker_order_id) == "entry"
            and (request_index := group.broker_order_request_indexes.get(broker_order_id))
            is not None
            and group.orders[request_index].cOID
        }
        owned_broker_order_ids = set(group.broker_order_ids)
        protective = [
            order
            for order in live_orders
            if order.account == group.account_id
            and order.ticker.upper() == group.intent.ticker.upper()
            and order.order_status in OPEN_ORDER_STATUSES
            and (
                initial_entry_group
                or order.order_status != OrderStatus.INACTIVE
            )
            and order.orderType.upper() in {"STP", "STOP_LIMIT", "TRAIL", "TRAILLMT"}
            and (
                str(order.parentId or "").startswith(self._protective_order_prefix())
                or str(order.cOID or "").startswith(self._protective_order_prefix())
            )
            and (
                not initial_entry_group
                or str(order.parentId or "") in processed_entry_parent_quantities
                or (
                    "repair-" in str(order.cOID or "")
                    and str(order.orderId) in owned_broker_order_ids
                )
            )
        ]
        by_protection_group: dict[str, float] = {}
        protection_groups: dict[str, list[LiveOrder]] = {}
        for order in protective:
            key = _protection_group_key(order)
            protection_groups.setdefault(key, []).append(order)
            effective_remaining = float(order.remainingQuantity)
            if initial_entry_group and str(order.parentId or ""):
                # An attached child is committed broker protection even while
                # held inactive for a partially filled parent. Count only the
                # causally processed parent fill, never the future slice size.
                effective_remaining = min(
                    effective_remaining,
                    processed_entry_parent_quantities.get(
                        str(order.parentId),
                        0.0,
                    ),
                )
            by_protection_group[key] = max(
                by_protection_group.get(key, 0.0),
                effective_remaining,
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
            confirmed_stop = float(group.intent.metadata.get("confirmed_support_stop") or 0)
            # A broker-confirmed trailing stop may be above the entry price.
            # Repair its quantity at that exact price; do not revalidate it as
            # a new entry stop or silently loosen it back below entry.
            stops = [confirmed_stop] if confirmed_stop > 0 else [
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
                    raw=_protective_repair_raw(group.orders[0].raw),
                )
            if repair is not None:
                target_prices = [
                    float(item.profit_target_price)
                    for item in profile.slices
                    if item.profit_target_price is not None
                    and float(item.profit_target_price) > 0
                ]
                target_price = (
                    target_prices[0]
                    if target_prices
                    else float(group.intent.profit_target_price or 0)
                )
                valid_target = bool(
                    target_price > 0
                    and (
                        target_price > float(group.intent.reference_price)
                        if position_side == "long"
                        else target_price < float(group.intent.reference_price)
                    )
                )
                repairs = [repair]
                if valid_target:
                    # A missing stop does not imply its target disappeared.
                    # Transfer only the target capacity being paired with the
                    # repaired stop, otherwise two independent sell groups
                    # compete for the same shares (or fail no-short checks).
                    transfer = missing
                    for orphan in live_orders:
                        if (transfer <= tolerance or str(orphan.orderId) not in owned_broker_order_ids
                                or group.broker_order_roles.get(str(orphan.orderId)) != "profit_target"
                                or orphan.order_status not in OPEN_ORDER_STATUSES):
                            continue
                        amount = min(transfer, float(orphan.remainingQuantity))
                        if amount <= 0:
                            continue
                        async with self._command_lane(group.account_id):
                            if amount >= float(orphan.remainingQuantity) - tolerance:
                                await self.broker.cancel_order(group.account_id, str(orphan.orderId))
                            else:
                                index = group.broker_order_request_indexes[str(orphan.orderId)]
                                replacement = replace(group.orders[index], quantity=float(orphan.filledQuantity)
                                                      + float(orphan.remainingQuantity) - amount)
                                response = await self.broker.modify_order(group.account_id, str(orphan.orderId), replacement)
                                _require_modify_acknowledgement(response)
                                group.orders[index] = replacement
                        transfer -= amount
                    refreshed_orders = {str(row.orderId): row for row in await self.broker.live_orders()}
                    for orphan in live_orders:
                        refreshed = refreshed_orders.get(str(orphan.orderId))
                        if (str(orphan.orderId) in owned_broker_order_ids
                                and group.broker_order_roles.get(str(orphan.orderId)) == "profit_target"
                                and refreshed is not None and refreshed.order_status in OPEN_ORDER_STATUSES
                                and float(refreshed.remainingQuantity) > max(0.0, required - missing) + tolerance):
                            return {"required": required, "coverage": coverage, "status": "target_transfer_pending"}
                    positions_now = await self.broker.positions(group.account_id)
                    held_now = sum(abs(float(row.position)) for row in positions_now
                                   if int(row.conid) == int(group.orders[0].conid))
                    if held_now + tolerance < required:
                        return {"required": held_now, "coverage": coverage, "status": "position_changed_during_repair"}
                    target_raw = {
                        **dict(group.orders[0].raw or {}),
                        "canonical_metadata": {
                            **dict(
                                dict(group.orders[0].raw or {}).get(
                                    "canonical_metadata"
                                )
                                or {}
                            ),
                            "execution_role": "profit_target",
                            "reason": "restore_position_profit_target",
                        },
                    }
                    repairs.insert(
                        0,
                        OrderRequest(
                            acctId=group.account_id,
                            conid=group.orders[0].conid,
                            secType=group.orders[0].secType,
                            cOID=(
                                f"{self._protective_order_prefix()}"
                                f"repair-target-{uuid4().hex[:12]}"
                            ),
                            ticker=group.orders[0].ticker,
                            orderType="LMT",
                            side="SELL" if position_side == "long" else "BUY",
                            quantity=missing,
                            tif=group.orders[0].tif,
                            outsideRTH=group.orders[0].outsideRTH,
                            price=target_price,
                            listingExchange=group.orders[0].listingExchange,
                            isSingleGroup=True,
                            raw=target_raw,
                        ),
                    )
                    repair = replace(repair, isSingleGroup=True)
                    repairs[-1] = repair
                async with self._command_lane(group.account_id):
                    response = await self.broker.place_orders(group.account_id, repairs)
                for request, role in zip(
                    repairs,
                    (
                        ("profit_target", "protective_stop")
                        if valid_target
                        else ("protective_stop",)
                    ),
                    strict=True,
                ):
                    request_index = len(group.orders)
                    group.orders.append(request)
                    group.plan = _extend_managed_plan(
                        group.plan,
                        group.orders,
                        slice_id="repair-protection",
                    )
                    if request.cOID:
                        self._group_by_client_id[request.cOID] = group.group_id
                for row, request, role in zip(
                    response,
                    repairs,
                    (
                        ("profit_target", "protective_stop")
                        if valid_target
                        else ("protective_stop",)
                    ),
                    strict=True,
                ):
                    order_id = str(row.get("order_id") or row.get("orderId") or "")
                    if not order_id:
                        continue
                    if order_id not in group.broker_order_ids:
                        group.broker_order_ids.append(order_id)
                    self._group_by_broker_id[order_id] = group.group_id
                    group.broker_order_roles[order_id] = role
                    group.broker_order_request_indexes[order_id] = group.orders.index(
                        request
                    )
                actions.append(
                    {
                        "action": (
                            "place_missing_oca_protection"
                            if valid_target
                            else "place_missing_backstop"
                        ),
                        "quantity": repair.quantity,
                        "stop_price": stop_price,
                        "target_price": target_price if valid_target else None,
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
                "protection_delegated": group.protection_delegated,
            },
        )
        if (
            self.state_callback is not None
            and state in {
                OrderManagementState.OUTCOME_UNKNOWN,
                OrderManagementState.CANCELLED,
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


def _require_modify_acknowledgement(rows: list[dict[str, Any]]) -> None:
    if not rows or _warning_response(rows):
        raise ValueError("Broker modification is not acknowledged")
    for row in rows:
        if row.get("error") or row.get("errorCode"):
            raise ValueError(str(row.get("error") or row.get("message") or row["errorCode"]))


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
    return _open_roots_for_role(group, "entry")


def _open_adaptive_roots(
    group: _ManagedOrderGroup,
) -> tuple[tuple[str, int], ...]:
    role = (
        "managed_exit"
        if str(group.intent.action)
        in {"reduce_long", "take_profit", "exit", "reduce_short", "cover"}
        else "entry"
    )
    return _open_roots_for_role(group, role)


def _open_roots_for_role(
    group: _ManagedOrderGroup,
    role: str,
) -> tuple[tuple[str, int], ...]:
    rows: list[tuple[str, int]] = []
    for broker_order_id in group.broker_order_ids:
        if group.broker_order_roles.get(broker_order_id) != role:
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


def _live_order_state_fingerprint(order: LiveOrder) -> tuple[Any, ...]:
    """Return only fields whose change requires a new OMS projection."""

    return (
        str(order.order_status),
        float(order.filledQuantity),
        float(order.remainingQuantity),
        float(order.avgPrice),
        float(order.price or 0),
        float(order.auxPrice or 0),
        str(order.statusDescription or ""),
    )


def _canonical_order_state_fingerprint(order: OrderState) -> tuple[Any, ...]:
    return (
        str(order.lifecycle_state),
        str(order.broker_status_raw),
        float(order.filled_quantity),
        float(order.remaining_quantity),
        float(order.average_fill_price),
        float(order.limit_price or 0),
        float(order.stop_price or 0),
        str(order.warning or ""),
        str(order.rejection_code or ""),
        str(order.rejection_reason or ""),
    )


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
