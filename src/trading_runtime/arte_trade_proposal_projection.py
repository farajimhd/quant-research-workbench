"""Inactive, lossless normalized projection of closed trade-proposal variants.

The active producer is TradingRuntime.submit_external_intent. This deliberately
rejects open market evidence, Portfolio metrics, and OMS order groups until
they have separately versioned, named-column contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

from .arte_journal_schema import TableContract


KEY = (("run_id", "String"), ("record_id", "UUID"))
TABLES = (
    TableContract("trading_trade_proposal_v1", KEY + (
        ("event_time", "DateTime64(6, 'UTC')"), ("proposal_id", "String"),
        ("account_id", "String"), ("authority", "LowCardinality(String)"),
        ("entity_type", "LowCardinality(String)"), ("status", "LowCardinality(String)"),
    ), "toYYYYMM(event_time)", "run_id, record_id"),
    TableContract("trading_trade_proposal_intent_v1", KEY + (
        ("event_time", "DateTime64(6, 'UTC')"), ("intent_id", "String"),
        ("ticker", "String"),
        ("action", "LowCardinality(String)"), ("quantity", "Decimal(38, 18)"),
        ("reference_price", "Decimal(38, 18)"), ("schema_version", "UInt16"),
        ("invalidation_price", "Nullable(Decimal(38, 18))"),
        ("profit_target_price", "Nullable(Decimal(38, 18))"),
        ("trailing_amount", "Nullable(Decimal(38, 18))"),
        ("urgency", "LowCardinality(String)"), ("time_in_force", "String"),
        ("outside_rth", "Bool"), ("reason", "String"),
    ), "toYYYYMM(event_time)", "run_id, record_id"),
    TableContract("trading_trade_proposal_result_v1", KEY + (
        ("event_time", "DateTime64(6, 'UTC')"), ("error", "String"),
        ("decision_reason", "String"),
        ("held_quantity", "Nullable(Decimal(38, 18))"),
    ), "toYYYYMM(event_time)", "run_id, record_id"),
)

INTENT_KEYS = frozenset({
    "intent_id", "ticker", "event_time", "action", "quantity", "reference_price",
    "schema_version", "capital_request", "invalidation_price", "profit_target_price",
    "trailing_amount", "execution_policy", "protection_profile", "urgency",
    "time_in_force", "outside_rth", "reason", "metadata",
})
INTENT_SCALARS = tuple(name for name, _ in TABLES[1].columns if name not in {
    "run_id", "record_id", "event_time",
})


@dataclass(frozen=True, slots=True)
class TradeProposalRows:
    parent: dict[str, Any]
    intent: dict[str, Any] | None
    result: dict[str, Any] | None


def _exact(value: Any, keys: set[str] | frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{label} has unsupported fields")
    return value


def _decimal(value: Any, label: str) -> str:
    if type(value) not in (int, float):
        raise ValueError(f"Invalid {label}")
    try:
        number = Decimal(str(value))
        scaled = number.quantize(Decimal("0.000000000000000001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
        raise ValueError(f"Invalid {label}")
    return format(scaled, "f")


def project_trade_proposal(record: Any) -> TradeProposalRows:
    """Project only exact, closed producer variants; never discard a field."""
    if record.category != "trade_proposal" or record.entity_type not in {
        "trade_proposal_confirmed", "trade_proposal_result",
    }:
        raise ValueError("Unsupported trade-proposal envelope")
    UUID(str(record.record_id))
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Trade-proposal payload must be a mapping")
    proposal_id = payload.get("proposal_id")
    authority = payload.get("authority")
    if not isinstance(proposal_id, str) or not proposal_id or proposal_id != record.entity_id:
        raise ValueError("Trade-proposal identity mismatch")
    if authority not in {"manual", "semi_automatic"}:
        raise ValueError("Unsupported trade-proposal authority")
    status = payload.get("status")
    common = {"run_id": record.run_id, "record_id": str(record.record_id),
              "event_time": record.event_time}
    parent = {**common, "proposal_id": proposal_id, "account_id": record.account_id,
              "authority": authority, "entity_type": record.entity_type, "status": status}
    if record.entity_type == "trade_proposal_confirmed":
        if set(payload) - {"proposal_id", "authority", "status", "intent",
                            "correlation_id", "causation_id"} or not {
            "proposal_id", "authority", "status", "intent",
        } <= set(payload):
            raise ValueError("Confirmation has unsupported fields")
        if status != "confirmed":
            raise ValueError("Invalid confirmation status")
        intent = _exact(payload["intent"], INTENT_KEYS, "proposal intent")
        for name in ("capital_request", "execution_policy", "protection_profile"):
            if intent[name] is not None:
                raise ValueError(f"Proposal {name} needs a typed child contract")
        if intent["metadata"] != {}:
            raise ValueError("Proposal metadata needs a named evidence contract")
        if not isinstance(intent["event_time"], datetime) or intent["event_time"] != record.event_time:
            raise ValueError("Proposal intent clock mismatch")
        if intent["intent_id"] != f"proposal:{proposal_id}":
            raise ValueError("Proposal intent identity mismatch")
        text_fields = ("intent_id", "ticker", "action", "urgency", "time_in_force", "reason")
        numeric_fields = ("quantity", "reference_price")
        optional_numeric = ("invalidation_price", "profit_target_price", "trailing_amount")
        if any(not isinstance(intent[name], str) for name in text_fields):
            raise ValueError("Proposal intent text field has wrong type")
        if (type(intent["schema_version"]) is not int or
                type(intent["outside_rth"]) is not bool or
                any(type(intent[name]) not in (int, float) for name in numeric_fields) or
                any(intent[name] is not None and type(intent[name]) not in (int, float)
                    for name in optional_numeric)):
            raise ValueError("Proposal intent scalar field has wrong type")
        row = {**common, **{name: intent[name] for name in INTENT_SCALARS}}
        for name in (*numeric_fields, *optional_numeric):
            if row[name] is not None:
                row[name] = _decimal(row[name], name)
        return TradeProposalRows(parent, row, None)
    if status == "failed":
        if set(payload) - {"proposal_id", "authority", "status", "error",
                            "correlation_id", "causation_id"} or not {
            "proposal_id", "authority", "status", "error",
        } <= set(payload):
            raise ValueError("Failed result has unsupported fields")
        if not isinstance(payload["error"], str):
            raise ValueError("Failure error must be text")
        result = {**common, "error": payload["error"],
                  "decision_reason": "", "held_quantity": None}
    else:
        if set(payload) - {"proposal_id", "authority", "status", "decision", "order_group",
                            "correlation_id", "causation_id"} or not {
            "proposal_id", "authority", "status", "decision", "order_group",
        } <= set(payload):
            raise ValueError("Result has unsupported fields")
        if payload["order_group"] is not None:
            raise ValueError("OMS order group needs a typed child contract")
        decision = payload["decision"]
        if not isinstance(decision, Mapping) or status != decision.get("status"):
            raise ValueError("Decision status mismatch")
        allowed = {"status", "reason", "held_quantity"}
        if set(decision) - allowed:
            raise ValueError("Decision requires a typed Portfolio child contract")
        if status not in {"acquisition_cancellation_requested", "exit_fill_pending",
                          "protection_replacement_deferred", "rejected"}:
            raise ValueError("Unsupported no-order decision status")
        if ("reason" in decision and not isinstance(decision["reason"], str)) or (
            "held_quantity" in decision and type(decision["held_quantity"]) not in (int, float)
        ):
            raise ValueError("Decision scalar field has wrong type")
        result = {**common, "error": "",
                  "decision_reason": decision.get("reason", ""),
                  "held_quantity": (_decimal(decision["held_quantity"], "held_quantity")
                                    if "held_quantity" in decision else None)}
    return TradeProposalRows(parent, None, result)
