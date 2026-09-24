"""Strict typed projections for the ARTE journal persistence lane.

These conversions belong on the writer lane, not on a realtime market callback.
They never publish or write files. Unknown broker fields fail closed until a
versioned typed column or child family represents them.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.domain import CommissionEvent
from src.trading_runtime.ibkr_client import _execution as parse_ibkr_execution
from src.trading_runtime.ibkr_schema import Execution, OrderRequest
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.signals import StrategySignal


_SOURCE_FIELDS = frozenset({
    "execution_id", "executionId", "symbol", "side", "order_ref", "orderRef",
    "trade_time", "trade_time_r", "timestamp", "size", "quantity", "price",
    "order_id", "orderId", "account", "acctId", "conid", "con_id",
    "commission", "currency", "exchange",
})
_SCALE = Decimal("0.0000000001")
_MEASURE_SCALE = Decimal("0.000000000000000001")


@dataclass(frozen=True, slots=True)
class FillDetails:
    execution: dict[str, Any]
    commission: dict[str, Any] | None


def runtime_lifecycle_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    expected_config: dict[str, Any] | None = None,
) -> TypedJournalBatch:
    """Project the runtime's start/finish record without persisting config twice."""
    if (record.category, record.entity_type, record.entity_id, record.account_id) != (
        "lifecycle", "run", record.run_id, "",
    ):
        raise ValueError("Run lifecycle record identity is invalid")
    if record.event_time.tzinfo is None or record.recorded_at.tzinfo is None:
        raise ValueError("Run lifecycle timestamps must be timezone-aware")
    payload = dict(record.payload)
    status = payload.get("status")
    if status not in {"running", "completed", "stopped", "failed"}:
        raise ValueError("Run lifecycle status is invalid")
    common = {"status", "correlation_id", "causation_id"}
    if status == "running":
        if set(payload) - common != {"config"} or expected_config is None:
            raise ValueError("Run start lacks its pinned typed configuration")
        if canonical_json(payload["config"]) != canonical_json(expected_config):
            raise ValueError("Run start configuration differs from the typed run context")
        processed_events = None
    else:
        if set(payload) - common != {"processed_events"}:
            raise ValueError("Run finish has unmodeled lifecycle evidence")
        processed_events = payload["processed_events"]
        if type(processed_events) is not int or processed_events < 0:
            raise ValueError("Run finish processed-event count is invalid")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    received = record.recorded_at.astimezone(timezone.utc).isoformat()
    event_month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": received,
        "category": "lifecycle", "entity_type": "run",
        "entity_id": record.run_id, "account_id": "",
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    transition = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": "", "status": status,
        "processed_events": processed_events, "source_event_time": at,
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, status, (event,),
        run_transitions=(transition,),
    )


def _exact_decimal(value: float | Decimal, scale: Decimal = _SCALE) -> str:
    try:
        decimal = Decimal(str(value))
        quantized = decimal.quantize(scale)
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
            "time_authority": "execution",
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


def order_command_batch(
    request: OrderRequest, *, run_id: str, run_month: date,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    command_id: str, created_at: datetime, recorded_at: datetime,
    strategy_id: str = "", strategy_revision: int = 0,
    strategy_intent_id: str = "", order_group_id: str = "",
    policy_version: str = "", strategy_intent_record_id: str = "",
    strategy_intent_content_hash: str = "",
) -> TypedJournalBatch:
    """Capture one simple broker command losslessly before external dispatch.

    Strategy metadata and broker algo parameters still need typed child rows;
    neither may be silently discarded by this projection.
    """
    if request.raw or request.strategyParameters:
        raise ValueError("Order command has unmodeled nested broker or strategy evidence")
    if (not command_id or not request.cOID or not run_id
            or created_at.tzinfo is None or recorded_at.tzinfo is None
            or strategy_revision < 0):
        raise ValueError("Order command identity or time is incomplete")
    if any((strategy_intent_id, order_group_id, policy_version)) and not all(
        (strategy_intent_id, order_group_id, policy_version)
    ):
        raise ValueError("Strategy order command context must be complete")
    if bool(strategy_intent_record_id) != bool(strategy_intent_content_hash):
        raise ValueError("Exact intent revision identity and hash must be paired")
    if strategy_intent_record_id and not strategy_intent_id:
        raise ValueError("Exact intent revision requires strategy command context")
    if strategy_intent_content_hash and not re.fullmatch(r"[0-9a-f]{64}", strategy_intent_content_hash):
        raise ValueError("Exact intent revision hash must be SHA-256")
    at = created_at.astimezone(timezone.utc).isoformat()
    received = recorded_at.astimezone(timezone.utc).isoformat()
    event_month = created_at.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = str(uuid5(NAMESPACE_URL, f"{run_id}:{batch_id}:{command_id}:order-command"))
    event = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record_id, "sequence": sequence,
        "event_time": at, "recorded_at": received,
        "category": "order_management", "entity_type": "order_command",
        "entity_id": command_id, "account_id": request.acctId,
        "correlation_id": "", "causation_id": "",
    }
    detail = {
        "record_id": record_id, "run_id": run_id, "event_month": event_month,
        "batch_id": batch_id, "account_id": request.acctId,
        "command_id": command_id, "client_order_id": request.cOID,
        "conid": request.conid, "ticker": request.ticker,
        "side": request.side, "order_type": request.orderType,
        "time_in_force": request.tif,
        "quantity": _exact_decimal(request.quantity) if request.quantity is not None else None,
        "cash_quantity": _exact_decimal(request.cashQty) if request.cashQty is not None else None,
        "limit_price": _exact_decimal(request.price) if request.price is not None else None,
        "aux_price": _exact_decimal(request.auxPrice) if request.auxPrice is not None else None,
        "outside_rth": int(request.outsideRTH),
        "parent_command_id": "", "oca_group": "",
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "created_at": at, "security_type": request.secType,
        "listing_exchange": request.listingExchange,
        "trailing_amount": (_exact_decimal(request.trailingAmt)
                            if request.trailingAmt is not None else None),
        "trailing_type": request.trailingType or "",
        "single_group": int(request.isSingleGroup),
        "manual_indicator": int(request.manualIndicator),
        "external_operator": request.extOperator or "",
        "referrer": request.referrer or "",
        "broker_strategy": request.strategy or "",
        "parent_broker_order_id": request.parentId or "",
    }
    context = ({
        "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:strategy-context")),
        "parent_record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": request.acctId,
        "strategy_intent_id": strategy_intent_id,
        "order_group_id": order_group_id,
        "policy_version": policy_version,
    },) if strategy_intent_id else ()
    intent_use = ({
        "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:intent-use")),
        "parent_record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": request.acctId,
        "intent_record_id": str(UUID(strategy_intent_record_id)),
        "intent_content_hash": strategy_intent_content_hash,
    },) if strategy_intent_record_id else ()
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        order_commands=(detail,), order_contexts=context, intent_uses=intent_use,
    )


def commission_revision_batch(
    report: CommissionEvent, *, run_id: str, run_month: date,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    time_authority: str, commission_status: str = "final",
) -> TypedJournalBatch:
    """Publish a later fee revision without duplicating its execution row."""
    if report.raw:
        raise ValueError("Commission report has unmodeled source fields")
    if not report.execution_id or not report.account_id or not report.currency:
        raise ValueError("Commission report identity is incomplete")
    if report.source_event_time.tzinfo is None or report.received_at.tzinfo is None:
        raise ValueError("Commission report timestamps must be timezone-aware")
    if time_authority not in {"broker", "observation"}:
        raise ValueError("Commission report time authority is required")
    source_time = report.source_event_time.astimezone(timezone.utc)
    received = report.received_at.astimezone(timezone.utc)
    if time_authority == "observation" and source_time != received:
        raise ValueError("Observed commission time must equal receipt time")
    if commission_status not in {"final", "corrected", "reversed"}:
        raise ValueError("Commission revision status is invalid")
    event_month = source_time.strftime("%Y-%m-01")
    record_id = str(uuid5(
        NAMESPACE_URL,
        f"{run_id}:{batch_id}:{report.execution_id}:commission-revision",
    ))
    event = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record_id, "sequence": sequence,
        "event_time": source_time.isoformat(), "recorded_at": received.isoformat(),
        "category": "execution", "entity_type": "commission",
        "entity_id": report.execution_id, "account_id": report.account_id,
        "correlation_id": "", "causation_id": "",
    }
    detail = {
        "record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": report.account_id, "execution_id": report.execution_id,
        "commission": _exact_decimal(report.commission),
        "currency": report.currency, "status": commission_status,
        "time_authority": time_authority,
        "realized_pnl": (_exact_decimal(report.realized_pnl)
                         if report.realized_pnl is not None else None),
        "source_event_time": source_time.isoformat(),
        "received_at": received.isoformat(),
    }
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        commissions=(detail,),
    )


def strategy_signal_batch(
    signal: StrategySignal, *, run_id: str, run_month: date,
    account_id: str, strategy_id: str, strategy_revision: int,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    recorded_at: datetime,
) -> TypedJournalBatch:
    """Project a signal only when every source and evidence field is represented."""
    if signal.metadata:
        raise ValueError("Strategy signal metadata has no typed evidence contract")
    if not signal.signal_id or not signal.ticker or not strategy_id or not account_id:
        raise ValueError("Strategy signal identity is incomplete")
    if not (-1 <= signal.score <= 1 and 0 <= signal.confidence <= 1):
        raise ValueError("Strategy signal score or confidence is out of range")
    if (len(signal.source_signal_ids) > 65535
            or any(not isinstance(source, str) or not source
                   for source in signal.source_signal_ids)):
        raise ValueError("Strategy signal sources are invalid")
    if signal.event_time.tzinfo is None or recorded_at.tzinfo is None:
        raise ValueError("Strategy signal timestamps must be timezone-aware")
    at = signal.event_time.astimezone(timezone.utc).isoformat()
    received = recorded_at.astimezone(timezone.utc).isoformat()
    month = signal.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = str(uuid5(NAMESPACE_URL,
        f"{run_id}:{batch_id}:{strategy_id}:{signal.signal_id}:signal"))
    event = {
        "run_id": run_id, "event_month": month, "attempt_id": attempt_id,
        "batch_id": batch_id, "record_id": record_id, "sequence": sequence,
        "event_time": at, "recorded_at": received,
        "category": "strategy_decision", "entity_type": "signal",
        "entity_id": signal.signal_id, "account_id": account_id,
        "correlation_id": "", "causation_id": "",
    }
    detail = {
        "record_id": record_id, "run_id": run_id, "event_month": month,
        "batch_id": batch_id, "account_id": account_id,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "signal_id": signal.signal_id, "signal_type": signal.signal_type,
        "ticker": signal.ticker.upper(),
        "action": str(getattr(signal.action, "value", signal.action)),
        "direction": str(getattr(signal.direction, "value", signal.direction)),
        "score": _exact_decimal(signal.score, _MEASURE_SCALE),
        "confidence": _exact_decimal(signal.confidence, _MEASURE_SCALE),
        "reason": signal.reason, "working_timeframe": signal.working_timeframe,
        "invalidation_price": (_exact_decimal(signal.invalidation_price)
                               if signal.invalidation_price is not None else None),
        "source_signal_count": len(signal.source_signal_ids),
        "source_event_time": at,
    }
    sources = tuple({
        "record_id": str(uuid5(NAMESPACE_URL,
            f"{record_id}:source:{ordinal}:{source_id}")),
        "run_id": run_id, "event_month": month, "batch_id": batch_id,
        "parent_record_id": record_id, "source_ordinal": ordinal,
        "source_signal_id": source_id,
    } for ordinal, source_id in enumerate(signal.source_signal_ids))
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        signals=(detail,), signal_sources=sources,
    )
