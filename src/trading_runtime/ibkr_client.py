from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.market_engine.events import MarketEvent
from src.trading_runtime.domain import AccountValue as CanonicalAccountValue
from src.trading_runtime.domain import BrokerAccount as CanonicalBrokerAccount
from src.trading_runtime.domain import Execution as CanonicalExecution
from src.trading_runtime.domain import LedgerBalance as CanonicalLedgerBalance
from src.trading_runtime.domain import OrderState as CanonicalOrderState
from src.trading_runtime.domain import PositionState as CanonicalPositionState
from src.trading_runtime.domain import SnapshotManifest
from src.trading_runtime.domain import BrokerEventEnvelope, BrokerEventType, BrokerProvider, OrderIntent, TradingMode
from src.trading_runtime.canonical_commands import intent_to_ibkr_request, lifecycle_event, response_events
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary, Execution, LiveOrder, OrderRequest, OrderStatus, PortfolioPosition
from src.trading_runtime.ibkr_normalizer import (
    normalize_account_values,
    normalize_accounts,
    normalize_execution,
    normalize_ledger,
    normalize_order,
    normalize_position_snapshot,
)


class IbkrClientPortalAdapter:
    """Live/paper broker adapter for Client Portal Web API v1.

    IBKR permits only one unresolved order-warning chain at a time. All order
    commands and `/iserver/reply/{id}` acknowledgements therefore share one
    lock, preventing a later command from implicitly cancelling an earlier
    warning response.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        verify_tls: bool = False,
        auto_confirm_warnings: bool = False,
        mode: TradingMode = TradingMode.PAPER,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.auto_confirm_warnings = auto_confirm_warnings
        self.mode = mode
        self._order_lane = asyncio.Lock()
        self._account_ids: list[str] = []
        self._portfolio_accounts_payload: Any = []
        self._trading_accounts_payload: Any = []

    async def initialize(self) -> None:
        status = await self._request("GET", "/iserver/auth/status")
        if not isinstance(status, dict) or not status.get("authenticated"):
            raise RuntimeError("IBKR Client Portal brokerage session is not authenticated")
        self._portfolio_accounts_payload = await self._request("GET", "/portfolio/accounts")
        self._trading_accounts_payload = await self._request("GET", "/iserver/accounts")
        self._account_ids = _account_ids(self._trading_accounts_payload)
        if not self._account_ids:
            raise RuntimeError("IBKR /iserver/accounts returned no tradeable accounts")

    async def accounts(self) -> list[str]:
        if not self._account_ids:
            self._account_ids = _account_ids(await self._request("GET", "/iserver/accounts"))
        return list(self._account_ids)

    async def canonical_accounts(self) -> list[CanonicalBrokerAccount]:
        """Return view and trading permissions without conflating the two IBKR account lists."""
        if not self._portfolio_accounts_payload:
            self._portfolio_accounts_payload = await self._request("GET", "/portfolio/accounts")
        if not self._trading_accounts_payload:
            self._trading_accounts_payload = await self._request("GET", "/iserver/accounts")
        return normalize_accounts(self._portfolio_accounts_payload, self._trading_accounts_payload)

    async def preview_orders(self, account_id: str, orders: list[OrderRequest]) -> list[dict[str, Any]]:
        self._require_account_orders(account_id, orders)
        conids = sorted({order.conid for order in orders})
        await self._request("GET", f"/iserver/marketdata/snapshot?conids={','.join(map(str, conids))}&fields=31,84,86")
        async with self._order_lane:
            response = await self._request(
                "POST",
                f"/iserver/account/{_quote(account_id)}/orders/whatif",
                {"orders": [order.to_cpapi() for order in orders]},
            )
            return _rows(response)

    async def place_orders(self, account_id: str, orders: list[OrderRequest]) -> list[dict[str, Any]]:
        self._require_account_orders(account_id, orders)
        async with self._order_lane:
            response = await self._request(
                "POST",
                f"/iserver/account/{_quote(account_id)}/orders",
                {"orders": [order.to_cpapi() for order in orders]},
            )
            if self.auto_confirm_warnings:
                response = await self._confirm_warning_chain_locked(response)
            return _rows(response)

    async def submit_intents(self, account_id: str, intents: list[OrderIntent]) -> list[BrokerEventEnvelope]:
        if not intents:
            raise ValueError("intents cannot be empty")
        if any(intent.account_id != account_id for intent in intents):
            raise ValueError("Every canonical intent must match the path account")
        rows = await self.place_orders(account_id, [intent_to_ibkr_request(intent) for intent in intents])
        if len(intents) == 1:
            return response_events(intents[0], rows, BrokerProvider.IBKR_CPAPI, self.mode)
        events: list[BrokerEventEnvelope] = []
        for index, intent in enumerate(intents):
            matched = [rows[index]] if index < len(rows) else []
            events.extend(response_events(intent, matched, BrokerProvider.IBKR_CPAPI, self.mode))
        return events

    async def reply(self, reply_id: str, confirmed: bool) -> list[dict[str, Any]]:
        async with self._order_lane:
            return _rows(await self._request("POST", f"/iserver/reply/{_quote(reply_id)}", {"confirmed": confirmed}))

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest) -> list[dict[str, Any]]:
        self._require_account_orders(account_id, [order])
        async with self._order_lane:
            response = await self._request(
                "POST",
                f"/iserver/account/{_quote(account_id)}/order/{_quote(order_id)}",
                order.to_cpapi(),
            )
            if self.auto_confirm_warnings:
                response = await self._confirm_warning_chain_locked(response)
            return _rows(response)

    async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
        self._require_known_account(account_id)
        async with self._order_lane:
            response = await self._request("DELETE", f"/iserver/account/{_quote(account_id)}/order/{_quote(order_id)}")
            if self.auto_confirm_warnings:
                response = await self._confirm_warning_chain_locked(response)
            rows = _rows(response)
            return rows[-1] if rows else {}

    async def cancel(self, account_id: str, broker_order_id: str) -> list[BrokerEventEnvelope]:
        payload = await self.cancel_order(account_id, broker_order_id)
        return [lifecycle_event(event_type=BrokerEventType.ORDER_STATUS_CHANGED, account_id=account_id, broker_order_id=broker_order_id, payload=payload, provider=BrokerProvider.IBKR_CPAPI, mode=self.mode)]

    async def replace(self, account_id: str, broker_order_id: str, intent: OrderIntent) -> list[BrokerEventEnvelope]:
        rows = await self.modify_order(account_id, broker_order_id, intent_to_ibkr_request(intent))
        return response_events(intent, rows, BrokerProvider.IBKR_CPAPI, self.mode)

    async def live_orders(self) -> list[LiveOrder]:
        payload = await self._request("GET", "/iserver/account/orders")
        raw_orders = payload.get("orders", []) if isinstance(payload, dict) else _rows(payload)
        return [_live_order(row) for row in raw_orders if isinstance(row, dict)]

    async def canonical_orders(self, account_id: str = "") -> list[CanonicalOrderState]:
        if account_id:
            self._require_known_account(account_id)
            await self._request("POST", "/iserver/account", {"acctId": account_id})
        payload = await self._request("GET", "/iserver/account/orders")
        raw_orders = payload.get("orders", []) if isinstance(payload, dict) else _rows(payload)
        rows = [normalize_order(row, account_id) for row in raw_orders if isinstance(row, dict)]
        return [row for row in rows if not account_id or row.account_id == account_id]

    async def trades(self, days: int = 7) -> list[Execution]:
        if not 1 <= days <= 7:
            raise ValueError("IBKR trade history supports 1 through 7 days")
        payload = await self._request("GET", f"/iserver/account/trades?days={days}")
        return [_execution(row) for row in _rows(payload)]

    async def canonical_executions(self, account_id: str = "", days: int = 7) -> list[CanonicalExecution]:
        if not 1 <= days <= 7:
            raise ValueError("IBKR trade history supports 1 through 7 days")
        if account_id:
            self._require_known_account(account_id)
            await self._request("POST", "/iserver/account", {"acctId": account_id})
        payload = await self._request("GET", f"/iserver/account/trades?days={days}")
        rows = [normalize_execution(row, account_id) for row in _rows(payload)]
        return [row for row in rows if not account_id or row.account_id == account_id]

    async def positions(self, account_id: str) -> list[PortfolioPosition]:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio2/{_quote(account_id)}/positions")
        return [_position(row, account_id) for row in _rows(payload)]

    async def canonical_position_snapshot(self, account_id: str) -> tuple[SnapshotManifest, list[CanonicalPositionState]]:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio2/{_quote(account_id)}/positions")
        return normalize_position_snapshot(payload, account_id)

    async def account_summary(self, account_id: str) -> AccountSummary:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio/{_quote(account_id)}/summary")
        return _summary(payload, account_id)

    async def canonical_account_values(self, account_id: str) -> list[CanonicalAccountValue]:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio/{_quote(account_id)}/summary")
        return normalize_account_values(payload, account_id)

    async def account_ledger(self, account_id: str) -> AccountLedger:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio/{_quote(account_id)}/ledger")
        return _ledger(payload, account_id)

    async def canonical_ledger(self, account_id: str) -> list[CanonicalLedgerBalance]:
        self._require_known_account(account_id)
        payload = await self._request("GET", f"/portfolio/{_quote(account_id)}/ledger")
        return normalize_ledger(payload, account_id)

    async def on_market_event(self, event: MarketEvent) -> list[Execution]:
        return []

    async def expire_day_orders(self, account_id: str, at: datetime) -> list[LiveOrder]:
        return []

    async def _confirm_warning_chain_locked(self, response: Any) -> Any:
        current = response
        seen: set[str] = set()
        while True:
            reply_ids = [str(row["id"]) for row in _rows(current) if row.get("id") and row.get("message")]
            if not reply_ids:
                return current
            if len(reply_ids) != 1 or reply_ids[0] in seen:
                raise RuntimeError("IBKR returned an ambiguous or repeated warning-reply chain")
            reply_id = reply_ids[0]
            seen.add(reply_id)
            current = await self._request("POST", f"/iserver/reply/{_quote(reply_id)}", {"confirmed": True})

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._request_sync, method, path, payload)

    def _request_sync(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        context = None if self.verify_tls else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"IBKR {method} {path} failed with HTTP {exc.code}: {detail[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"IBKR {method} {path} failed: {exc.reason}") from exc
        return json.loads(text) if text.strip() else {}

    def _require_account_orders(self, account_id: str, orders: list[OrderRequest]) -> None:
        self._require_known_account(account_id)
        if not orders:
            raise ValueError("orders cannot be empty")
        for order in orders:
            if order.acctId != account_id:
                raise ValueError(f"Path account {account_id} does not match order acctId {order.acctId}")

    def _require_known_account(self, account_id: str) -> None:
        if self._account_ids and account_id not in self._account_ids:
            raise ValueError(f"Account {account_id} was not returned by /iserver/accounts")


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("orders", "trades", "positions", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _account_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("accounts") or payload.get("acctIds") or []
    else:
        raw = payload
    if isinstance(raw, str):
        return [raw]
    return [str(row.get("id") or row.get("accountId")) if isinstance(row, dict) else str(row) for row in (raw or [])]


def _status(value: Any) -> OrderStatus:
    compact = str(value or "Inactive").replace("_", "").replace(" ", "").lower()
    for status in OrderStatus:
        if status.value.lower() == compact:
            return status
    return OrderStatus.UNKNOWN


def _live_order(row: dict[str, Any]) -> LiveOrder:
    total = _number(row, "totalSize", "quantity", "size")
    filled = _number(row, "filledQuantity", "filled")
    return LiveOrder(
        account=str(row.get("acct") or row.get("account") or row.get("acctId") or ""),
        orderId=str(row.get("orderId") or row.get("order_id") or ""),
        conid=int(_number(row, "conid", "con_id")),
        ticker=str(row.get("ticker") or row.get("symbol") or ""),
        side=str(row.get("side") or "").upper(),
        orderType=str(row.get("orderType") or row.get("order_type") or ""),
        tif=str(row.get("tif") or "DAY"),
        totalSize=total,
        filledQuantity=filled,
        remainingQuantity=max(0.0, _number(row, "remainingQuantity", "remaining", default=total - filled)),
        avgPrice=_number(row, "avgPrice", "avg_price"),
        order_status=_status(row.get("order_status") or row.get("status")),
        cOID=str(row.get("cOID") or row.get("order_ref") or ""),
        parentId=str(row.get("parentId")) if row.get("parentId") is not None else None,
        price=_optional_number(row, "price", "limitPrice"),
        auxPrice=_optional_number(row, "auxPrice", "stopPrice"),
        outsideRTH=bool(row.get("outsideRTH", False)),
        statusDescription=str(row.get("statusDescription") or ""),
        raw=row,
    )


def _execution(row: dict[str, Any]) -> Execution:
    timestamp_ms = int(_number(row, "trade_time_r", "timestamp"))
    parsed = _parse_time(row.get("trade_time"), timestamp_ms)
    return Execution(
        execution_id=str(row.get("execution_id") or row.get("executionId") or ""),
        symbol=str(row.get("symbol") or ""),
        side=str(row.get("side") or ""),
        order_ref=str(row.get("order_ref") or row.get("orderRef") or ""),
        trade_time=parsed,
        trade_time_r=timestamp_ms or int(parsed.timestamp() * 1000),
        size=_number(row, "size", "quantity"),
        price=_number(row, "price"),
        order_id=str(row.get("order_id") or row.get("orderId") or ""),
        account=str(row.get("account") or row.get("acctId") or ""),
        conid=int(_number(row, "conid", "con_id")),
        commission=_number(row, "commission"),
        currency=str(row.get("currency") or "USD"),
        raw=row,
    )


def _position(row: dict[str, Any], account_id: str) -> PortfolioPosition:
    position = _number(row, "position")
    price = _number(row, "mktPrice", "marketPrice")
    return PortfolioPosition(
        acctId=account_id,
        conid=int(_number(row, "conid")),
        contractDesc=str(row.get("contractDesc") or row.get("ticker") or ""),
        position=position,
        mktPrice=price,
        mktValue=_number(row, "mktValue", default=position * price),
        avgCost=_number(row, "avgCost", "avgPrice"),
        avgPrice=_number(row, "avgPrice", "avgCost"),
        realizedPnl=_number(row, "realizedPnl"),
        unrealizedPnl=_number(row, "unrealizedPnl"),
        currency=str(row.get("currency") or "USD"),
        assetClass=str(row.get("assetClass") or "STK"),
        raw=row,
    )


def _summary(payload: Any, account_id: str) -> AccountSummary:
    row = payload if isinstance(payload, dict) else {}
    currency = str(_nested(row, "netliquidation", "currency") or "USD")
    return AccountSummary(
        account_id=account_id,
        netliquidation=_summary_value(row, "netliquidation"),
        totalcashvalue=_summary_value(row, "totalcashvalue"),
        buyingpower=_summary_value(row, "buyingpower"),
        grosspositionvalue=_summary_value(row, "grosspositionvalue"),
        availablefunds=_summary_value(row, "availablefunds"),
        excessliquidity=_summary_value(row, "excessliquidity"),
        currency=currency,
    )


def _ledger(payload: Any, account_id: str) -> AccountLedger:
    rows = payload if isinstance(payload, dict) else {}
    currency = "BASE" if "BASE" in rows else next(iter(rows), "USD")
    row = rows.get(currency, {}) if isinstance(rows.get(currency, {}), dict) else {}
    return AccountLedger(
        acctId=account_id,
        cashbalance=_number(row, "cashbalance", "cashBalance"),
        settledcash=_number(row, "settledcash", "settledCash"),
        stockmarketvalue=_number(row, "stockmarketvalue", "stockMarketValue"),
        netliquidationvalue=_number(row, "netliquidationvalue", "netLiquidationValue"),
        realizedpnl=_number(row, "realizedpnl", "realizedPnl"),
        unrealizedpnl=_number(row, "unrealizedpnl", "unrealizedPnl"),
        currency=currency,
        exchangerate=_number(row, "exchangerate", "exchangeRate", default=1.0),
    )


def _summary_value(row: dict[str, Any], key: str) -> float:
    value = row.get(key, 0)
    if isinstance(value, dict):
        value = value.get("amount", value.get("monetaryValue", value.get("value", 0)))
    return _float(value)


def _nested(row: dict[str, Any], key: str, nested_key: str) -> Any:
    value = row.get(key)
    return value.get(nested_key) if isinstance(value, dict) else None


def _number(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if row.get(key) is not None:
            return _float(row[key])
    return default


def _optional_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if row.get(key) is not None:
            return _float(row[key])
    return None


def _float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _parse_time(value: Any, timestamp_ms: int) -> datetime:
    if timestamp_ms:
        divisor = 1000 if timestamp_ms > 10_000_000_000 else 1
        return datetime.fromtimestamp(timestamp_ms / divisor, tz=timezone.utc)
    if value:
        text = str(value).replace("Z", "+00:00")
        for parser in (lambda: datetime.fromisoformat(text), lambda: datetime.strptime(text, "%Y%m%d-%H:%M:%S")):
            try:
                parsed = parser()
                return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
            except ValueError:
                pass
    return datetime.now(timezone.utc)
