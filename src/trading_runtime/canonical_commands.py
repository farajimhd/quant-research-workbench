from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.trading_runtime.domain import (
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerProvider,
    OrderIntent,
    TradingMode,
    json_safe,
)
from src.trading_runtime.ibkr_schema import OrderRequest


def intent_to_ibkr_request(intent: OrderIntent) -> OrderRequest:
    """Translate the broker-neutral command once, at the IBKR boundary."""
    return OrderRequest(
        acctId=intent.account_id,
        conid=intent.instrument.conid,
        secType=intent.instrument.security_type,
        orderType=intent.order_type,
        side=intent.side,
        quantity=float(intent.quantity) if intent.quantity is not None else None,
        cashQty=float(intent.cash_quantity) if intent.cash_quantity is not None else None,
        cOID=intent.client_order_id,
        parentId=intent.parent_command_id or None,
        ticker=intent.instrument.symbol,
        tif=intent.time_in_force,
        outsideRTH=intent.outside_rth,
        price=float(intent.limit_price) if intent.limit_price is not None else None,
        auxPrice=float(intent.stop_price) if intent.stop_price is not None else None,
        trailingAmt=float(intent.trailing_amount) if intent.trailing_amount is not None else None,
        trailingType=intent.trailing_type or None,
        listingExchange=intent.instrument.exchange,
        isSingleGroup=bool(intent.oca_group),
        strategy=intent.strategy_id or None,
        raw={
            "canonical_command_id": intent.command_id,
            "canonical_run_id": intent.run_id,
            "canonical_strategy_revision": intent.strategy_revision,
            "canonical_metadata": json_safe(intent.metadata),
        },
    )


def command_event(intent: OrderIntent, provider: BrokerProvider, mode: TradingMode) -> BrokerEventEnvelope:
    return BrokerEventEnvelope.create(
        event_type=BrokerEventType.ORDER_COMMAND,
        provider=provider,
        mode=mode,
        account_id=intent.account_id,
        payload=json_safe(asdict(intent)),
        source_event_time=intent.created_at,
        command_id=intent.command_id,
        client_order_id=intent.client_order_id,
        run_id=intent.run_id,
    )


def response_events(
    intent: OrderIntent,
    rows: list[dict[str, Any]],
    provider: BrokerProvider,
    mode: TradingMode,
) -> list[BrokerEventEnvelope]:
    events = [command_event(intent, provider, mode)]
    for row in rows:
        warning = bool(row.get("message") and row.get("id"))
        rejected = bool(row.get("error") or row.get("errorCode"))
        event_type = BrokerEventType.ORDER_WARNING if warning else BrokerEventType.ORDER_REJECTED if rejected else BrokerEventType.ORDER_ACKNOWLEDGED
        events.append(
            BrokerEventEnvelope.create(
                event_type=event_type,
                provider=provider,
                mode=mode,
                account_id=intent.account_id,
                payload=row,
                source_event_time=intent.created_at,
                command_id=intent.command_id,
                client_order_id=intent.client_order_id,
                broker_order_id=str(row.get("order_id") or row.get("orderId") or ""),
                broker_event_id=str(row.get("id") or ""),
                run_id=intent.run_id,
            )
        )
    return events


def lifecycle_event(
    *,
    event_type: BrokerEventType,
    account_id: str,
    broker_order_id: str,
    payload: dict[str, Any],
    provider: BrokerProvider,
    mode: TradingMode,
    intent: OrderIntent | None = None,
) -> BrokerEventEnvelope:
    return BrokerEventEnvelope.create(
        event_type=event_type,
        provider=provider,
        mode=mode,
        account_id=account_id,
        payload=payload,
        broker_order_id=broker_order_id,
        command_id=intent.command_id if intent else "",
        client_order_id=intent.client_order_id if intent else "",
        run_id=intent.run_id if intent else "",
    )
