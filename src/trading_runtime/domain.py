from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Iterable
from uuid import uuid4


class TradingMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    REPLAY = "replay"
    BACKTEST = "backtest"
    BACKTEST_DEBUG = "backtest_debug"


class BrokerProvider(StrEnum):
    IBKR_CPAPI = "ibkr_cpapi"
    IBKR_TWS = "ibkr_tws"
    SIMULATED = "simulated"


class OrderLifecycleState(StrEnum):
    CREATED = "created"
    PENDING_SUBMISSION = "pending_submission"
    WORKING = "working"
    TRIGGER_PENDING = "trigger_pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


TERMINAL_ORDER_STATES = {
    OrderLifecycleState.FILLED,
    OrderLifecycleState.CANCELLED,
    OrderLifecycleState.REJECTED,
    OrderLifecycleState.EXPIRED,
}


class BrokerEventType(StrEnum):
    ORDER_COMMAND = "order_command"
    ORDER_ACKNOWLEDGED = "order_acknowledged"
    ORDER_STATUS_CHANGED = "order_status_changed"
    ORDER_WARNING = "order_warning"
    ORDER_REJECTED = "order_rejected"
    EXECUTION_REPORTED = "execution_reported"
    COMMISSION_REPORTED = "commission_reported"
    POSITION_SNAPSHOT_STARTED = "position_snapshot_started"
    POSITION_OBSERVED = "position_observed"
    POSITION_SNAPSHOT_COMPLETED = "position_snapshot_completed"
    ACCOUNT_VALUE_CHANGED = "account_value_changed"
    LEDGER_VALUE_CHANGED = "ledger_value_changed"
    CONNECTION_CHANGED = "connection_changed"
    RECONCILIATION_COMPLETED = "reconciliation_completed"


def utc_now() -> datetime:
    return datetime.now(UTC)


def decimal_value(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class InstrumentContract:
    instrument_id: str
    conid: int
    symbol: str
    security_type: str
    currency: str
    exchange: str = "SMART"
    primary_exchange: str = ""
    local_symbol: str = ""
    trading_class: str = ""
    multiplier: Decimal = Decimal("1")
    expiry: str = ""
    strike: Decimal | None = None
    right: str = ""
    provider_ids: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    provider: BrokerProvider
    account_id: str
    base_currency: str
    account_type: str = ""
    alias: str = ""
    title: str = ""
    parent_account_id: str = ""
    can_view: bool = True
    can_trade: bool = False
    valid_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    command_id: str
    account_id: str
    instrument: InstrumentContract
    client_order_id: str
    side: str
    order_type: str
    time_in_force: str
    quantity: Decimal | None = None
    cash_quantity: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trailing_amount: Decimal | None = None
    trailing_type: str = ""
    outside_rth: bool = False
    parent_command_id: str = ""
    oca_group: str = ""
    strategy_id: str = ""
    strategy_revision: int = 0
    run_id: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not self.command_id or not self.account_id or not self.client_order_id:
            raise ValueError("command_id, account_id, and client_order_id are required")
        if (self.quantity is None) == (self.cash_quantity is None):
            raise ValueError("Specify exactly one of quantity or cash_quantity")


@dataclass(frozen=True, slots=True)
class OrderState:
    account_id: str
    instrument: InstrumentContract
    lifecycle_state: OrderLifecycleState
    broker_status_raw: str
    broker_order_id: str = ""
    client_order_id: str = ""
    command_id: str = ""
    parent_order_id: str = ""
    side: str = ""
    order_type: str = ""
    time_in_force: str = ""
    total_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal = Decimal("0")
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    outside_rth: bool = False
    can_modify: bool = False
    can_cancel: bool = False
    terminal: bool = False
    warning: str = ""
    rejection_code: str = ""
    rejection_reason: str = ""
    source_event_time: datetime = field(default_factory=utc_now)
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    account_id: str
    instrument: InstrumentContract
    side: str
    quantity: Decimal
    price: Decimal
    source_event_time: datetime
    broker_order_id: str = ""
    client_order_id: str = ""
    exchange: str = ""
    commission: Decimal | None = None
    commission_currency: str = ""
    commission_status: str = "pending"
    net_amount: Decimal | None = None
    cumulative_quantity: Decimal | None = None
    average_price: Decimal | None = None
    liquidity: str = ""
    liquidation_trade: bool = False
    strategy_id: str = ""
    strategy_revision: int = 0
    run_id: str = ""
    setup: str = ""
    exit_reason: str = ""
    signal_price: Decimal | None = None
    arrival_midpoint: Decimal | None = None
    planned_risk: Decimal | None = None
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class CommissionEvent:
    execution_id: str
    account_id: str
    commission: Decimal
    currency: str
    realized_pnl: Decimal | None = None
    source_event_time: datetime = field(default_factory=utc_now)
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class PositionState:
    snapshot_id: str
    account_id: str
    instrument: InstrumentContract
    quantity: Decimal
    market_price: Decimal
    market_value: Decimal
    average_cost: Decimal
    average_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    model: str = ""
    is_last_to_liquidate: bool = False
    source_event_time: datetime = field(default_factory=utc_now)
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AccountValue:
    account_id: str
    key: str
    value: str
    monetary_value: Decimal | None
    currency: str
    segment: str = "base"
    is_null: bool = False
    severity: int = 0
    source_event_time: datetime = field(default_factory=utc_now)
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class LedgerBalance:
    account_id: str
    currency: str
    values: dict[str, Decimal | str | bool]
    is_base: bool = False
    source_event_time: datetime = field(default_factory=utc_now)
    received_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    snapshot_id: str
    entity_kind: str
    account_id: str
    provider: BrokerProvider
    started_at: datetime
    completed_at: datetime | None
    complete: bool
    expected_pages: int | None = None
    received_pages: int = 0
    item_count: int = 0
    source_watermark: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class BrokerEventEnvelope:
    event_id: str
    event_type: BrokerEventType
    provider: BrokerProvider
    mode: TradingMode
    account_id: str
    source_event_time: datetime
    received_at: datetime
    recorded_at: datetime
    payload: dict[str, Any]
    schema_version: int = 2
    run_id: str = ""
    broker_session_id: str = ""
    broker_event_id: str = ""
    command_id: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    execution_id: str = ""
    source_sequence: int = 0
    payload_hash: str = ""

    @classmethod
    def create(
        cls,
        *,
        event_type: BrokerEventType,
        provider: BrokerProvider,
        mode: TradingMode,
        account_id: str,
        payload: dict[str, Any],
        source_event_time: datetime | None = None,
        **identity: Any,
    ) -> "BrokerEventEnvelope":
        now = utc_now()
        event_id = str(identity.pop("event_id", "") or uuid4())
        safe_payload = json_safe(payload)
        encoded = json.dumps(safe_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return cls(
            event_id=event_id,
            event_type=event_type,
            provider=provider,
            mode=mode,
            account_id=account_id,
            source_event_time=source_event_time or now,
            received_at=now,
            recorded_at=now,
            payload=safe_payload,
            payload_hash=hashlib.sha256(encoded).hexdigest(),
            **identity,
        )


@dataclass(frozen=True, slots=True)
class RoundTripTrade:
    trade_id: str
    account_id: str
    instrument: InstrumentContract
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    side: str
    strategy_id: str = ""
    strategy_revision: int = 0
    run_id: str = ""
    setup: str = ""
    exit_reason: str = ""
    mae: Decimal | None = None
    mfe: Decimal | None = None
    planned_risk: Decimal | None = None
    execution_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeEpisode:
    """One strategy position lifecycle from flat to flat.

    Episodes are the performance-reporting unit. They remain separate from
    FIFO realization fragments and immutable broker executions.
    """

    episode_id: str
    account_id: str
    instrument: InstrumentContract
    opened_at: datetime
    closed_at: datetime
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    strategy_id: str = ""
    strategy_revision: int = 0
    run_id: str = ""
    setup: str = ""
    exit_reason: str = ""
    mae: Decimal | None = None
    mfe: Decimal | None = None
    planned_risk: Decimal | None = None
    execution_ids: tuple[str, ...] = ()
    order_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TradingStateSnapshot:
    schema_version: int
    mode: TradingMode
    provider: BrokerProvider
    as_of: datetime
    account_ids: tuple[str, ...]
    complete: bool
    stale: bool
    stale_reason: str
    accounts: tuple[BrokerAccount, ...]
    account_values: tuple[AccountValue, ...]
    ledger: tuple[LedgerBalance, ...]
    positions: tuple[PositionState, ...]
    orders: tuple[OrderState, ...]
    executions: tuple[Execution, ...]
    closed_trades: tuple[RoundTripTrade, ...] = ()
    activity: tuple[BrokerEventEnvelope, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


def serialize_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [json_safe(asdict(row)) for row in rows]
