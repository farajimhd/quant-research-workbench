"""Strict typed projections for the ARTE journal persistence lane.

These conversions belong on the writer lane, not on a realtime market callback.
They never publish or write files. Unknown broker fields fail closed until a
versioned typed column or child family represents them.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.ibkr_client import _execution as parse_ibkr_execution
from src.trading_runtime.ibkr_schema import Execution


_SOURCE_FIELDS = frozenset({
    "execution_id", "executionId", "symbol", "side", "order_ref", "orderRef",
    "trade_time", "trade_time_r", "timestamp", "size", "quantity", "price",
    "order_id", "orderId", "account", "acctId", "conid", "con_id",
    "commission", "currency", "exchange",
})
_SCALE = Decimal("0.0000000001")


@dataclass(frozen=True, slots=True)
class FillDetails:
    execution: dict[str, Any]
    commission: dict[str, Any] | None


def _exact_decimal(value: float) -> str:
    try:
        decimal = Decimal(str(value))
        quantized = decimal.quantize(_SCALE)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Broker number cannot fit Decimal(38, 10)") from exc
    if not decimal.is_finite() or decimal != quantized:
        raise ValueError("Broker number cannot fit Decimal(38, 10) losslessly")
    return format(quantized, "f")


def broker_fill_details(
    execution: Execution, *, run_id: str, event_month: str,
    batch_id: str, execution_record_id: str, commission_record_id: str | None,
    received_at: datetime, strategy_id: str = "", strategy_revision: int = 0,
    setup: str = "", exit_reason: str = "",
) -> FillDetails:
    """Project a broker fill without dropping an unmodeled source attribute."""
    if not run_id or not execution.execution_id or not execution.account or not execution.symbol:
        raise ValueError("Broker execution identity is incomplete")
    if execution.trade_time.tzinfo is None or received_at.tzinfo is None:
        raise ValueError("Broker execution timestamps must be timezone-aware")
    if execution.trade_time.astimezone(timezone.utc).strftime("%Y-%m-01") != event_month:
        raise ValueError("Broker execution partition differs from source time")
    if abs(execution.trade_time.timestamp() * 1000 - execution.trade_time_r) >= 1000:
        raise ValueError("Broker execution millisecond clock disagrees with trade time")
    unknown = set(execution.raw) - _SOURCE_FIELDS
    if unknown:
        raise ValueError(f"Broker execution has unmodeled source fields: {sorted(unknown)}")
    if execution.raw:
        parsed = parse_ibkr_execution(execution.raw)
        for field in fields(Execution):
            if field.name != "raw" and getattr(parsed, field.name) != getattr(execution, field.name):
                raise ValueError(f"Broker execution source disagrees on {field.name}")
    if execution.commission is not None and not commission_record_id:
        raise ValueError("Confirmed broker commission needs a separate typed event")
    if execution.commission is None and commission_record_id:
        raise ValueError("Pending broker commission cannot be published as final")
    common = {
        "run_id": run_id, "event_month": event_month, "batch_id": batch_id,
        "account_id": execution.account,
    }
    at = execution.trade_time.astimezone(timezone.utc).isoformat()
    received = received_at.astimezone(timezone.utc).isoformat()
    fill = {
        **common, "record_id": execution_record_id,
        "execution_id": execution.execution_id, "broker_order_id": execution.order_id,
        "client_order_id": execution.order_ref, "conid": execution.conid,
        "ticker": execution.symbol.upper(), "side": execution.side,
        "quantity": _exact_decimal(execution.size),
        "price": _exact_decimal(execution.price),
        "exchange": str(execution.raw.get("exchange") or ""),
        "currency": execution.currency, "net_amount": None,
        "cumulative_quantity": None, "average_price": None, "liquidity": "",
        "liquidation_trade": 0, "signal_price": None, "arrival_midpoint": None,
        "planned_risk": None, "source_event_time": at, "received_at": received,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "setup": setup, "exit_reason": exit_reason,
    }
    commission = None
    if execution.commission is not None:
        commission = {
            **common, "record_id": commission_record_id,
            "execution_id": execution.execution_id,
            "commission": _exact_decimal(execution.commission),
            "currency": execution.currency, "status": "final",
            "realized_pnl": None, "source_event_time": at, "received_at": received,
        }
    return FillDetails(fill, commission)


def broker_fill_batch(
    execution: Execution, *, run_id: str, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, first_sequence: int,
    source_cursor: str, status: str, received_at: datetime,
    strategy_id: str = "", strategy_revision: int = 0,
    setup: str = "", exit_reason: str = "",
) -> TypedJournalBatch:
    """Build one retry-stable fill batch on a background persistence lane."""
    event_month = execution.trade_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    identity = f"{run_id}:{batch_id}:{execution.execution_id}"
    fill_id = str(uuid5(NAMESPACE_URL, identity + ":fill"))
    fee_id = (str(uuid5(NAMESPACE_URL, identity + ":commission"))
              if execution.commission is not None else None)
    details = broker_fill_details(
        execution, run_id=run_id, event_month=event_month, batch_id=batch_id,
        execution_record_id=fill_id, commission_record_id=fee_id,
        received_at=received_at, strategy_id=strategy_id,
        strategy_revision=strategy_revision, setup=setup,
        exit_reason=exit_reason,
    )
    common = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "event_time": execution.trade_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": received_at.astimezone(timezone.utc).isoformat(),
        "category": "execution", "entity_id": execution.execution_id,
        "account_id": execution.account, "correlation_id": "", "causation_id": "",
    }
    events = [{**common, "record_id": fill_id, "sequence": first_sequence,
               "entity_type": "fill"}]
    if fee_id is not None:
        events.append({**common, "record_id": fee_id,
                       "sequence": first_sequence + 1,
                       "entity_type": "commission"})
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        first_sequence, first_sequence + len(events) - 1,
        source_cursor, status, tuple(events),
        executions=(details.execution,),
        commissions=(details.commission,) if details.commission is not None else (),
    )
