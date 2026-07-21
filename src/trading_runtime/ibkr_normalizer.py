from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import uuid4

from src.trading_runtime.domain import (
    AccountValue,
    BrokerAccount,
    BrokerProvider,
    Execution,
    InstrumentContract,
    LedgerBalance,
    OrderLifecycleState,
    OrderState,
    PositionState,
    SnapshotManifest,
    decimal_value,
    utc_now,
)


IBKR_STATUS_MAP = {
    "inactive": OrderLifecycleState.INACTIVE,
    "pendingsubmit": OrderLifecycleState.PENDING_SUBMISSION,
    "pending_submit": OrderLifecycleState.PENDING_SUBMISSION,
    "presubmitted": OrderLifecycleState.TRIGGER_PENDING,
    "pre_submitted": OrderLifecycleState.TRIGGER_PENDING,
    "submitted": OrderLifecycleState.WORKING,
    "filled": OrderLifecycleState.FILLED,
    "pendingcancel": OrderLifecycleState.CANCEL_PENDING,
    "pending_cancel": OrderLifecycleState.CANCEL_PENDING,
    "precancelled": OrderLifecycleState.CANCEL_PENDING,
    "pre_cancelled": OrderLifecycleState.CANCEL_PENDING,
    "cancelled": OrderLifecycleState.CANCELLED,
    "canceled": OrderLifecycleState.CANCELLED,
    "warnstate": OrderLifecycleState.PENDING_SUBMISSION,
    "warn_state": OrderLifecycleState.PENDING_SUBMISSION,
    "rejected": OrderLifecycleState.REJECTED,
    "expired": OrderLifecycleState.EXPIRED,
}


def ibkr_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, UTC)
    text = str(value or "").strip()
    if text:
        for fmt in ("%Y%m%d-%H:%M:%S", "%Y%m%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
            except ValueError:
                pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            pass
    return fallback or utc_now()


def normalize_accounts(portfolio_payload: Any, trading_payload: Any) -> list[BrokerAccount]:
    viewable = {_account_id(row): row for row in _rows(portfolio_payload) if _account_id(row)}
    tradable = {_account_id(row): row for row in _account_rows(trading_payload) if _account_id(row)}
    result: list[BrokerAccount] = []
    for account_id in sorted(set(viewable) | set(tradable)):
        raw = {"portfolio": viewable.get(account_id, {}), "iserver": tradable.get(account_id, {})}
        source = tradable.get(account_id) or viewable.get(account_id) or {}
        result.append(
            BrokerAccount(
                provider=BrokerProvider.IBKR_CPAPI,
                account_id=account_id,
                base_currency=str(source.get("currency") or source.get("baseCurrency") or "USD"),
                account_type=str(source.get("type") or source.get("accountType") or ""),
                alias=str(source.get("accountAlias") or source.get("alias") or ""),
                title=str(source.get("accountTitle") or source.get("title") or ""),
                parent_account_id=str(source.get("parent") or source.get("parentAccount") or ""),
                can_view=account_id in viewable,
                can_trade=account_id in tradable,
                raw=raw,
            )
        )
    return result


def normalize_order(row: dict[str, Any], fallback_account: str = "") -> OrderState:
    raw_status = str(row.get("order_status") or row.get("status") or row.get("order_ccp_status") or "")
    normalized_key = raw_status.replace(" ", "").lower()
    lifecycle = IBKR_STATUS_MAP.get(normalized_key, OrderLifecycleState.UNKNOWN)
    total = decimal_value(row.get("totalSize") or row.get("quantity") or row.get("total_quantity"))
    filled = decimal_value(row.get("filledQuantity") or row.get("filled") or row.get("filled_quantity"))
    remaining = decimal_value(row.get("remainingQuantity") or row.get("remaining") or row.get("remaining_quantity"), "-1")
    if remaining < 0:
        remaining = max(Decimal("0"), total - filled)
    if lifecycle == OrderLifecycleState.WORKING and filled > 0 and remaining > 0:
        lifecycle = OrderLifecycleState.PARTIALLY_FILLED
    terminal = lifecycle in {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
    }
    not_editable = _bool(row.get("order_not_editable") or row.get("orderNotEditable"))
    cannot_cancel = _bool(row.get("cannot_cancel_order") or row.get("cannotCancelOrder"))
    return OrderState(
        account_id=str(row.get("acct") or row.get("acctId") or row.get("account") or fallback_account),
        instrument=_instrument(row),
        lifecycle_state=lifecycle,
        broker_status_raw=raw_status,
        broker_order_id=str(row.get("orderId") or row.get("order_id") or ""),
        client_order_id=str(row.get("order_ref") or row.get("cOID") or row.get("client_order_id") or ""),
        command_id=str(row.get("command_id") or ""),
        parent_order_id=str(row.get("parentId") or row.get("parent_id") or ""),
        side=str(row.get("side") or "").upper(),
        order_type=str(row.get("orderType") or row.get("origOrderType") or row.get("order_type") or ""),
        time_in_force=str(row.get("tif") or row.get("timeInForce") or ""),
        total_quantity=total,
        filled_quantity=filled,
        remaining_quantity=remaining,
        average_fill_price=decimal_value(row.get("avgPrice") or row.get("avg_fill_price")),
        limit_price=_optional_decimal(row.get("price") or row.get("limit_price")),
        stop_price=_optional_decimal(row.get("auxPrice") or row.get("stop_price")),
        outside_rth=_bool(row.get("outsideRTH") or row.get("outside_rth")),
        can_modify=not terminal and not not_editable,
        can_cancel=not terminal and not cannot_cancel,
        terminal=terminal,
        warning=str(row.get("warning") or row.get("message") or ""),
        rejection_code=str(row.get("errorCode") or row.get("error_code") or ""),
        rejection_reason=str(row.get("error") or row.get("statusDescription") or row.get("description") or ""),
        source_event_time=ibkr_datetime(row.get("lastExecutionTime_r") or row.get("lastExecutionTime") or row.get("last_execution_time")),
        raw=dict(row),
    )


def normalize_execution(row: dict[str, Any], fallback_account: str = "") -> Execution:
    timestamp = ibkr_datetime(row.get("trade_time_r") or row.get("trade_time") or row.get("executionTime") or row.get("time"))
    commission_value = row.get("commission")
    metadata = row.get("canonical_metadata") or row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return Execution(
        execution_id=str(row.get("execution_id") or row.get("executionId") or row.get("execId") or row.get("tradeId") or row.get("id") or ""),
        account_id=str(row.get("account") or row.get("accountCode") or row.get("acctId") or fallback_account),
        instrument=_instrument(row),
        side=_execution_side(row.get("side") or row.get("buySell")),
        quantity=decimal_value(row.get("size") or row.get("shares") or row.get("quantity")),
        price=decimal_value(row.get("price") or row.get("tradePrice") or row.get("executionPrice")),
        source_event_time=timestamp,
        broker_order_id=str(row.get("order_id") or row.get("orderId") or ""),
        client_order_id=str(row.get("order_ref") or row.get("cOID") or ""),
        exchange=str(row.get("exchange") or ""),
        commission=_optional_decimal(commission_value),
        commission_currency=str(row.get("commission_currency") or row.get("currency") or ""),
        commission_status="final" if commission_value not in (None, "") else "pending",
        net_amount=_optional_decimal(row.get("net_amount") or row.get("netAmount")),
        cumulative_quantity=_optional_decimal(row.get("cumQty") or row.get("cumulative_quantity")),
        average_price=_optional_decimal(row.get("avgPrice") or row.get("average_price")),
        liquidity=str(row.get("lastLiquidity") or row.get("liquidity") or ""),
        liquidation_trade=_bool(row.get("liquidation_trade") or row.get("liquidation")),
        strategy_id=str(row.get("strategy_id") or row.get("strategy") or metadata.get("strategy_id") or ""),
        strategy_revision=_integer(row.get("strategy_revision") or row.get("canonical_strategy_revision") or metadata.get("strategy_revision")),
        run_id=str(row.get("run_id") or row.get("canonical_run_id") or metadata.get("run_id") or ""),
        setup=str(row.get("setup") or metadata.get("setup") or metadata.get("tag") or ""),
        exit_reason=str(row.get("exit_reason") or row.get("reason") or metadata.get("exit_reason") or ""),
        signal_price=_optional_decimal(row.get("signal_price") or metadata.get("signal_price")),
        arrival_midpoint=_optional_decimal(row.get("arrival_midpoint") or metadata.get("arrival_midpoint")),
        planned_risk=_optional_decimal(row.get("planned_risk") or metadata.get("planned_risk")),
        raw=dict(row),
    )


def normalize_position_snapshot(payload: Any, account_id: str) -> tuple[SnapshotManifest, list[PositionState]]:
    started_at = utc_now()
    snapshot_id = str(uuid4())
    rows = [row for row in _rows(payload) if isinstance(row, dict)]
    positions = [normalize_position(row, account_id, snapshot_id) for row in rows]
    completed_at = utc_now()
    return (
        SnapshotManifest(
            snapshot_id=snapshot_id,
            entity_kind="position",
            account_id=account_id,
            provider=BrokerProvider.IBKR_CPAPI,
            started_at=started_at,
            completed_at=completed_at,
            complete=True,
            expected_pages=1,
            received_pages=1,
            item_count=len(positions),
            source_watermark=completed_at.isoformat(),
        ),
        positions,
    )


def normalize_position(row: dict[str, Any], account_id: str, snapshot_id: str) -> PositionState:
    quantity = decimal_value(row.get("position") or row.get("quantity"))
    market_price = decimal_value(row.get("mktPrice") or row.get("marketPrice") or row.get("mark_price"))
    market_value = _optional_decimal(row.get("mktValue") or row.get("marketValue") or row.get("market_value"))
    return PositionState(
        snapshot_id=snapshot_id,
        account_id=account_id,
        instrument=_instrument(row),
        quantity=quantity,
        market_price=market_price,
        market_value=market_value if market_value is not None else quantity * market_price,
        average_cost=decimal_value(row.get("avgCost") or row.get("averageCost") or row.get("average_cost")),
        average_price=decimal_value(row.get("avgPrice") or row.get("averagePrice") or row.get("average_price") or row.get("avgCost") or row.get("averageCost")),
        realized_pnl=decimal_value(row.get("realizedPnl") or row.get("realized_pnl")),
        unrealized_pnl=decimal_value(row.get("unrealizedPnl") or row.get("unrealized_pnl")),
        model=str(row.get("model") or ""),
        is_last_to_liquidate=_bool(row.get("isLastToLiq") or row.get("isLastToLoq")),
        source_event_time=ibkr_datetime(row.get("timestamp")),
        raw=dict(row),
    )


def normalize_account_values(payload: Any, account_id: str) -> list[AccountValue]:
    if not isinstance(payload, dict):
        return []
    result: list[AccountValue] = []
    for raw_key, raw_value in payload.items():
        value = raw_value if isinstance(raw_value, dict) else {"value": raw_value}
        key, segment = _summary_key(str(raw_key))
        amount = value.get("amount", value.get("monetaryValue"))
        result.append(
            AccountValue(
                account_id=account_id,
                key=key,
                value=str(value.get("value") or ""),
                monetary_value=_optional_decimal(amount),
                currency=str(value.get("currency") or ""),
                segment=segment,
                is_null=_bool(value.get("isNull")),
                severity=int(value.get("severity") or 0),
                source_event_time=ibkr_datetime(value.get("timestamp")),
                raw={"source_key": raw_key, **value},
            )
        )
    return result


def normalize_ledger(payload: Any, account_id: str) -> list[LedgerBalance]:
    if not isinstance(payload, dict):
        return []
    result: list[LedgerBalance] = []
    for raw_currency, raw_value in payload.items():
        if not isinstance(raw_value, dict):
            continue
        currency = str(raw_currency).replace("LedgerList", "") or "BASE"
        values: dict[str, Decimal | str | bool] = {}
        for key, value in raw_value.items():
            if key in {"timestamp", "severity", "acctCode", "key", "secondKey"}:
                continue
            values[str(key)] = _typed_ledger_value(value)
        result.append(
            LedgerBalance(
                account_id=account_id,
                currency=currency,
                values=values,
                is_base=currency.upper() == "BASE",
                source_event_time=ibkr_datetime(raw_value.get("timestamp")),
                raw={"source_key": raw_currency, **raw_value},
            )
        )
    return result


def merge_ledger_delta(current: Iterable[LedgerBalance], delta: Iterable[LedgerBalance]) -> list[LedgerBalance]:
    merged = {(row.account_id, row.currency): row for row in current}
    for update in delta:
        key = (update.account_id, update.currency)
        previous = merged.get(key)
        values = dict(previous.values) if previous else {}
        values.update(update.values)
        merged[key] = LedgerBalance(
            account_id=update.account_id,
            currency=update.currency,
            values=values,
            is_base=update.is_base,
            source_event_time=update.source_event_time,
            received_at=update.received_at,
            raw=update.raw,
        )
    return sorted(merged.values(), key=lambda row: (row.account_id, not row.is_base, row.currency))


def _instrument(row: dict[str, Any]) -> InstrumentContract:
    conid = int(decimal_value(row.get("conid") or row.get("con_id") or row.get("contractId")))
    symbol = str(
        row.get("ticker")
        or row.get("symbol")
        or row.get("contractDesc")
        or row.get("description")
        or row.get("contract_description_1")
        or ""
    ).split(" ")[0].upper()
    security_type = str(row.get("secType") or row.get("sec_type") or row.get("assetClass") or row.get("asset_class") or "STK")
    currency = str(row.get("currency") or row.get("commission_currency") or "USD")
    return InstrumentContract(
        instrument_id=f"ibkr:{conid}" if conid else f"symbol:{symbol}:{security_type}:{currency}",
        conid=conid,
        symbol=symbol,
        security_type=security_type,
        currency=currency,
        exchange=str(row.get("exchange") or "SMART"),
        primary_exchange=str(row.get("listingExchange") or row.get("listing_exchange") or row.get("primaryExchange") or ""),
        local_symbol=str(row.get("localSymbol") or row.get("contract_description_1") or symbol),
        trading_class=str(row.get("tradingClass") or ""),
        multiplier=decimal_value(row.get("multiplier"), "1"),
        expiry=str(row.get("expiry") or row.get("maturityDate") or row.get("lastTradeDateOrContractMonth") or ""),
        strike=_optional_decimal(row.get("strike")),
        right=str(row.get("right") or ""),
        provider_ids={"ibkr_conid": str(conid)} if conid else {},
    )


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("accounts", "orders", "trades", "positions", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return [payload]


def _account_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("accounts"), list):
        metadata = payload.get("acctProps") if isinstance(payload.get("acctProps"), dict) else {}
        rows: list[dict[str, Any]] = []
        for value in payload["accounts"]:
            if isinstance(value, dict):
                rows.append(value)
                continue
            account_id = str(value or "")
            details = metadata.get(account_id) if isinstance(metadata.get(account_id), dict) else {}
            rows.append({"accountId": account_id, **details})
        return rows
    return _rows(payload)


def _account_id(row: dict[str, Any]) -> str:
    return str(row.get("accountId") or row.get("id") or row.get("account") or row.get("acct") or row.get("acctId") or "")


def _summary_key(key: str) -> tuple[str, str]:
    lowered = key.lower()
    if lowered.endswith("-c"):
        return lowered[:-2], "commodity"
    if lowered.endswith("-s"):
        return lowered[:-2], "security"
    return lowered, "base"


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else decimal_value(value)


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _typed_ledger_value(value: Any) -> Decimal | str | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return decimal_value(value)
    try:
        return Decimal(str(value))
    except Exception:
        return str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _execution_side(value: Any) -> str:
    normalized = str(value or "").upper()
    return {"B": "BUY", "BOT": "BUY", "S": "SELL", "SLD": "SELL"}.get(normalized, normalized)
