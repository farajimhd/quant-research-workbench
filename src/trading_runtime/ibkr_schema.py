from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class OrderStatus(StrEnum):
    """IBKR Client Portal order statuses, preserved without app-local aliases."""

    INACTIVE = "Inactive"
    PENDING_SUBMIT = "PendingSubmit"
    PRE_SUBMITTED = "PreSubmitted"
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    PENDING_CANCEL = "PendingCancel"
    PRE_CANCELLED = "PreCancelled"
    CANCELLED = "Cancelled"
    WARN_STATE = "WarnState"


OPEN_ORDER_STATUSES = {
    OrderStatus.INACTIVE,
    OrderStatus.PENDING_SUBMIT,
    OrderStatus.PRE_SUBMITTED,
    OrderStatus.SUBMITTED,
    OrderStatus.PENDING_CANCEL,
    OrderStatus.PRE_CANCELLED,
    OrderStatus.WARN_STATE,
}


SUPPORTED_ORDER_TYPES = {"MKT", "LMT", "STP", "STOP_LIMIT", "MIDPRICE", "TRAIL", "TRAILLMT"}
SUPPORTED_TIF = {"DAY", "GTC", "IOC", "OPG"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Canonical order command using the CPAPI place/modify request field names.

    Fields deliberately retain IBKR spelling (acctId, cOID, orderType, etc.).
    A strategy can therefore emit the same command for simulated, paper, and
    live brokers without a mode-specific translation layer.
    """

    acctId: str
    conid: int
    orderType: str
    side: str
    quantity: float | None = None
    cashQty: float | None = None
    secType: str = "STK"
    cOID: str = ""
    parentId: str | None = None
    ticker: str = ""
    tif: str = "DAY"
    outsideRTH: bool = False
    price: float | None = None
    auxPrice: float | None = None
    trailingAmt: float | None = None
    trailingType: str | None = None
    listingExchange: str = "SMART"
    isSingleGroup: bool = False
    manualIndicator: bool = False
    extOperator: str | None = None
    referrer: str | None = None
    strategy: str | None = None
    strategyParameters: tuple[dict[str, Any], ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        order_type = self.orderType.upper()
        side = self.side.upper()
        tif = self.tif.upper()
        if not self.acctId.strip():
            raise ValueError("acctId is required")
        if self.conid <= 0:
            raise ValueError("conid must be positive")
        if order_type not in SUPPORTED_ORDER_TYPES:
            raise ValueError(f"Unsupported IBKR orderType: {self.orderType}")
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if tif not in SUPPORTED_TIF:
            raise ValueError(f"Unsupported IBKR tif: {self.tif}")
        if (self.quantity is None) == (self.cashQty is None):
            raise ValueError("Specify exactly one of quantity or cashQty")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.cashQty is not None and self.cashQty <= 0:
            raise ValueError("cashQty must be positive")
        if order_type in {"LMT", "STOP_LIMIT", "TRAILLMT"} and (self.price is None or self.price <= 0):
            raise ValueError(f"{order_type} requires a positive price")
        if order_type in {"STP", "STOP_LIMIT"} and (self.auxPrice is None or self.auxPrice <= 0):
            raise ValueError(f"{order_type} requires a positive auxPrice")
        if self.cOID and len(self.cOID) > 64:
            raise ValueError("cOID cannot exceed 64 characters")

    @classmethod
    def from_cpapi(cls, payload: dict[str, Any], *, account_id: str | None = None) -> "OrderRequest":
        known = {field.name for field in cls.__dataclass_fields__.values() if field.name != "raw"}
        values = {key: payload[key] for key in known if key in payload}
        values["acctId"] = str(values.get("acctId") or account_id or "")
        if "strategyParameters" in values:
            values["strategyParameters"] = tuple(values["strategyParameters"] or ())
        values["raw"] = {key: value for key, value in payload.items() if key not in known}
        return cls(**values)

    def to_cpapi(self, *, include_account: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        raw = payload.pop("raw", {})
        payload.update(raw)
        payload["strategyParameters"] = list(self.strategyParameters)
        if not include_account:
            payload.pop("acctId", None)
        return {key: value for key, value in payload.items() if value not in (None, "", (), [])}


@dataclass(frozen=True, slots=True)
class LiveOrder:
    account: str
    orderId: str
    conid: int
    ticker: str
    side: str
    orderType: str
    tif: str
    totalSize: float
    filledQuantity: float
    remainingQuantity: float
    avgPrice: float
    order_status: OrderStatus
    cOID: str = ""
    parentId: str | None = None
    price: float | None = None
    auxPrice: float | None = None
    outsideRTH: bool = False
    lastExecutionTime: datetime | None = None
    statusDescription: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_cpapi(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["order_status"] = self.order_status.value
        if self.lastExecutionTime is not None:
            payload["lastExecutionTime"] = self.lastExecutionTime.isoformat()
        raw = payload.pop("raw", {})
        payload.update(raw)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    symbol: str
    side: str
    order_ref: str
    trade_time: datetime
    trade_time_r: int
    size: float
    price: float
    order_id: str
    account: str
    conid: int
    commission: float = 0.0
    currency: str = "USD"
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_cpapi(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_time"] = self.trade_time.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        raw = payload.pop("raw", {})
        payload.update(raw)
        return payload


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    acctId: str
    conid: int
    contractDesc: str
    position: float
    mktPrice: float
    mktValue: float
    avgCost: float
    avgPrice: float
    realizedPnl: float
    unrealizedPnl: float
    currency: str = "USD"
    assetClass: str = "STK"
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_cpapi(self) -> dict[str, Any]:
        payload = asdict(self)
        raw = payload.pop("raw", {})
        payload.update(raw)
        return payload


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account_id: str
    netliquidation: float
    totalcashvalue: float
    buyingpower: float
    grosspositionvalue: float
    availablefunds: float
    excessliquidity: float
    currency: str = "USD"
    timestamp: datetime = field(default_factory=utc_now)

    def to_cpapi(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for key in (
            "netliquidation",
            "totalcashvalue",
            "buyingpower",
            "grosspositionvalue",
            "availablefunds",
            "excessliquidity",
        ):
            result[key] = {"amount": getattr(self, key), "currency": self.currency, "timestamp": int(self.timestamp.timestamp() * 1000)}
        return result


@dataclass(frozen=True, slots=True)
class AccountLedger:
    acctId: str
    cashbalance: float
    settledcash: float
    stockmarketvalue: float
    netliquidationvalue: float
    realizedpnl: float
    unrealizedpnl: float
    currency: str = "USD"
    exchangerate: float = 1.0
    timestamp: datetime = field(default_factory=utc_now)

    def to_cpapi(self) -> dict[str, dict[str, Any]]:
        return {
            self.currency: {
                "cashbalance": self.cashbalance,
                "settledcash": self.settledcash,
                "stockmarketvalue": self.stockmarketvalue,
                "netliquidationvalue": self.netliquidationvalue,
                "realizedpnl": self.realizedpnl,
                "unrealizedpnl": self.unrealizedpnl,
                "exchangerate": self.exchangerate,
                "timestamp": int(self.timestamp.timestamp() * 1000),
            }
        }
