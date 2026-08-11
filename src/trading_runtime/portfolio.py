from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from time import monotonic
from typing import Any, Mapping
from uuid import uuid4

from src.request_context import causal_identity, normalize_request_identity

from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.control_plane import TradingControlPlane
from src.trading_runtime.domain import OrderState, TradingStateSnapshot
from src.trading_runtime.execution_policies import AddProtectionPolicy, StopOrderType
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary, LiveOrder, PortfolioPosition
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import OrderGroupSnapshot, OrderManagementState
from src.trading_runtime.signals import StrategyIntent


ENTRY_ACTIONS = {"enter_long", "add_long", "enter_short", "add_short"}
REDUCTION_ACTIONS = {"reduce_long", "take_profit", "exit", "reduce_short", "cover"}


class PortfolioDecisionStatus(StrEnum):
    APPROVED = "approved"
    RESIZED = "resized"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class PortfolioSyncState(StrEnum):
    INITIALIZING = "initializing"
    SYNCHRONIZED = "synchronized"
    DEGRADED = "degraded"
    RECONCILING = "reconciling"
    ENTRIES_BLOCKED = "entries_blocked"
    FULLY_BLOCKED = "fully_blocked"
    DISABLED = "disabled"


class PortfolioControlMode(StrEnum):
    ENABLED = "enabled"
    ENTRIES_PAUSED = "entries_paused"
    REDUCE_ONLY = "reduce_only"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class PortfolioPolicy:
    """Immutable per-account capital, exposure, capability, and loss policy."""

    policy_id: str = "default"
    revision: int = 1
    eligible_equity_fraction: float = 1.0
    minimum_cash_reserve: float = 0.0
    maximum_buying_power_utilization: float = 1.0
    maximum_gross_exposure: float = 5_000_000.0
    maximum_net_long_exposure: float = 5_000_000.0
    maximum_net_short_exposure: float = 0.0
    maximum_position_fraction: float = 0.25
    maximum_ticker_fraction: float = 0.25
    maximum_strategy_fraction: float = 1.0
    maximum_sector_fraction: float = 1.0
    maximum_industry_fraction: float = 1.0
    maximum_correlated_group_fraction: float = 1.0
    maximum_planned_risk_fraction: float = 0.01
    maximum_open_risk_fraction: float = 0.05
    maximum_open_positions: int = 100
    maximum_order_quantity: float = 100_000.0
    maximum_order_notional: float = 1_000_000.0
    maximum_daily_loss: float = 100_000.0
    maximum_drawdown: float = 250_000.0
    daily_loss_warning: float = 0.0
    emergency_loss: float = 0.0
    maximum_snapshot_age_ms: int = 6_000
    maximum_protection_slices: int = 4
    maximum_internal_reaction_ms: int = 100
    allow_long: bool = True
    allow_short: bool = False
    allow_margin: bool = False
    allow_unsettled_cash: bool = False
    allow_outside_rth: bool = False
    allow_overnight: bool = True
    allowed_security_types: tuple[str, ...] = ("STK",)
    allowed_currencies: tuple[str, ...] = ("USD", "CAD")
    restricted_symbols: tuple[str, ...] = ()
    block_on_unattributed_position: bool = True
    allow_stop_limit_protection: bool = False
    allow_partial_profit_pocket: bool = True
    allow_emergency_auto_liquidation: bool = False
    allowed_execution_policies: tuple[str, ...] = ("*",)
    allowed_protection_profiles: tuple[str, ...] = ("*",)

    def __post_init__(self) -> None:
        if not self.policy_id or self.revision < 1:
            raise ValueError("Portfolio policy identity and positive revision are required")
        fractions = {
            "eligible_equity_fraction": self.eligible_equity_fraction,
            "maximum_buying_power_utilization": self.maximum_buying_power_utilization,
            "maximum_position_fraction": self.maximum_position_fraction,
            "maximum_ticker_fraction": self.maximum_ticker_fraction,
            "maximum_strategy_fraction": self.maximum_strategy_fraction,
            "maximum_sector_fraction": self.maximum_sector_fraction,
            "maximum_industry_fraction": self.maximum_industry_fraction,
            "maximum_correlated_group_fraction": self.maximum_correlated_group_fraction,
            "maximum_planned_risk_fraction": self.maximum_planned_risk_fraction,
            "maximum_open_risk_fraction": self.maximum_open_risk_fraction,
        }
        if any(not 0 <= value <= 1 for value in fractions.values()):
            raise ValueError("Portfolio policy fractions must be between zero and one")
        nonnegative = (
            self.minimum_cash_reserve,
            self.maximum_gross_exposure,
            self.maximum_net_long_exposure,
            self.maximum_net_short_exposure,
            self.maximum_order_quantity,
            self.maximum_order_notional,
            self.maximum_daily_loss,
            self.maximum_drawdown,
            self.daily_loss_warning,
            self.emergency_loss,
        )
        if any(value < 0 for value in nonnegative):
            raise ValueError("Portfolio policy limits cannot be negative")
        if (
            self.maximum_open_positions < 0
            or self.maximum_snapshot_age_ms < 1
            or self.maximum_protection_slices < 1
            or self.maximum_internal_reaction_ms < 1
        ):
            raise ValueError("Portfolio position count and freshness limits are invalid")
        if self.daily_loss_warning and self.daily_loss_warning > self.maximum_daily_loss:
            raise ValueError("daily loss warning cannot exceed the hard daily loss limit")
        if self.emergency_loss and self.emergency_loss < self.maximum_daily_loss:
            raise ValueError("emergency loss cannot be below the hard daily loss limit")

    @property
    def identity(self) -> str:
        return f"{self.policy_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class PortfolioAccountProfile:
    """Stable application account key plus externally bound broker account."""

    account_key: str
    account_id: str
    mode: str
    account_class: str
    policy: PortfolioPolicy
    session_key: str = ""
    enabled: bool = True
    base_currency: str = "USD"
    strategy_allocations: Mapping[str, float] = field(default_factory=dict)
    strategy_mandates: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.account_key or not self.account_id:
            raise ValueError("Portfolio account_key and account_id are required")
        if self.mode not in {"live", "paper", "replay", "backtest", "backtest_debug"}:
            raise ValueError(f"Unsupported portfolio account mode: {self.mode}")
        if any(not 0 <= float(value) <= 1 for value in self.strategy_allocations.values()):
            raise ValueError("Strategy allocation fractions must be between zero and one")


@dataclass(frozen=True, slots=True)
class PortfolioGroupPolicy:
    group_id: str
    account_keys: tuple[str, ...]
    maximum_gross_exposure: float
    maximum_ticker_exposure: float

    def __post_init__(self) -> None:
        if not self.group_id or not self.account_keys:
            raise ValueError("Portfolio group identity and accounts are required")
        if self.maximum_gross_exposure < 0 or self.maximum_ticker_exposure < 0:
            raise ValueError("Portfolio group limits cannot be negative")


@dataclass(frozen=True, slots=True)
class PortfolioReservation:
    reservation_id: str
    decision_id: str
    intent_id: str
    account_key: str
    account_id: str
    strategy_id: str
    assignment_id: str
    ticker: str
    action: str
    quantity: float
    remaining_quantity: float
    reference_price: float
    reserved_notional: float
    reserved_planned_risk: float
    created_at: datetime
    status: str = "reserved"
    filled_quantity: float = 0.0
    admission_epoch: int = 0
    admission_owner: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioAllocationLot:
    allocation_id: str
    account_key: str
    account_id: str
    strategy_id: str
    strategy_revision: int
    assignment_id: str
    ticker: str
    quantity: float
    average_price: float
    planned_risk: float
    realized_pnl: float
    source: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioReconciliationDifference:
    account_key: str
    ticker: str
    broker_quantity: float
    attributed_quantity: float
    unattributed_quantity: float
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    decision_id: str
    request_id: str
    account_key: str
    account_id: str
    policy_id: str
    policy_revision: int
    snapshot_id: str
    status: PortfolioDecisionStatus
    requested_quantity: float
    approved_quantity: float
    approved_notional: float
    planned_loss: float
    reservation_id: str
    reasons: tuple[str, ...]
    metrics_before: Mapping[str, float]
    metrics_after: Mapping[str, float]
    decided_at: datetime

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class PortfolioRebalanceProposal:
    proposal_id: str
    request_id: str
    account_key: str
    requested_ticker: str
    candidate_ticker: str
    candidate_quantity: float
    candidate_unrealized_return_pct: float
    opportunity_score: float
    minimum_improvement_pct: float
    required_action_authority: str
    status: str
    reasons: tuple[str, ...]
    proposed_at: datetime

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PortfolioAccountState:
    profile: PortfolioAccountProfile
    sync_state: PortfolioSyncState = PortfolioSyncState.INITIALIZING
    control_mode: PortfolioControlMode = PortfolioControlMode.ENABLED
    summary: AccountSummary | None = None
    ledger: AccountLedger | None = None
    positions: dict[str, PortfolioPosition] = field(default_factory=dict)
    open_orders: list[LiveOrder | OrderState] = field(default_factory=list)
    observed_at: datetime | None = None
    snapshot_id: str = ""
    stale_reason: str = "Initial broker synchronization has not completed."
    realized_pnl_today: float = 0.0
    peak_net_liquidation: float = 0.0
    realized_pnl_baseline: float | None = None
    component_watermarks: dict[str, datetime] = field(default_factory=dict)
    policy_override: PortfolioPolicy | None = None
    disabled_strategy_allocations: set[str] = field(default_factory=set)
    pending_operational_commands: list[dict[str, Any]] = field(default_factory=list)

    @property
    def synchronized(self) -> bool:
        return self.sync_state == PortfolioSyncState.SYNCHRONIZED


def default_policy_for_account(account_class: str) -> PortfolioPolicy:
    normalized = account_class.strip().lower()
    if normalized in {"rrsp", "registered", "tfsa"}:
        return PortfolioPolicy(
            policy_id="registered-long-only",
            allow_short=False,
            allow_margin=False,
            maximum_net_short_exposure=0.0,
            maximum_buying_power_utilization=0.95,
            minimum_cash_reserve=500.0,
        )
    if normalized == "cash":
        return PortfolioPolicy(
            policy_id="cash-long-only",
            allow_short=False,
            allow_margin=False,
            maximum_net_short_exposure=0.0,
            maximum_buying_power_utilization=0.95,
        )
    if normalized == "margin":
        return PortfolioPolicy(
            policy_id="margin",
            allow_short=True,
            allow_margin=True,
            maximum_net_short_exposure=2_500_000.0,
        )
    return PortfolioPolicy(policy_id=f"{normalized or 'default'}-portfolio")


class PortfolioManagementEngine:
    """Portfolio authority between semantic strategy requests and OMS.

    Live and paper account state is synchronized from the broker. Replay and
    backtest use the same decisions and reservations against the simulated
    broker adapter. Account routing is always explicit.
    """

    def __init__(
        self,
        profiles: list[PortfolioAccountProfile] | tuple[PortfolioAccountProfile, ...],
        *,
        journal: TradingJournal,
        run_id: str,
        strategy_id: str,
        strategy_revision: int,
        groups: list[PortfolioGroupPolicy] | tuple[PortfolioGroupPolicy, ...] = (),
        control_plane: TradingControlPlane | None = None,
        allocation_identity: str = "",
    ) -> None:
        if not profiles:
            raise ValueError("Portfolio management requires at least one account profile")
        self.journal = journal
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.strategy_revision = strategy_revision
        self.allocation_identity = allocation_identity or strategy_id
        self.states = {profile.account_id: PortfolioAccountState(profile=profile) for profile in profiles}
        if len(self.states) != len(profiles):
            raise ValueError("Portfolio broker account ids must be unique")
        self.by_key = {profile.account_key: self.states[profile.account_id] for profile in profiles}
        if len(self.by_key) != len(profiles):
            raise ValueError("Portfolio account keys must be unique")
        self.groups = {group.group_id: group for group in groups}
        unknown = {
            key
            for group in groups
            for key in group.account_keys
            if key not in self.by_key
        }
        if unknown:
            raise ValueError(f"Portfolio groups reference unknown account keys: {', '.join(sorted(unknown))}")
        self.reservations: dict[str, PortfolioReservation] = {}
        self.allocations: dict[str, PortfolioAllocationLot] = {}
        self.rebalance_proposals: list[PortfolioRebalanceProposal] = []
        self.differences: dict[tuple[str, str], PortfolioReconciliationDifference] = {}
        self.decisions: list[PortfolioDecision] = []
        self.control_plane = control_plane
        self._account_locks = {
            account_id: (
                control_plane.account_lock(account_id)
                if control_plane is not None
                else asyncio.Lock()
            )
            for account_id in self.states
        }
        self._group_locks = {
            group_id: (
                control_plane.group_lock(group_id)
                if control_plane is not None
                else asyncio.Lock()
            )
            for group_id in self.groups
        }
        self._last_filled_by_reservation: dict[str, float] = {}
        self._active_admission_lease: dict[str, Any] | None = None
        self._restore()

    def bind_control_plane(self, control_plane: TradingControlPlane) -> None:
        """Promote this engine's admission locks to shared account authorities."""

        self.control_plane = control_plane
        self._account_locks = {
            account_id: control_plane.account_lock(account_id)
            for account_id in self.states
        }
        self._group_locks = {
            group_id: control_plane.group_lock(group_id)
            for group_id in self.groups
        }

    async def synchronize(self, broker: BrokerAdapter) -> None:
        """Create one coherent multi-account snapshot from broker authorities."""
        live_orders = await broker.live_orders()
        for account_id, state in self.states.items():
            async with self._account_locks[account_id]:
                if not state.profile.enabled:
                    state.sync_state = PortfolioSyncState.DISABLED
                    state.control_mode = PortfolioControlMode.DISABLED
                    state.stale_reason = "Account profile is disabled."
                    self._persist_state(state)
                    continue
                try:
                    summary, ledger, positions = await asyncio.gather(
                        broker.account_summary(account_id),
                        broker.account_ledger(account_id),
                        broker.positions(account_id),
                    )
                    state.summary = summary
                    state.ledger = ledger
                    state.positions = {_ticker(position): position for position in positions}
                    state.open_orders = [
                        order
                        for order in live_orders
                        if order.account == account_id and not _terminal_order(order)
                    ]
                    synchronized_at = datetime.now(timezone.utc)
                    state.component_watermarks = {
                        "summary": summary.timestamp.astimezone(timezone.utc),
                        "ledger": ledger.timestamp.astimezone(timezone.utc),
                        "positions": synchronized_at,
                        "orders": synchronized_at,
                    }
                    state.observed_at = min(state.component_watermarks.values())
                    state.snapshot_id = str(uuid4())
                    state.peak_net_liquidation = max(
                        state.peak_net_liquidation,
                        float(summary.netliquidation),
                    )
                    self._update_realized_pnl(state, float(ledger.realizedpnl))
                    state.sync_state = PortfolioSyncState.SYNCHRONIZED
                    state.stale_reason = ""
                    self._reconcile_account(state)
                except Exception as exc:
                    state.sync_state = PortfolioSyncState.ENTRIES_BLOCKED
                    state.stale_reason = str(exc)
                self._persist_state(state)

    def synchronize_snapshot(
        self,
        account_id: str,
        *,
        summary: AccountSummary,
        ledger: AccountLedger,
        positions: list[PortfolioPosition],
        open_orders: list[LiveOrder] | None = None,
        snapshot_id: str = "",
    ) -> None:
        """Deterministic synchronization seam for replay, backtest, and tests."""
        state = self._state(account_id)
        state.summary = summary
        state.ledger = ledger
        state.positions = {_ticker(position): position for position in positions}
        state.open_orders = list(open_orders or [])
        synchronized_at = max(summary.timestamp, ledger.timestamp).astimezone(timezone.utc)
        state.component_watermarks = {
            "summary": summary.timestamp.astimezone(timezone.utc),
            "ledger": ledger.timestamp.astimezone(timezone.utc),
            "positions": synchronized_at,
            "orders": synchronized_at,
        }
        state.observed_at = min(state.component_watermarks.values())
        state.snapshot_id = snapshot_id or str(uuid4())
        state.peak_net_liquidation = max(state.peak_net_liquidation, float(summary.netliquidation))
        self._update_realized_pnl(state, float(ledger.realizedpnl))
        state.sync_state = PortfolioSyncState.SYNCHRONIZED
        state.stale_reason = ""
        self._reconcile_account(state)
        self._persist_state(state)

    def synchronize_canonical(self, snapshot: TradingStateSnapshot) -> None:
        """Consume the canonical projector used by live, paper, and simulation.

        Incomplete canonical snapshots never clear prior broker positions.
        """
        for account_id, state in self.states.items():
            if account_id not in snapshot.account_ids:
                state.sync_state = PortfolioSyncState.ENTRIES_BLOCKED
                state.stale_reason = "Configured account is absent from the canonical broker snapshot."
                self._persist_state(state)
                continue
            values = [row for row in snapshot.account_values if row.account_id == account_id]
            ledgers = [row for row in snapshot.ledger if row.account_id == account_id]
            positions = [row for row in snapshot.positions if row.account_id == account_id]
            if not snapshot.complete or snapshot.stale:
                state.sync_state = PortfolioSyncState.ENTRIES_BLOCKED
                state.stale_reason = snapshot.stale_reason or "Canonical broker snapshot is incomplete or stale."
                self._persist_state(state)
                continue
            if not values and not ledgers:
                state.sync_state = PortfolioSyncState.ENTRIES_BLOCKED
                state.stale_reason = "Canonical account values and ledger are unavailable."
                self._persist_state(state)
                continue
            summary = AccountSummary(
                account_id=account_id,
                netliquidation=_canonical_amount(values, ledgers, "netliquidation", "netliquidationvalue"),
                totalcashvalue=_canonical_amount(values, ledgers, "totalcashvalue", "cashbalance"),
                buyingpower=_canonical_amount(values, ledgers, "buyingpower"),
                grosspositionvalue=_canonical_amount(values, ledgers, "grosspositionvalue"),
                availablefunds=_canonical_amount(values, ledgers, "availablefunds"),
                excessliquidity=_canonical_amount(values, ledgers, "excessliquidity"),
                currency=state.profile.base_currency,
                timestamp=_oldest_timestamp(values + ledgers, snapshot.as_of),
            )
            base_ledger = next((row for row in ledgers if row.is_base), ledgers[0] if ledgers else None)
            ledger_values = base_ledger.values if base_ledger else {}
            ledger = AccountLedger(
                acctId=account_id,
                cashbalance=float(ledger_values.get("cashbalance") or summary.totalcashvalue),
                settledcash=float(ledger_values.get("settledcash") or ledger_values.get("cashbalance") or summary.totalcashvalue),
                stockmarketvalue=float(ledger_values.get("stockmarketvalue") or summary.grosspositionvalue),
                netliquidationvalue=float(ledger_values.get("netliquidationvalue") or summary.netliquidation),
                realizedpnl=float(ledger_values.get("realizedpnl") or 0),
                unrealizedpnl=float(ledger_values.get("unrealizedpnl") or 0),
                currency=base_ledger.currency if base_ledger else state.profile.base_currency,
                exchangerate=float(ledger_values.get("exchangerate") or 1),
                timestamp=base_ledger.source_event_time if base_ledger else snapshot.as_of,
            )
            converted_positions = [
                PortfolioPosition(
                    acctId=account_id,
                    conid=row.instrument.conid,
                    contractDesc=row.instrument.symbol,
                    position=float(row.quantity),
                    mktPrice=float(row.market_price),
                    mktValue=float(row.market_value),
                    avgCost=float(row.average_cost),
                    avgPrice=float(row.average_price),
                    realizedPnl=float(row.realized_pnl),
                    unrealizedPnl=float(row.unrealized_pnl),
                    currency=row.instrument.currency,
                    assetClass=row.instrument.security_type,
                    raw={
                        **row.raw,
                        "ticker": row.instrument.symbol,
                        "model": row.model,
                        "snapshot_id": row.snapshot_id,
                    },
                )
                for row in positions
            ]
            state.summary = summary
            state.ledger = ledger
            state.positions = {_ticker(row): row for row in converted_positions}
            state.open_orders = [
                row
                for row in snapshot.orders
                if row.account_id == account_id and not row.terminal
            ]
            state.component_watermarks = {
                "summary": _oldest_timestamp(values, snapshot.as_of),
                "ledger": _oldest_timestamp(ledgers, snapshot.as_of),
                "positions": _oldest_timestamp(positions, snapshot.as_of),
                "orders": _oldest_timestamp(
                    [row for row in snapshot.orders if row.account_id == account_id],
                    snapshot.as_of,
                ),
                "executions": _oldest_timestamp(
                    [row for row in snapshot.executions if row.account_id == account_id],
                    snapshot.as_of,
                ),
            }
            state.observed_at = min(state.component_watermarks.values())
            state.snapshot_id = next(
                (row.snapshot_id for row in positions if row.snapshot_id),
                str(uuid4()),
            )
            state.peak_net_liquidation = max(state.peak_net_liquidation, summary.netliquidation)
            self._update_realized_pnl(state, ledger.realizedpnl)
            state.sync_state = PortfolioSyncState.SYNCHRONIZED
            state.stale_reason = ""
            self._reconcile_account(state)
            self._persist_state(state)

    async def approve(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
    ) -> tuple[PortfolioDecision, StrategyIntent | None]:
        state = self._state(account_id)
        self._refresh_operational_state(state)
        group_ids = sorted(
            group.group_id
            for group in self.groups.values()
            if state.profile.account_key in group.account_keys
        )
        locks = [self._group_locks[group_id] for group_id in group_ids]
        for lock in locks:
            await lock.acquire()
        try:
            async with self._account_locks[account_id]:
                resources = [
                    *(f"portfolio-group:{value}" for value in group_ids),
                    f"portfolio-account:{account_id}",
                ]
                owner_id = f"{self.run_id}:{uuid4()}"
                leases: list[dict[str, Any]] = []
                try:
                    for resource_id in resources:
                        deadline = monotonic() + 5.0
                        lease = None
                        while lease is None and monotonic() < deadline:
                            lease = self.journal.acquire_portfolio_admission_lease(
                                resource_id,
                                owner_id=owner_id,
                                ttl_seconds=30.0,
                            )
                            if lease is None:
                                await asyncio.sleep(0.01)
                        if lease is None:
                            raise RuntimeError(
                                f"Portfolio admission lease unavailable for {resource_id}"
                            )
                        leases.append(lease)
                    account_lease = leases[-1]
                    if not self.journal.portfolio_admission_lease_is_current(
                        account_lease["resource_id"],
                        owner_id=owner_id,
                        epoch=int(account_lease["epoch"]),
                    ):
                        raise RuntimeError("Portfolio admission lease became stale")
                    # Reload after all cross-process fences are held so this
                    # admission sees reservations committed by every run.
                    self._restore()
                    self._active_admission_lease = account_lease
                    return self._approve_locked(intent, state)
                finally:
                    self._active_admission_lease = None
                    for lease in reversed(leases):
                        self.journal.release_portfolio_admission_lease(
                            lease["resource_id"],
                            owner_id=owner_id,
                            epoch=int(lease["epoch"]),
                        )
        finally:
            for lock in reversed(locks):
                lock.release()

    def release_intent(self, intent_id: str, *, reason: str) -> None:
        reservation = next(
            (row for row in self.reservations.values() if row.intent_id == intent_id and row.status == "reserved"),
            None,
        )
        if reservation is None:
            return
        self.reservations[reservation.reservation_id] = replace(
            reservation,
            status="released",
            remaining_quantity=0.0,
        )
        self._record(
            "portfolio_reservation",
            reservation.reservation_id,
            reservation.account_id,
            {"event": "reservation_released", "reason": reason, **asdict(self.reservations[reservation.reservation_id])},
        )
        self._persist_state(self._state(reservation.account_id))

    def on_order_group_update(self, snapshot: OrderGroupSnapshot) -> None:
        reservation = next(
            (row for row in self.reservations.values() if row.intent_id == snapshot.intent_id),
            None,
        )
        if reservation is None:
            return
        entry_update = not snapshot.fill_role or snapshot.fill_role == "entry"
        prior_filled = self._last_filled_by_reservation.get(reservation.reservation_id, 0.0)
        filled = max(prior_filled, float(snapshot.filled_quantity)) if entry_update else prior_filled
        incremental = (
            float(snapshot.fill_incremental_quantity)
            if snapshot.fill_incremental_quantity > 0
            else max(0.0, filled - prior_filled)
        )
        if incremental:
            fill_action = snapshot.action if snapshot.fill_role and snapshot.fill_role != "entry" else reservation.action
            self._apply_fill(reservation, incremental, action=fill_action)
            if entry_update:
                self._last_filled_by_reservation[reservation.reservation_id] = filled
        terminal = snapshot.state in {
            OrderManagementState.FILLED,
            OrderManagementState.CANCELLED,
            OrderManagementState.REJECTED,
            OrderManagementState.POLICY_BLOCKED,
        }
        status = snapshot.state.value if entry_update else reservation.status
        remaining = 0.0 if terminal else max(0.0, reservation.quantity - filled)
        self.reservations[reservation.reservation_id] = replace(
            reservation,
            status=status,
            filled_quantity=filled,
            remaining_quantity=remaining,
            reserved_notional=remaining * reservation.reference_price,
            reserved_planned_risk=(
                reservation.reserved_planned_risk * remaining / reservation.quantity
                if reservation.quantity > 0
                else 0.0
            ),
        )
        self._record(
            "portfolio_reservation",
            reservation.reservation_id,
            reservation.account_id,
            {"event": "reservation_updated", **asdict(self.reservations[reservation.reservation_id])},
        )
        self._persist_state(self._state(reservation.account_id))

    def set_control(
        self,
        account_key: str,
        mode: PortfolioControlMode | str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self.by_key.get(account_key)
        if state is None:
            raise KeyError(account_key)
        control = PortfolioControlMode(mode)
        if not state.profile.enabled and control != PortfolioControlMode.DISABLED:
            raise ValueError("A disabled account profile cannot be enabled by an operational command")
        state.control_mode = control
        if self.control_plane is not None:
            self.control_plane.account_control_modes[state.profile.account_id] = control.value
        self._record(
            "portfolio_control",
            account_key,
            state.profile.account_id,
            {"event": "control_changed", "control_mode": control.value, "reason": reason},
        )
        self._persist_state(state)
        return self.account_payload(state.profile.account_id)

    def apply_persisted_operational_state(
        self,
        account_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Merge operator-authored durable controls into the live authority.

        The backend and trading runtime use separate journal connections.
        Applying this before authoritative broker refresh prevents the in-memory
        runtime from overwriting a newly queued operator command.
        """
        state = self._state(account_id)
        if payload.get("control_mode") is not None:
            state.control_mode = PortfolioControlMode(str(payload["control_mode"]))
        selected_policy = payload.get("selected_policy")
        if isinstance(selected_policy, Mapping):
            state.policy_override = narrow_policy_for_account_class(
                portfolio_policy_from_payload(selected_policy),
                state.profile.account_class,
            )
        state.disabled_strategy_allocations = {
            str(item) for item in payload.get("disabled_strategy_allocations") or ()
        }
        state.pending_operational_commands = [
            dict(item)
            for item in payload.get("pending_operational_commands") or ()
            if isinstance(item, Mapping)
        ][-100:]

    def set_strategy_allocation_enabled(
        self,
        account_key: str,
        strategy_id: str,
        *,
        enabled: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self.by_key.get(account_key)
        if state is None:
            raise KeyError(account_key)
        if enabled:
            state.disabled_strategy_allocations.discard(strategy_id)
        else:
            state.disabled_strategy_allocations.add(strategy_id)
        self._record(
            "portfolio_control",
            f"{account_key}:{strategy_id}",
            state.profile.account_id,
            {
                "event": "strategy_allocation_control_changed",
                "strategy_id": strategy_id,
                "enabled": enabled,
                "reason": reason,
            },
        )
        self._persist_state(state)
        return self.account_payload(state.profile.account_id)

    def select_policy(
        self,
        account_key: str,
        policy: PortfolioPolicy,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self.by_key.get(account_key)
        if state is None:
            raise KeyError(account_key)
        state.policy_override = narrow_policy_for_account_class(policy, state.profile.account_class)
        state.control_mode = PortfolioControlMode.ENTRIES_PAUSED
        self._record(
            "portfolio_control",
            account_key,
            state.profile.account_id,
            {
                "event": "portfolio_policy_selected",
                "policy": _policy_payload(state.policy_override),
                "entries_paused": True,
                "reason": reason,
            },
        )
        self._persist_state(state)
        return self.account_payload(state.profile.account_id)

    def account_payload(self, account_id: str) -> dict[str, Any]:
        state = self._state(account_id)
        metrics = self._metrics(state)
        reservations = [
            asdict(row)
            for row in self.reservations.values()
            if row.account_id == account_id and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        ]
        allocations = [asdict(row) for row in self.allocations.values() if row.account_id == account_id]
        differences = [asdict(row) for row in self.differences.values() if row.account_key == state.profile.account_key]
        return {
            "account_key": state.profile.account_key,
            "account_id": state.profile.account_id,
            "account_class": state.profile.account_class,
            "mode": state.profile.mode,
            "session_key": state.profile.session_key,
            "base_currency": state.profile.base_currency,
            "enabled": state.profile.enabled,
            "sync_state": state.sync_state.value,
            "control_mode": state.control_mode.value,
            "snapshot_id": state.snapshot_id,
            "observed_at": state.observed_at.isoformat() if state.observed_at else "",
            "component_watermarks": {
                key: value.isoformat() for key, value in state.component_watermarks.items()
            },
            "stale_reason": state.stale_reason,
            "policy": _policy_payload(self._policy(state)),
            "strategy_allocations": dict(state.profile.strategy_allocations),
            "disabled_strategy_allocations": sorted(state.disabled_strategy_allocations),
            "metrics": metrics,
            "reservations": reservations,
            "allocations": allocations,
            "reconciliation": differences,
        }

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "strategy_revision": self.strategy_revision,
            "accounts": [self.account_payload(account_id) for account_id in self.states],
            "groups": [asdict(group) for group in self.groups.values()],
            "recent_decisions": [decision.payload() for decision in self.decisions[-100:]],
            "recent_rebalance_proposals": [
                proposal.payload() for proposal in self.rebalance_proposals[-100:]
            ],
        }

    def _approve_locked(
        self,
        intent: StrategyIntent,
        state: PortfolioAccountState,
    ) -> tuple[PortfolioDecision, StrategyIntent | None]:
        now = datetime.now(timezone.utc)
        policy = self._policy(state)
        requested = float(intent.quantity)
        reasons: list[str] = []
        entry = intent.action in ENTRY_ACTIONS
        reduction = intent.action in REDUCTION_ACTIONS
        control_mode = state.control_mode
        if self.control_plane is not None:
            control_mode = PortfolioControlMode(
                self.control_plane.account_control_modes.get(
                    state.profile.account_id,
                    control_mode.value,
                )
            )
        if entry and intent.capital_request is not None:
            requested = self._capital_request_quantity(intent, state)
        if not state.profile.enabled or control_mode == PortfolioControlMode.DISABLED:
            reasons.append("account_disabled")
        if entry and control_mode in {PortfolioControlMode.ENTRIES_PAUSED, PortfolioControlMode.REDUCE_ONLY}:
            reasons.append("entries_paused")
        if entry and self.allocation_identity in state.disabled_strategy_allocations:
            reasons.append("strategy_allocation_disabled")
        if not entry and not reduction:
            reasons.append("unsupported_portfolio_action")
        if state.sync_state not in {PortfolioSyncState.SYNCHRONIZED, PortfolioSyncState.DEGRADED}:
            reasons.append("account_not_synchronized")
        if entry and self._snapshot_stale(state, now):
            reasons.append("portfolio_snapshot_stale")
        ticker = intent.ticker.upper()
        if ticker in {symbol.upper() for symbol in policy.restricted_symbols}:
            reasons.append("symbol_restricted")
        requested_sessions = {
            str(value)
            for value in intent.metadata.get("eligible_sessions") or []
        }
        requests_extended_session = (
            intent.metadata.get("session_routing") == "smart"
            and bool(requested_sessions & {"premarket", "after_hours"})
        ) or intent.outside_rth
        if requests_extended_session and not policy.allow_outside_rth:
            reasons.append("outside_rth_not_allowed")
        if bool(intent.metadata.get("would_hold_overnight")) and not policy.allow_overnight:
            reasons.append("overnight_position_not_allowed")
        security_type = str(intent.metadata.get("security_type") or "STK").upper()
        if security_type not in {item.upper() for item in policy.allowed_security_types}:
            reasons.append("security_type_not_allowed")
        currency = str(intent.metadata.get("currency") or state.profile.base_currency).upper()
        if currency not in {item.upper() for item in policy.allowed_currencies}:
            reasons.append("currency_not_allowed")
        fx_to_base = float(intent.metadata.get("fx_to_base") or (1.0 if currency == state.profile.base_currency else 0.0))
        if fx_to_base <= 0:
            reasons.append("fx_rate_unavailable")
        short_action = intent.action in {"enter_short", "add_short"}
        if short_action and not policy.allow_short:
            reasons.append("short_not_allowed")
        if intent.action in {"enter_long", "add_long"} and not policy.allow_long:
            reasons.append("long_not_allowed")
        execution_policy = intent.resolved_execution_policy()
        protection_profile = intent.resolved_protection_profile()
        if entry and not _policy_allows(policy.allowed_execution_policies, execution_policy.identity, execution_policy.name.value):
            reasons.append("execution_policy_not_allowed")
        if entry and protection_profile is None:
            reasons.append("protection_profile_required")
        if protection_profile is not None:
            if not _policy_allows(
                policy.allowed_protection_profiles,
                protection_profile.identity,
                protection_profile.profile_id,
            ):
                reasons.append("protection_profile_not_allowed")
            if len(protection_profile.slices) > policy.maximum_protection_slices:
                reasons.append("too_many_protection_slices")
            if not policy.allow_stop_limit_protection and any(
                item.stop.order_type == StopOrderType.STOP_LIMIT
                for item in protection_profile.slices
            ):
                reasons.append("stop_limit_protection_not_allowed")
        if policy.block_on_unattributed_position and (state.profile.account_key, ticker) in self.differences and entry:
            reasons.append("unattributed_position")
        position = state.positions.get(ticker)
        current_quantity = float(position.position) if position else 0.0
        if (
            reduction
            and intent.action != "exit"
            and requested + 1e-9 < abs(current_quantity)
            and not policy.allow_partial_profit_pocket
        ):
            reasons.append("partial_profit_pocket_not_allowed")
        summary = state.summary
        if summary is None:
            reasons.append("account_summary_unavailable")
        metrics_before = self._metrics(state)
        if float(metrics_before["daily_loss"]) > policy.maximum_daily_loss:
            reasons.append("daily_loss_limit")
        if float(metrics_before["drawdown"]) > policy.maximum_drawdown:
            reasons.append("drawdown_limit")
        if reasons:
            decision = self._decision(
                intent,
                state,
                PortfolioDecisionStatus.REJECTED,
                requested,
                0.0,
                0.0,
                "",
                reasons,
                metrics_before,
                metrics_before,
                now,
            )
            return decision, None

        price = _worst_entry_price(intent) if entry else float(intent.reference_price)
        base_price = price * fx_to_base
        if requested <= 0 or price <= 0:
            decision = self._decision(
                intent,
                state,
                PortfolioDecisionStatus.REJECTED,
                requested,
                0.0,
                0.0,
                "",
                ["invalid_quantity_or_reference_price"],
                metrics_before,
                metrics_before,
                now,
            )
            return decision, None

        if reduction:
            available = (
                max(0.0, current_quantity)
                if intent.action in {"reduce_long", "take_profit", "exit"}
                else max(0.0, -current_quantity)
            )
            approved = min(requested, available)
            if approved <= 0:
                reasons.append("no_broker_position_to_reduce")
        else:
            approved, capacity_reasons = self._entry_capacity(intent, state, requested, base_price)
            reasons.extend(capacity_reasons)
        if approved <= 0:
            proposal = self._propose_rebalance(intent, state, requested, now)
            if proposal is not None:
                reasons.append("rebalance_proposed")
            decision = self._decision(
                intent,
                state,
                PortfolioDecisionStatus.REJECTED,
                requested,
                0.0,
                0.0,
                "",
                reasons or ["no_portfolio_capacity"],
                metrics_before,
                metrics_before,
                now,
            )
            return decision, None

        approved = min(approved, policy.maximum_order_quantity)
        approved = math.floor(approved * 1_000_000) / 1_000_000
        notional = approved * base_price
        planned_loss = _planned_loss(intent, approved) * fx_to_base
        decision_id = str(uuid4())
        reservation_id = str(uuid4())
        status = (
            PortfolioDecisionStatus.APPROVED
            if math.isclose(approved, requested, rel_tol=0.0, abs_tol=1e-9)
            else PortfolioDecisionStatus.RESIZED
        )
        metrics_after = dict(metrics_before)
        if entry:
            metrics_after["reserved_notional"] = float(metrics_before["reserved_notional"]) + notional
            metrics_after["gross_exposure"] = float(metrics_before["gross_exposure"]) + notional
            metrics_after["open_risk"] = float(metrics_before["open_risk"]) + planned_loss
        self._assert_active_admission_lease()
        reservation = PortfolioReservation(
            reservation_id=reservation_id,
            decision_id=decision_id,
            intent_id=intent.intent_id,
            account_key=state.profile.account_key,
            account_id=state.profile.account_id,
            strategy_id=self.strategy_id,
            assignment_id=str(intent.metadata.get("assignment_id") or ""),
            ticker=ticker,
            action=str(intent.action),
            quantity=approved,
            remaining_quantity=approved,
            reference_price=price,
            reserved_notional=notional if entry else 0.0,
            reserved_planned_risk=planned_loss if entry else 0.0,
            created_at=now,
            admission_epoch=int((self._active_admission_lease or {}).get("epoch") or 0),
            admission_owner=str((self._active_admission_lease or {}).get("owner_id") or ""),
        )
        self.reservations[reservation_id] = reservation
        decision = self._decision(
            intent,
            state,
            status,
            requested,
            approved,
            planned_loss,
            reservation_id,
            reasons,
            metrics_before,
            metrics_after,
            now,
            decision_id=decision_id,
        )
        approved_intent = replace(
            intent,
            quantity=approved,
            metadata={
                **intent.metadata,
                "portfolio_account_key": state.profile.account_key,
                "portfolio_decision_id": decision.decision_id,
                "portfolio_policy": policy.identity,
                "portfolio_reservation_id": reservation_id,
                "requested_quantity": requested,
                "portfolio_fx_to_base": fx_to_base,
                "correlation_id": _intent_correlation(self.run_id, intent),
                "causation_id": decision.decision_id,
            },
        )
        self._record(
            "portfolio_reservation",
            reservation_id,
            state.profile.account_id,
            {
                "event": "reservation_created",
                **asdict(reservation),
                "correlation_id": approved_intent.metadata["correlation_id"],
                "causation_id": decision.decision_id,
            },
        )
        self._persist_state(state)
        return decision, approved_intent

    def _assert_active_admission_lease(self) -> None:
        lease = self._active_admission_lease
        if lease is None:
            raise RuntimeError("Portfolio reservation requires a fenced admission lease")
        if not self.journal.portfolio_admission_lease_is_current(
            str(lease["resource_id"]),
            owner_id=str(lease["owner_id"]),
            epoch=int(lease["epoch"]),
        ):
            raise RuntimeError("Portfolio admission lease became stale before reservation commit")

    def _capital_request_quantity(
        self,
        intent: StrategyIntent,
        state: PortfolioAccountState,
    ) -> float:
        request = intent.capital_request
        if request is None or request.mode == "fixed_quantity":
            requested = request.value if request is not None else float(intent.quantity)
        else:
            summary = state.summary
            price = _worst_entry_price(intent)
            if summary is None or price <= 0:
                return 0.0
            if request.mode == "mandate_fraction":
                requested = float(summary.availablefunds) * request.value / price
            elif request.mode == "risk_fraction":
                risk_per_share = _risk_per_share(intent, max(float(intent.quantity), 1.0))
                requested = (
                    float(summary.netliquidation) * request.value / risk_per_share
                    if risk_per_share > 0
                    else 0.0
                )
            else:
                requested = float(summary.availablefunds) / price
        if request.maximum_quantity is not None:
            requested = min(requested, request.maximum_quantity)
        return max(request.minimum_quantity, requested)

    def _propose_rebalance(
        self,
        intent: StrategyIntent,
        state: PortfolioAccountState,
        requested: float,
        now: datetime,
    ) -> PortfolioRebalanceProposal | None:
        request = intent.capital_request
        mandate = dict(
            state.profile.strategy_mandates.get(self.allocation_identity)
            or state.profile.strategy_mandates.get(self.strategy_id)
            or {}
        )
        if (
            request is None
            or not request.allow_replacement
            or not bool(mandate.get("allow_replacement", False))
        ):
            return None
        opportunity_score = float(intent.metadata.get("opportunity_score") or 0)
        minimum_improvement = float(
            mandate.get("minimum_replacement_improvement_pct") or 0
        )
        candidates = [
            (ticker, position)
            for ticker, position in state.positions.items()
            if ticker != intent.ticker.upper()
            and abs(float(position.position)) > 0
            and any(
                allocation.account_id == state.profile.account_id
                and allocation.ticker == ticker
                for allocation in self.allocations.values()
            )
        ]
        if not candidates:
            return None
        candidate_ticker, candidate = min(
            candidates,
            key=lambda item: (
                float(item[1].unrealizedPnl)
                / max(abs(float(item[1].mktValue)), 1.0)
            ),
        )
        candidate_return_pct = (
            float(candidate.unrealizedPnl)
            / max(abs(float(candidate.mktValue)), 1.0)
            * 100
        )
        improvement_pct = (opportunity_score * 100) - candidate_return_pct
        if improvement_pct < minimum_improvement:
            return None
        proposal = PortfolioRebalanceProposal(
            proposal_id=str(uuid4()),
            request_id=intent.intent_id,
            account_key=state.profile.account_key,
            requested_ticker=intent.ticker.upper(),
            candidate_ticker=candidate_ticker,
            candidate_quantity=abs(float(candidate.position)),
            candidate_unrealized_return_pct=candidate_return_pct,
            opportunity_score=opportunity_score,
            minimum_improvement_pct=minimum_improvement,
            required_action_authority=str(
                mandate.get("maximum_action_authority")
                or mandate.get("autonomy")
                or "confirm"
            ),
            status="proposed",
            reasons=(
                "insufficient_capacity",
                "explicit_replacement_permission",
                "minimum_improvement_satisfied",
            ),
            proposed_at=now,
        )
        self.rebalance_proposals.append(proposal)
        self._record(
            "portfolio_rebalance",
            proposal.proposal_id,
            state.profile.account_id,
            {"event": "rebalance_proposed", "requested_quantity": requested, **proposal.payload()},
        )
        return proposal

    def _entry_capacity(
        self,
        intent: StrategyIntent,
        state: PortfolioAccountState,
        requested: float,
        base_price: float,
    ) -> tuple[float, list[str]]:
        policy = self._policy(state)
        summary = state.summary
        assert summary is not None
        eligible_equity = max(0.0, float(summary.netliquidation) * policy.eligible_equity_fraction)
        current_position = state.positions.get(intent.ticker.upper())
        current_value = abs(float(current_position.mktValue)) if current_position else 0.0
        reserved_notional = sum(
            row.reserved_notional
            for row in self.reservations.values()
            if row.account_id == state.profile.account_id and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        )
        reserved_risk = sum(
            row.reserved_planned_risk
            for row in self.reservations.values()
            if row.account_id == state.profile.account_id and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        )
        allocated_risk = sum(
            row.planned_risk
            for row in self.allocations.values()
            if row.account_id == state.profile.account_id
        )
        gross = sum(abs(float(row.mktValue)) for row in state.positions.values())
        net = sum(float(row.mktValue) for row in state.positions.values())
        strategy_fraction = float(
            state.profile.strategy_allocations.get(
                self.allocation_identity,
                state.profile.strategy_allocations.get(
                    self.strategy_id,
                    state.profile.strategy_allocations.get(
                        "default", policy.maximum_strategy_fraction
                    ),
                ),
            )
        )
        attributed = sum(
            abs(row.quantity * row.average_price)
            for row in self.allocations.values()
            if row.account_id == state.profile.account_id
            and row.strategy_id in {self.strategy_id, self.allocation_identity}
        )
        broker_cash_capacity = float(summary.availablefunds)
        if not policy.allow_margin and not policy.allow_unsettled_cash and state.ledger is not None:
            broker_cash_capacity = min(broker_cash_capacity, float(state.ledger.settledcash))
        available_cash = max(
            0.0,
            broker_cash_capacity * policy.maximum_buying_power_utilization
            - policy.minimum_cash_reserve
            - reserved_notional,
        )
        capacities = {
            "requested": requested,
            "order_notional": policy.maximum_order_notional / base_price,
            "available_funds": available_cash / base_price,
            "gross_exposure": max(0.0, policy.maximum_gross_exposure - gross - reserved_notional) / base_price,
            "position": max(0.0, eligible_equity * policy.maximum_position_fraction - current_value) / base_price,
            "ticker": max(0.0, eligible_equity * policy.maximum_ticker_fraction - current_value) / base_price,
            "strategy": max(0.0, eligible_equity * strategy_fraction - attributed - reserved_notional) / base_price,
        }
        if intent.action in {"enter_long", "add_long"}:
            capacities["net_exposure"] = max(0.0, policy.maximum_net_long_exposure - max(0.0, net) - reserved_notional) / base_price
        else:
            capacities["net_exposure"] = max(0.0, policy.maximum_net_short_exposure - max(0.0, -net) - reserved_notional) / base_price
        risk_per_share = _risk_per_share(intent, requested) * float(intent.metadata.get("fx_to_base") or 1.0)
        if risk_per_share > 0:
            capacities["planned_risk"] = max(
                0.0,
                eligible_equity * policy.maximum_planned_risk_fraction,
            ) / risk_per_share
            capacities["open_risk"] = max(
                0.0,
                eligible_equity * policy.maximum_open_risk_fraction
                - allocated_risk
                - reserved_risk,
            ) / risk_per_share
        open_positions = sum(1 for row in state.positions.values() if abs(float(row.position)) > 1e-12)
        if current_position is None and open_positions >= policy.maximum_open_positions:
            capacities["position_count"] = 0.0
        for dimension, fraction in (
            ("sector", policy.maximum_sector_fraction),
            ("industry", policy.maximum_industry_fraction),
            ("correlation_group", policy.maximum_correlated_group_fraction),
        ):
            value = str(intent.metadata.get(dimension) or "").strip()
            if not value:
                continue
            existing = sum(
                abs(float(row.mktValue))
                for row in state.positions.values()
                if str(row.raw.get(dimension) or "").strip() == value
            )
            capacities[dimension] = max(0.0, eligible_equity * fraction - existing) / base_price
        group_capacity = self._group_capacity(state, intent.ticker, base_price)
        if group_capacity is not None:
            capacities["portfolio_group"] = group_capacity
        approved = min(capacities.values())
        limiting = [name for name, value in capacities.items() if value <= approved + 1e-9 and name != "requested"]
        return max(0.0, approved), tuple(f"limited_by_{name}" for name in limiting)

    def _group_capacity(self, state: PortfolioAccountState, ticker: str, price: float) -> float | None:
        capacities: list[float] = []
        for group in self.groups.values():
            if state.profile.account_key not in group.account_keys:
                continue
            group_states = [self.by_key[key] for key in group.account_keys]
            gross = sum(
                abs(float(position.mktValue))
                for group_state in group_states
                for position in group_state.positions.values()
            )
            ticker_value = sum(
                abs(float(group_state.positions[ticker.upper()].mktValue))
                for group_state in group_states
                if ticker.upper() in group_state.positions
            )
            reserved = sum(
                row.reserved_notional
                for row in self.reservations.values()
                if row.account_key in group.account_keys
                and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
            )
            ticker_reserved = sum(
                row.reserved_notional
                for row in self.reservations.values()
                if row.account_key in group.account_keys
                and row.ticker == ticker.upper()
                and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
            )
            capacities.append(max(0.0, group.maximum_gross_exposure - gross - reserved) / price)
            capacities.append(max(0.0, group.maximum_ticker_exposure - ticker_value - ticker_reserved) / price)
        return min(capacities) if capacities else None

    def _decision(
        self,
        intent: StrategyIntent,
        state: PortfolioAccountState,
        status: PortfolioDecisionStatus,
        requested: float,
        approved: float,
        planned_loss: float,
        reservation_id: str,
        reasons: list[str] | tuple[str, ...],
        before: Mapping[str, float],
        after: Mapping[str, float],
        now: datetime,
        *,
        decision_id: str = "",
    ) -> PortfolioDecision:
        decision = PortfolioDecision(
            decision_id=decision_id or str(uuid4()),
            request_id=intent.intent_id,
            account_key=state.profile.account_key,
            account_id=state.profile.account_id,
            policy_id=self._policy(state).policy_id,
            policy_revision=self._policy(state).revision,
            snapshot_id=state.snapshot_id,
            status=status,
            requested_quantity=requested,
            approved_quantity=approved,
            approved_notional=(
                approved
                * float(intent.reference_price)
                * float(intent.metadata.get("fx_to_base") or 1.0)
            ),
            planned_loss=planned_loss,
            reservation_id=reservation_id,
            reasons=tuple(dict.fromkeys(reasons)),
            metrics_before=dict(before),
            metrics_after=dict(after),
            decided_at=now,
        )
        self.decisions.append(decision)
        self._record(
            "portfolio_decision",
            decision.decision_id,
            state.profile.account_id,
            {
                "event": "portfolio_decision",
                "ticker": intent.ticker,
                "action": intent.action,
                **decision.payload(),
                **causal_identity(
                    correlation_seed=(
                        self.run_id
                        or intent.metadata.get("assignment_id")
                        or intent.intent_id
                    ),
                    causation_seed=intent.intent_id,
                ),
                "correlation_id": _intent_correlation(self.run_id, intent),
                "causation_id": intent.intent_id,
            },
        )
        return decision

    def _metrics(self, state: PortfolioAccountState) -> dict[str, float]:
        summary = state.summary
        net_liquidation = float(summary.netliquidation) if summary else 0.0
        gross = sum(abs(float(row.mktValue)) for row in state.positions.values())
        net = sum(float(row.mktValue) for row in state.positions.values())
        reserved_notional = sum(
            row.reserved_notional
            for row in self.reservations.values()
            if row.account_id == state.profile.account_id
            and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        )
        open_risk = sum(
            row.reserved_planned_risk
            for row in self.reservations.values()
            if row.account_id == state.profile.account_id
            and row.status not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        )
        open_risk += sum(
            row.planned_risk
            for row in self.allocations.values()
            if row.account_id == state.profile.account_id
        )
        unrealized = sum(float(row.unrealizedPnl) for row in state.positions.values())
        daily_loss = max(0.0, -(state.realized_pnl_today + unrealized))
        drawdown = max(0.0, state.peak_net_liquidation - net_liquidation)
        return {
            "net_liquidation": net_liquidation,
            "available_funds": float(summary.availablefunds) if summary else 0.0,
            "buying_power": float(summary.buyingpower) if summary else 0.0,
            "gross_exposure": gross,
            "net_exposure": net,
            "reserved_notional": reserved_notional,
            "open_risk": open_risk,
            "daily_loss": daily_loss,
            "drawdown": drawdown,
            "position_count": float(sum(1 for row in state.positions.values() if abs(float(row.position)) > 1e-12)),
        }

    def _snapshot_stale(self, state: PortfolioAccountState, now: datetime) -> bool:
        if state.observed_at is None:
            return True
        if state.profile.mode in {"replay", "backtest", "backtest_debug"}:
            return False
        age_ms = (now - state.observed_at.astimezone(timezone.utc)).total_seconds() * 1_000
        return age_ms > self._policy(state).maximum_snapshot_age_ms

    def _reconcile_account(self, state: PortfolioAccountState) -> None:
        account_key = state.profile.account_key
        prior = {
            key: value
            for key, value in self.differences.items()
            if key[0] == account_key
        }
        existing_keys = [key for key in self.differences if key[0] == account_key]
        for key in existing_keys:
            del self.differences[key]
        attributed: dict[str, float] = {}
        for lot in self.allocations.values():
            if lot.account_id == state.profile.account_id and lot.source != "external":
                attributed[lot.ticker] = attributed.get(lot.ticker, 0.0) + lot.quantity
        for ticker in sorted(set(state.positions) | set(attributed)):
            broker_quantity = float(state.positions[ticker].position) if ticker in state.positions else 0.0
            attributed_quantity = attributed.get(ticker, 0.0)
            delta = broker_quantity - attributed_quantity
            if abs(delta) <= 1e-9:
                continue
            self.differences[(account_key, ticker)] = PortfolioReconciliationDifference(
                account_key=account_key,
                ticker=ticker,
                broker_quantity=broker_quantity,
                attributed_quantity=attributed_quantity,
                unattributed_quantity=delta,
                observed_at=state.observed_at or datetime.now(timezone.utc),
            )
        current = {
            key: value
            for key, value in self.differences.items()
            if key[0] == account_key
        }
        if _reconciliation_signature(prior) != _reconciliation_signature(current):
            rows = [asdict(current[key]) for key in sorted(current)]
            self._record(
                "portfolio_reconciliation",
                account_key,
                state.profile.account_id,
                {
                    "event": "portfolio_reconciliation_completed",
                    "snapshot_id": state.snapshot_id,
                    "difference_count": len(rows),
                    "differences": rows,
                },
            )

    def _apply_fill(
        self,
        reservation: PortfolioReservation,
        quantity: float,
        *,
        action: str | None = None,
    ) -> None:
        effective_action = action or reservation.action
        signed = quantity
        if effective_action in {"enter_short", "add_short", "reduce_long", "take_profit", "exit"}:
            signed = -quantity
        allocation_id = (
            f"{reservation.account_id}:{reservation.strategy_id}:"
            f"{reservation.assignment_id or reservation.ticker}:{reservation.ticker}"
        )
        current = self.allocations.get(allocation_id)
        previous_quantity = current.quantity if current else 0.0
        previous_risk = current.planned_risk if current else 0.0
        incremental_risk = (
            reservation.reserved_planned_risk * quantity / reservation.quantity
            if reservation.quantity > 0
            else 0.0
        )
        if effective_action in REDUCTION_ACTIONS:
            if previous_quantity > 0:
                signed = -min(abs(signed), previous_quantity)
            elif previous_quantity < 0:
                signed = min(abs(signed), abs(previous_quantity))
            else:
                signed = 0.0
            risk_reduction_fraction = (
                min(1.0, abs(signed) / abs(previous_quantity))
                if abs(previous_quantity) > 1e-12
                else 0.0
            )
            next_risk = max(0.0, previous_risk * (1.0 - risk_reduction_fraction))
        else:
            next_risk = previous_risk + incremental_risk
        next_quantity = previous_quantity + signed
        if abs(next_quantity) <= 1e-9:
            self.allocations.pop(allocation_id, None)
        else:
            average_price = reservation.reference_price
            if current is not None and abs(previous_quantity) > 1e-12 and signed * previous_quantity > 0:
                average_price = (
                    abs(previous_quantity) * current.average_price
                    + abs(signed) * reservation.reference_price
                ) / abs(next_quantity)
            self.allocations[allocation_id] = PortfolioAllocationLot(
                allocation_id=allocation_id,
                account_key=reservation.account_key,
                account_id=reservation.account_id,
                strategy_id=reservation.strategy_id,
                strategy_revision=self.strategy_revision,
                assignment_id=reservation.assignment_id,
                ticker=reservation.ticker,
                quantity=next_quantity,
                average_price=average_price,
                planned_risk=next_risk,
                realized_pnl=current.realized_pnl if current else 0.0,
                source="managed",
                updated_at=datetime.now(timezone.utc),
            )
        self._record(
            "portfolio_allocation",
            allocation_id,
            reservation.account_id,
            {
                "event": "allocation_fill_applied",
                "incremental_quantity": signed,
                "quantity": next_quantity,
                "ticker": reservation.ticker,
                "action": effective_action,
                "strategy_id": reservation.strategy_id,
                "assignment_id": reservation.assignment_id,
            },
        )

    def _state(self, account_id: str) -> PortfolioAccountState:
        state = self.states.get(account_id)
        if state is None:
            raise KeyError(f"Unknown portfolio account: {account_id}")
        return state

    def _record(self, entity_type: str, entity_id: str, account_id: str, payload: dict[str, Any]) -> None:
        self.journal.append(
            run_id=self.run_id,
            category="portfolio_management",
            entity_type=entity_type,
            entity_id=entity_id,
            account_id=account_id,
            payload=payload,
        )

    def _persist_state(self, state: PortfolioAccountState) -> None:
        self.journal.save_portfolio_state(
            state.profile.account_id,
            {
                "account_key": state.profile.account_key,
                "control_mode": state.control_mode.value,
                "sync_state": state.sync_state.value,
                "snapshot_id": state.snapshot_id,
                "observed_at": state.observed_at,
                "stale_reason": state.stale_reason,
                "peak_net_liquidation": state.peak_net_liquidation,
                "realized_pnl_baseline": state.realized_pnl_baseline,
                "selected_policy": _policy_payload(state.policy_override) if state.policy_override else None,
                "disabled_strategy_allocations": sorted(state.disabled_strategy_allocations),
                "pending_operational_commands": list(
                    state.pending_operational_commands
                ),
                "reservations": [
                    asdict(row)
                    for row in self.reservations.values()
                    if row.account_id == state.profile.account_id
                ],
                "allocations": [
                    asdict(row)
                    for row in self.allocations.values()
                    if row.account_id == state.profile.account_id
                ],
                "reconciliation": [
                    asdict(row)
                    for row in self.differences.values()
                    if row.account_key == state.profile.account_key
                ],
            },
        )

    def _restore(self) -> None:
        for account_id, payload in self.journal.portfolio_states().items():
            state = self.states.get(account_id)
            if state is None:
                continue
            try:
                state.control_mode = PortfolioControlMode(payload.get("control_mode") or PortfolioControlMode.ENABLED)
                state.peak_net_liquidation = float(payload.get("peak_net_liquidation") or 0.0)
                state.realized_pnl_baseline = (
                    float(payload["realized_pnl_baseline"])
                    if payload.get("realized_pnl_baseline") is not None
                    else None
                )
                selected_policy = payload.get("selected_policy")
                if isinstance(selected_policy, dict):
                    state.policy_override = narrow_policy_for_account_class(
                        portfolio_policy_from_payload(selected_policy),
                        state.profile.account_class,
                    )
                state.disabled_strategy_allocations = {
                    str(item) for item in payload.get("disabled_strategy_allocations") or ()
                }
                state.pending_operational_commands = [
                    dict(item)
                    for item in payload.get("pending_operational_commands") or ()
                    if isinstance(item, Mapping)
                ][-100:]
                for raw in payload.get("reservations") or []:
                    reservation = PortfolioReservation(
                        **{
                            **raw,
                            "created_at": _timestamp(raw.get("created_at")),
                        }
                    )
                    self.reservations[reservation.reservation_id] = reservation
                    self._last_filled_by_reservation[reservation.reservation_id] = reservation.filled_quantity
                for raw in payload.get("allocations") or []:
                    raw = {**raw, "planned_risk": float(raw.get("planned_risk") or 0)}
                    allocation = PortfolioAllocationLot(
                        **{
                            **raw,
                            "updated_at": _timestamp(raw.get("updated_at")),
                        }
                    )
                    self.allocations[allocation.allocation_id] = allocation
                for raw in payload.get("reconciliation") or []:
                    difference = PortfolioReconciliationDifference(
                        **{
                            **raw,
                            "observed_at": _timestamp(raw.get("observed_at")),
                        }
                    )
                    self.differences[(difference.account_key, difference.ticker)] = difference
            except (TypeError, ValueError):
                state.control_mode = PortfolioControlMode.ENTRIES_PAUSED
                state.stale_reason = "Persisted portfolio state is invalid; entries are paused."

    def _update_realized_pnl(self, state: PortfolioAccountState, current: float) -> None:
        if state.realized_pnl_baseline is None:
            state.realized_pnl_baseline = current
        state.realized_pnl_today = current - state.realized_pnl_baseline

    def _policy(self, state: PortfolioAccountState) -> PortfolioPolicy:
        return state.policy_override or state.profile.policy

    def _refresh_operational_state(self, state: PortfolioAccountState) -> None:
        payload = self.journal.portfolio_states().get(state.profile.account_id) or {}
        try:
            state.control_mode = PortfolioControlMode(
                payload.get("control_mode") or state.control_mode
            )
            state.disabled_strategy_allocations = {
                str(item) for item in payload.get("disabled_strategy_allocations") or ()
            }
            if isinstance(payload.get("selected_policy"), dict):
                state.policy_override = narrow_policy_for_account_class(
                    portfolio_policy_from_payload(payload["selected_policy"]),
                    state.profile.account_class,
                )
        except (TypeError, ValueError):
            state.control_mode = PortfolioControlMode.ENTRIES_PAUSED
            state.stale_reason = "Operational portfolio controls are invalid; entries are paused."


def _reconciliation_signature(
    rows: Mapping[tuple[str, str], PortfolioReconciliationDifference],
) -> tuple[tuple[str, str, float, float, float], ...]:
    return tuple(
        (
            row.account_key,
            row.ticker,
            row.broker_quantity,
            row.attributed_quantity,
            row.unattributed_quantity,
        )
        for row in sorted(rows.values(), key=lambda value: (value.account_key, value.ticker))
    )


def profiles_for_runtime(
    account_ids: tuple[str, ...] | list[str],
    *,
    mode: str,
    account_classes: Mapping[str, str] | None = None,
) -> tuple[PortfolioAccountProfile, ...]:
    classes = dict(account_classes or {})
    return tuple(
        PortfolioAccountProfile(
            account_key=account_id,
            account_id=account_id,
            mode=mode,
            account_class=classes.get(account_id, "simulated" if mode in {"replay", "backtest", "backtest_debug"} else "default"),
            policy=default_policy_for_account(classes.get(account_id, "default")),
        )
        for account_id in account_ids
    )


def _policy_payload(policy: PortfolioPolicy) -> dict[str, Any]:
    return {**asdict(policy), "identity": policy.identity}


def portfolio_policy_from_payload(payload: Mapping[str, Any]) -> PortfolioPolicy:
    valid = set(PortfolioPolicy.__dataclass_fields__)
    normalized = {key: value for key, value in payload.items() if key in valid}
    for tuple_field in (
        "allowed_security_types",
        "allowed_currencies",
        "restricted_symbols",
        "allowed_execution_policies",
        "allowed_protection_profiles",
    ):
        if tuple_field in normalized:
            normalized[tuple_field] = tuple(str(item) for item in normalized[tuple_field])
    return PortfolioPolicy(**normalized)


def narrow_policy_for_account_class(
    policy: PortfolioPolicy,
    account_class: str,
) -> PortfolioPolicy:
    if account_class.strip().lower() in {"cash", "rrsp", "registered", "tfsa"}:
        return replace(
            policy,
            allow_margin=False,
            allow_short=False,
            maximum_net_short_exposure=0.0,
        )
    return policy


def _intent_correlation(run_id: str, intent: StrategyIntent) -> str:
    inherited = normalize_request_identity(
        str(intent.metadata.get("correlation_id") or "")
    )
    if inherited:
        return inherited
    return causal_identity(
        correlation_seed=(
            run_id or intent.metadata.get("assignment_id") or intent.intent_id
        ),
        causation_seed=intent.intent_id,
    )["correlation_id"]


def _planned_loss(intent: StrategyIntent, quantity: float) -> float:
    profile = intent.resolved_protection_profile()
    if profile is None:
        return 0.0
    entry_price = _worst_entry_price(intent)
    position_side = "short" if intent.action in {"enter_short", "add_short"} else "long"
    volatility = float(intent.metadata.get("volatility") or 0)
    planned = 0.0
    for item in profile.slices:
        slice_quantity = quantity * item.quantity_fraction
        if (
            intent.action in {"add_long", "add_short"}
            and profile.add_policy == AddProtectionPolicy.INHERIT_POSITION_STOP
        ):
            stop_price = float(intent.metadata.get("position_stop_price") or 0)
            if stop_price <= 0:
                raise ValueError(
                    "inherit_position_stop add policy requires position_stop_price"
                )
            if (position_side == "long" and stop_price >= entry_price) or (
                position_side == "short" and stop_price <= entry_price
            ):
                raise ValueError("inherited position stop is on the wrong side of the add")
        else:
            stop_price = item.stop.resolve(
                reference_price=entry_price,
                side=position_side,
                quantity=slice_quantity,
                volatility=volatility,
            )
        planned += abs(entry_price - stop_price) * slice_quantity
    return planned


def _risk_per_share(intent: StrategyIntent, quantity: float = 1.0) -> float:
    return _planned_loss(intent, max(quantity, 1e-12)) / max(quantity, 1e-12)


def _worst_entry_price(intent: StrategyIntent) -> float:
    reference = float(intent.reference_price)
    envelope = intent.resolved_execution_policy().envelope
    if intent.action in {"enter_long", "add_long"}:
        return float(envelope.maximum_buy_price or reference)
    if intent.action in {"enter_short", "add_short"}:
        return float(envelope.minimum_sell_price or reference)
    return reference


def _policy_allows(allowed: tuple[str, ...], identity: str, name: str) -> bool:
    return "*" in allowed or identity in allowed or name in allowed


def _ticker(position: PortfolioPosition) -> str:
    ticker = str(position.raw.get("ticker") or position.raw.get("symbol") or position.contractDesc).strip().upper()
    return ticker or str(position.conid)


def _terminal_order(order: LiveOrder) -> bool:
    return str(order.order_status).lower() in {"filled", "cancelled", "inactive", "rejected"}


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed.astimezone(timezone.utc)


def _canonical_amount(values: list[Any], ledgers: list[Any], *keys: str) -> float:
    wanted = {key.lower() for key in keys}
    for row in values:
        if str(row.key).lower() in wanted and row.segment == "base":
            return float(row.monetary_value if row.monetary_value is not None else row.value or 0)
    for row in ledgers:
        if not row.is_base:
            continue
        for key, value in row.values.items():
            if str(key).lower() in wanted:
                return float(value or 0)
    return 0.0


def _oldest_timestamp(rows: list[Any], fallback: datetime) -> datetime:
    timestamps = [
        getattr(row, "source_event_time", None) or getattr(row, "received_at", None)
        for row in rows
    ]
    valid = [row.astimezone(timezone.utc) for row in timestamps if isinstance(row, datetime)]
    return min(valid) if valid else fallback.astimezone(timezone.utc)
