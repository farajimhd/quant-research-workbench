"""Inactive named-column children for active trade-proposal evidence.

Rows are pure projections. This module does not register a journal family or
permit any writer to insert them before a committed fence covers every child.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping
from uuid import UUID

from .arte_journal_schema import TableContract
from .journal_contract import canonical_json
from .order_management import OrderGroupSnapshot
from .portfolio import PortfolioDecision


METRICS = (
    "net_liquidation", "available_funds", "buying_power", "gross_exposure",
    "net_exposure", "reserved_notional", "open_risk", "daily_loss",
    "drawdown", "position_count",
)
LIVE_MARKET = frozenset({
    "authority", "ticker", "observed_at", "reference_price", "bid", "ask",
    "source_sequence", "age_ms", "freshness", "client_chart_observed_at",
    "client_chart_sequence",
})
REPLAY_MARKET = frozenset({
    "observed_at", "reference_price", "bid", "ask", "tick_size", "freshness",
    "source_sequence",
})
LIVE_METADATA = frozenset({
    "origin", "proposal_id", "proposal_authority", "action_id", "identity_revision",
    "market_snapshot", "bid", "ask", "tick_size", "quote_observed_at",
    "security_type", "currency", "conid", "exchange",
})
REPLAY_METADATA = frozenset({
    "origin", "proposal_id", "proposal_authority", "action_id", "market_snapshot",
    "identity_revision", "bid", "ask", "tick_size", "quote_observed_at",
})
ORDER_FIELDS = tuple(f.name for f in fields(OrderGroupSnapshot) if f.name not in {
    "client_order_ids", "broker_order_ids", "warning_message_ids", "protection_task",
})
ORDER_LISTS = ("client_order_ids", "broker_order_ids", "warning_message_ids")
DECISION_FIELDS = tuple(f.name for f in fields(PortfolioDecision) if f.name not in {
    "reasons", "metrics_before", "metrics_after",
})


def _kind(name: str) -> str:
    kinds = {
        **dict.fromkeys(("group_id", "intent_id", "account_id", "ticker", "action",
                         "state", "rejection_reason", "assignment_id", "fill_role",
                         "fill_exit_reason", "broker_order_id", "slice_id",
                         "execution_policy", "protection_profile", "r1_stop_error"), "String"),
        "policy_version": "UInt32",
        "submitted_at": "Nullable(DateTime64(6, 'UTC'))",
        "updated_at": "DateTime64(6, 'UTC')",
        **dict.fromkeys(("reentry_after_fill", "protection_delegated",
                         "entry_submission_closed"), "Bool"),
        **dict.fromkeys(("filled_quantity", "remaining_quantity",
                         "fill_cumulative_quantity", "fill_incremental_quantity",
                         "protection_required_quantity", "protection_coverage_quantity",
                         "high_water_price", "low_water_price"), "Decimal(38, 18)"),
        **dict.fromkeys(("decision_to_submit_ms", "current_limit_price",
                         "internal_reaction_ms", "r1_initial_stop",
                         "r1_actual_entry_average", "momentum_fill_average",
                         "momentum_entry_basis", "momentum_stop",
                         "tight_reentry_stop"), "Nullable(Decimal(38, 18))"),
    }
    return kinds[name]


KEY = (("run_id", "String"), ("record_id", "UUID"),
       ("event_time", "DateTime64(6, 'UTC')"))
TABLES = (
    TableContract("trading_trade_proposal_market_v1", KEY + (
        ("origin", "LowCardinality(String)"), ("action_id", "String"),
        ("identity_revision", "String"), ("tick_size", "Decimal(38, 18)"),
        ("security_type", "Nullable(String)"), ("currency", "Nullable(String)"),
        ("conid", "Nullable(UInt64)"), ("exchange", "Nullable(String)"),
        ("market_authority", "Nullable(String)"),
        ("market_observed_at", "DateTime64(6, 'UTC')"),
        ("reference_price", "Decimal(38, 18)"),
        ("market_bid", "Decimal(38, 18)"), ("market_ask", "Decimal(38, 18)"),
        ("scanner_sequence", "Nullable(UInt64)"),
        ("replay_source_sequence", "Nullable(String)"),
        ("age_ms", "Nullable(UInt32)"),
        ("freshness", "LowCardinality(String)"),
        ("client_chart_observed_at", "Nullable(DateTime64(6, 'UTC'))"),
        ("client_chart_sequence", "Nullable(String)"),
        ("market_sha256", "FixedString(64)"),
    ), "toYYYYMM(event_time)", "run_id, record_id"),
    TableContract("trading_trade_proposal_decision_v1", KEY + tuple(
        (name, "DateTime64(6, 'UTC')" if name == "decided_at" else
         "Int32" if name == "policy_revision" else
         "Decimal(38, 18)" if name in {"requested_quantity", "approved_quantity", "approved_notional", "planned_loss"}
         else "String") for name in DECISION_FIELDS
    ), "toYYYYMM(event_time)", "run_id, record_id"),
    TableContract("trading_trade_proposal_metric_v1", KEY + (
        ("phase", "LowCardinality(String)"),
    ) + tuple((name, "Decimal(38, 18)") for name in METRICS),
        "toYYYYMM(event_time)", "run_id, record_id, phase"),
    TableContract("trading_trade_proposal_reason_v1", KEY + (
        ("ordinal", "UInt32"), ("reason", "String"),
    ), "toYYYYMM(event_time)", "run_id, record_id, ordinal"),
    TableContract("trading_trade_proposal_order_group_v1", KEY + tuple(
        (name, _kind(name)) for name in ORDER_FIELDS
    ), "toYYYYMM(event_time)", "run_id, record_id"),
    TableContract("trading_trade_proposal_order_id_v1", KEY + (
        ("kind", "LowCardinality(String)"), ("ordinal", "UInt32"),
        ("value", "String"),
    ), "toYYYYMM(event_time)", "run_id, record_id, kind, ordinal"),
)


@dataclass(frozen=True, slots=True)
class ProposalChildren:
    market: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    metrics: tuple[dict[str, Any], ...] = ()
    reasons: tuple[dict[str, Any], ...] = ()
    order_group: dict[str, Any] | None = None
    order_ids: tuple[dict[str, Any], ...] = ()


def _exact(value: Any, names: frozenset[str] | set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != names:
        raise ValueError(f"Unsupported {label} fields")
    return value


def _number(value: Any, label: str) -> str:
    if type(value) not in (int, float):
        raise ValueError(f"Invalid {label} numeric value")
    try:
        number = Decimal(str(value))
        scaled = number.quantize(Decimal("0.000000000000000001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {label} numeric value") from exc
    if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
        raise ValueError(f"Invalid {label} numeric value")
    return format(scaled, "f")


def _time(value: Any, label: str) -> datetime:
    try:
        result = (value if isinstance(value, datetime)
                  else datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label} timestamp") from exc
    if result.tzinfo is None:
        raise ValueError(f"Invalid {label} timestamp")
    return result.astimezone(timezone.utc)


def _base(record: Any) -> dict[str, Any]:
    try:
        UUID(str(record.record_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Proposal record_id must be UUID") from exc
    return {"run_id": record.run_id, "record_id": str(record.record_id),
            "event_time": _time(record.event_time, "event")}


def _validate_row(row: Mapping[str, Any], table: TableContract) -> None:
    if set(row) != {name for name, _ in table.columns}:
        raise ValueError(f"{table.name} columns differ")
    for name, declared in table.columns:
        value = row[name]
        nullable = declared.startswith("Nullable(")
        if value is None and nullable:
            continue
        kind = declared.removeprefix("Nullable(").removesuffix(")") if nullable else declared
        if kind in {"String", "LowCardinality(String)"}:
            valid = isinstance(value, str)
        elif kind == "UUID":
            try:
                UUID(str(value))
                valid = True
            except (TypeError, ValueError):
                valid = False
        elif kind == "DateTime64(6, 'UTC')":
            valid = isinstance(value, datetime) and value.tzinfo is not None
        elif kind == "Decimal(38, 18)":
            valid = isinstance(value, str) and re.fullmatch(r"-?\d+\.\d{18}", value) is not None
        elif kind == "FixedString(64)":
            valid = isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
        elif kind in {"UInt32", "UInt64", "Int32"}:
            lower = -(2**31) if kind == "Int32" else 0
            upper = 2**31 if kind == "Int32" else 2**32 if kind == "UInt32" else 2**64
            valid = type(value) is int and lower <= value < upper
        elif kind == "Bool":
            valid = type(value) is bool
        else:
            raise ValueError(f"Unhandled {table.name}.{name} type")
        if not valid:
            raise ValueError(f"Invalid {table.name}.{name}")


def project_market_child(record: Any) -> ProposalChildren:
    """Accept exact live producer evidence or the closed replay sample shape."""
    base = _base(record)
    payload = record.payload
    if set(payload) - {"proposal_id", "authority", "status", "intent",
                        "correlation_id", "causation_id"} or not {
        "proposal_id", "authority", "status", "intent",
    } <= set(payload) or payload["status"] != "confirmed":
        raise ValueError("Unsupported confirmation envelope")
    metadata = payload.get("intent", {}).get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("Proposal metadata missing")
    origin = metadata.get("origin")
    if origin == "trade_proposal":
        _exact(metadata, LIVE_METADATA, "live proposal metadata")
        market = _exact(metadata["market_snapshot"], LIVE_MARKET, "live market snapshot")
    elif origin == "canvas_trade_proposal":
        _exact(metadata, REPLAY_METADATA, "replay proposal metadata")
        market = _exact(metadata["market_snapshot"], REPLAY_MARKET, "replay market snapshot")
    else:
        raise ValueError("Unsupported proposal origin")
    if metadata["proposal_id"] != payload.get("proposal_id") or metadata["proposal_authority"] != payload.get("authority"):
        raise ValueError("Proposal metadata identity mismatch")
    if metadata["bid"] != market.get("bid", 0) or metadata["ask"] != market.get("ask", 0):
        raise ValueError("Proposal quote evidence mismatch")
    if _time(metadata["quote_observed_at"], "quote") != _time(market["observed_at"], "market"):
        raise ValueError("Proposal quote clock differs from market")
    if origin == "trade_proposal" and market["ticker"] != payload["intent"]["ticker"]:
        raise ValueError("Proposal market ticker differs from intent")
    if origin == "canvas_trade_proposal" and metadata["tick_size"] != market["tick_size"]:
        raise ValueError("Proposal tick size differs from market")
    if market["freshness"] != "ready":
        raise ValueError("Proposal market not ready")
    row = {**base, "origin": origin, "action_id": metadata["action_id"],
           "identity_revision": metadata["identity_revision"],
           "tick_size": _number(metadata["tick_size"], "tick_size"),
           "security_type": metadata.get("security_type"),
           "currency": metadata.get("currency"), "conid": metadata.get("conid"),
           "exchange": metadata.get("exchange"),
           "market_authority": market.get("authority"),
           "market_observed_at": _time(market["observed_at"], "market"),
           "reference_price": _number(market["reference_price"], "reference price"),
           "market_bid": _number(market.get("bid", 0), "market bid"),
           "market_ask": _number(market.get("ask", 0), "market ask"),
           "scanner_sequence": market["source_sequence"] if origin == "trade_proposal" else None,
           "replay_source_sequence": market["source_sequence"] if origin == "canvas_trade_proposal" else None,
           "age_ms": market.get("age_ms"), "freshness": market["freshness"],
           "client_chart_observed_at": (_time(market["client_chart_observed_at"], "chart")
                                        if origin == "trade_proposal" else None),
           "client_chart_sequence": market.get("client_chart_sequence"),
           "market_sha256": sha256(canonical_json(market).encode()).hexdigest()}
    if (origin == "trade_proposal" and (
            type(row["conid"]) is not int or row["conid"] <= 0 or
            type(row["age_ms"]) is not int or row["age_ms"] < 0 or
            type(row["scanner_sequence"]) is not int or row["scanner_sequence"] <= 0 or
            not isinstance(row["client_chart_sequence"], str) or
            not row["client_chart_sequence"])) or (
            origin == "canvas_trade_proposal" and (
                not isinstance(row["replay_source_sequence"], str) or
                not row["replay_source_sequence"])):
        raise ValueError("Proposal market typed field mismatch")
    if Decimal(row["reference_price"]) <= 0 or Decimal(row["tick_size"]) <= 0:
        raise ValueError("Proposal market price or tick size is not positive")
    _validate_row(row, TABLES[0])
    return ProposalChildren(market=row)


def project_result_children(record: Any) -> ProposalChildren:
    """Project exact PortfolioDecision and OMS snapshots, rejecting dynamic tasks."""
    base = _base(record)
    payload = record.payload
    if set(payload) - {"proposal_id", "authority", "status", "decision",
                        "order_group", "correlation_id", "causation_id"} or not {
        "proposal_id", "authority", "status", "decision", "order_group",
    } <= set(payload):
        raise ValueError("Unsupported result envelope")
    decision = payload.get("decision")
    if not isinstance(decision, Mapping) or set(decision) != {f.name for f in fields(PortfolioDecision)}:
        raise ValueError("Result is not a complete PortfolioDecision")
    if payload.get("status") != decision["status"]:
        raise ValueError("Proposal/decision status mismatch")
    if decision["request_id"] != f"proposal:{payload['proposal_id']}":
        raise ValueError("Proposal decision request mismatch")
    decision_row = {**base, **{name: decision[name] for name in DECISION_FIELDS}}
    for name in ("requested_quantity", "approved_quantity", "approved_notional", "planned_loss"):
        decision_row[name] = _number(decision_row[name], name)
    decision_row["decided_at"] = _time(decision_row["decided_at"], "decision")
    _validate_row(decision_row, TABLES[1])
    metrics = []
    for phase in ("before", "after"):
        source = _exact(decision[f"metrics_{phase}"], set(METRICS), f"{phase} metrics")
        metrics.append({**base, "phase": phase,
                        **{name: _number(source[name], name) for name in METRICS}})
        _validate_row(metrics[-1], TABLES[2])
    reasons_source = decision["reasons"]
    if not isinstance(reasons_source, (list, tuple)) or any(not isinstance(x, str) for x in reasons_source):
        raise ValueError("Unsupported decision reasons")
    reasons = tuple({**base, "ordinal": i, "reason": value}
                    for i, value in enumerate(reasons_source))
    for row in reasons:
        _validate_row(row, TABLES[3])
    group = payload.get("order_group")
    if group is None:
        return ProposalChildren(decision=decision_row, metrics=tuple(metrics), reasons=reasons)
    _exact(group, {f.name for f in fields(OrderGroupSnapshot)}, "OMS order group")
    if group["protection_task"] is not None:
        raise ValueError("Live protection task cannot be journaled")
    if group["intent_id"] != decision["request_id"] or group["account_id"] != decision["account_id"]:
        raise ValueError("OMS group decision identity mismatch")
    order_row = {**base, **{name: group[name] for name in ORDER_FIELDS}}
    for name in ORDER_FIELDS:
        kind = _kind(name)
        value = order_row[name]
        if "Decimal(38, 18)" in kind:
            if value is not None:
                order_row[name] = _number(value, name)
            elif not kind.startswith("Nullable("):
                raise ValueError(f"Missing OMS {name}")
        elif "DateTime64(6, 'UTC')" in kind:
            if value is not None:
                order_row[name] = _time(value, name)
            elif not kind.startswith("Nullable("):
                raise ValueError(f"Missing OMS {name}")
        elif kind == "Bool" and type(value) is not bool:
            raise ValueError(f"Invalid OMS {name}")
        elif kind == "UInt32" and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid OMS {name}")
        elif kind == "String" and not isinstance(value, str):
            raise ValueError(f"Invalid OMS {name}")
    _validate_row(order_row, TABLES[4])
    ordered = []
    for kind in ORDER_LISTS:
        values = group[kind]
        if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) for x in values):
            raise ValueError("Unsupported OMS ordered IDs")
        ordered.extend({**base, "kind": kind, "ordinal": i, "value": value}
                       for i, value in enumerate(values))
    for row in ordered:
        _validate_row(row, TABLES[5])
    return ProposalChildren(decision=decision_row, metrics=tuple(metrics),
                            reasons=reasons, order_group=order_row,
                            order_ids=tuple(ordered))
