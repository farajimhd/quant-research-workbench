"""Strict relational projection of the live Portfolio recovery snapshot.

This module has no persistence authority. The writer must publish these typed
families under one fenced snapshot before SQLite can be retired. In particular,
it never serializes an unknown dict into a catchall column.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.portfolio import (
    PortfolioAllocationLot, PortfolioReconciliationDifference,
    PortfolioControlMode, PortfolioReservation, PortfolioSyncState,
    portfolio_policy_from_payload,
)


_ROOT_FIELDS = frozenset({
    "account_key", "control_mode", "sync_state", "snapshot_id", "observed_at",
    "stale_reason", "peak_net_liquidation", "realized_pnl_baseline",
    "selected_policy", "disabled_strategy_allocations",
    "pending_operational_commands", "pending_entry_requests", "reservations",
    "allocations", "reconciliation",
})
_RESERVATION_NUMERIC = frozenset({
    "quantity", "remaining_quantity", "reference_price", "reserved_notional",
    "reserved_planned_risk", "filled_quantity", "reserved_entry_fees",
    "cash_tranche_size", "cash_tranche_budget",
})
_ALLOCATION_NUMERIC = frozenset({
    "quantity", "average_price", "planned_risk", "realized_pnl",
})
_RECONCILIATION_NUMERIC = frozenset({
    "broker_quantity", "attributed_quantity", "unattributed_quantity",
})
_RESERVATION_INTEGER = frozenset({"admission_epoch", "cash_tranche_count", "cash_tranche_next"})
_ALLOCATION_INTEGER = frozenset({"strategy_revision"})
_COMMAND_FIELDS = frozenset({"command_id", "command", "reason", "status"})
_REQUEST_FIELDS = frozenset({
    "request_id", "ticker", "assignment_id", "requested_at",
    "last_validated_at", "reasons",
})


@dataclass(frozen=True, slots=True)
class PortfolioSnapshotRows:
    account: dict[str, Any]
    disabled_strategies: tuple[dict[str, Any], ...]
    commands: tuple[dict[str, Any], ...]
    requests: tuple[dict[str, Any], ...]
    request_reasons: tuple[dict[str, Any], ...]
    reservations: tuple[dict[str, Any], ...]
    allocations: tuple[dict[str, Any], ...]
    reconciliation: tuple[dict[str, Any], ...]


def _decimal(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("Portfolio snapshot measure cannot be boolean")
    try:
        with localcontext() as context:
            context.prec = 50
            number = Decimal(str(value))
            scaled = number.quantize(Decimal("0.000000000000000001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Portfolio snapshot measure is not Decimal(38,18)") from exc
    if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
        raise ValueError("Portfolio snapshot measure is not Decimal(38,18)")
    return format(scaled, ".18f")


def _time(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("Portfolio snapshot timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _model_row(raw: Any, model: type, numeric: frozenset[str],
               timestamp: str, integer: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {field.name for field in fields(model)}:
        raise ValueError(f"Portfolio {model.__name__} has unmodeled fields")
    row = dict(raw)
    for name in numeric:
        row[name] = _decimal(row[name])
    row[timestamp] = _time(row[timestamp])
    for name, value in row.items():
        if name in numeric or name == timestamp:
            continue
        if name in integer:
            if type(value) is not int or value < 0:
                raise ValueError(f"Portfolio {model.__name__}.{name} is not a nonnegative integer")
        elif not isinstance(value, str):
            raise ValueError(f"Portfolio {model.__name__}.{name} is not a string")
    return row


def project_portfolio_snapshot(account_id: str, state: Mapping[str, Any]) -> PortfolioSnapshotRows:
    """Reject unknown recovery fields and return only scalar, typed table rows."""
    if (not isinstance(account_id, str) or not account_id
            or not isinstance(state, Mapping) or set(state) != _ROOT_FIELDS):
        raise ValueError("Portfolio snapshot has missing or unmodeled root fields")
    if any(not isinstance(state[name], str) for name in (
            "account_key", "control_mode", "sync_state", "snapshot_id", "stale_reason")):
        raise ValueError("Portfolio snapshot has non-scalar root fields")
    selected = state["selected_policy"]
    policy_hash = None
    if selected is not None:
        if not isinstance(selected, Mapping):
            raise ValueError("Selected portfolio policy is not a typed mapping")
        policy_hash = _policy_rows(portfolio_policy_from_payload(selected))[0]
    account = {
        "account_id": account_id,
        "account_key": str(state["account_key"]),
        "control_mode": str(state["control_mode"]),
        "sync_state": str(state["sync_state"]),
        "snapshot_id": str(state["snapshot_id"]),
        "observed_at": _time(state["observed_at"], nullable=True),
        "stale_reason": str(state["stale_reason"]),
        "peak_net_liquidation": _decimal(state["peak_net_liquidation"]),
        "realized_pnl_baseline": (_decimal(state["realized_pnl_baseline"])
                                  if state["realized_pnl_baseline"] is not None else None),
        "selected_policy_hash": policy_hash,
    }
    if (not account["account_key"]
            or account["control_mode"] not in {item.value for item in PortfolioControlMode}
            or account["sync_state"] not in {item.value for item in PortfolioSyncState}):
        raise ValueError("Portfolio snapshot identity or state is empty")

    disabled = state["disabled_strategy_allocations"]
    if (not isinstance(disabled, list) or any(not isinstance(value, str) or not value
                                             for value in disabled)
            or len(disabled) != len(set(disabled))):
        raise ValueError("Disabled strategies are not a unique string list")
    disabled_rows = tuple({"account_id": account_id, "strategy_id": value}
                          for value in sorted(disabled))

    commands = state["pending_operational_commands"]
    if not isinstance(commands, list):
        raise ValueError("Portfolio commands are not a list")
    command_rows = []
    for ordinal, raw in enumerate(commands):
        if not isinstance(raw, Mapping):
            raise ValueError("Portfolio command is not typed")
        status = raw.get("status")
        expected = (_COMMAND_FIELDS | ({"error"} if status == "failed" else set())
                    | ({"completed_at"} if status == "completed" else set()))
        if (set(raw) != expected or status not in {"pending", "failed", "completed"}
                or raw.get("command") not in {"kill_entries", "emergency_flatten", "resume_entries"}
                or any(not isinstance(raw.get(key), str)
                       for key in expected - {"completed_at"})):
            raise ValueError("Portfolio command has unmodeled fields or status")
        if (not isinstance(raw["command_id"], str) or not raw["command_id"]
                or not isinstance(raw["reason"], str)
                or (status == "failed" and not isinstance(raw["error"], str))):
            raise ValueError("Portfolio command has unmodeled fields or status")
        row = {"account_id": account_id, "ordinal": ordinal, **raw}
        row.setdefault("error", None)
        row.setdefault("completed_at", None)
        if row["completed_at"] is not None:
            row["completed_at"] = _time(row["completed_at"])
        command_rows.append(row)
    if len({row["command_id"] for row in command_rows}) != len(command_rows):
        raise ValueError("Portfolio command IDs are duplicated")

    requests = state["pending_entry_requests"]
    if not isinstance(requests, Mapping):
        raise ValueError("Portfolio requests are not keyed")
    request_rows = []
    reason_rows = []
    for request_id, raw in sorted(requests.items()):
        if (not isinstance(request_id, str) or not request_id
                or not isinstance(raw, Mapping) or set(raw) != _REQUEST_FIELDS
                or raw["request_id"] != request_id
                or not isinstance(raw["ticker"], str)
                or not isinstance(raw["assignment_id"], str)
                or not isinstance(raw["reasons"], list)):
            raise ValueError("Portfolio request has unmodeled fields")
        request_rows.append({
            "account_id": account_id, "request_id": request_id,
            "ticker": str(raw["ticker"]), "assignment_id": str(raw["assignment_id"]),
            "requested_at": _time(raw["requested_at"]),
            "last_validated_at": _time(raw["last_validated_at"]),
        })
        for ordinal, reason in enumerate(raw["reasons"]):
            if not isinstance(reason, str) or not reason:
                raise ValueError("Portfolio request reason is invalid")
            reason_rows.append({"account_id": account_id, "request_id": request_id,
                                "ordinal": ordinal, "reason": reason})

    families = (
        ("reservations", PortfolioReservation, _RESERVATION_NUMERIC, "created_at",
         "reservation_id", _RESERVATION_INTEGER),
        ("allocations", PortfolioAllocationLot, _ALLOCATION_NUMERIC, "updated_at",
         "allocation_id", _ALLOCATION_INTEGER),
        ("reconciliation", PortfolioReconciliationDifference, _RECONCILIATION_NUMERIC,
         "observed_at", "ticker", frozenset()),
    )
    projected = {}
    for name, model, numeric, timestamp, identity, integer in families:
        source = state[name]
        if not isinstance(source, list):
            raise ValueError(f"Portfolio {name} is not a list")
        rows = tuple(_model_row(raw, model, numeric, timestamp, integer) for raw in source)
        if any(row.get("account_id", account_id) != account_id
               or row.get("account_key") != account["account_key"] for row in rows):
            raise ValueError(f"Portfolio {name} has another account")
        if len({str(row[identity]) for row in rows}) != len(rows):
            raise ValueError(f"Portfolio {name} identities are duplicated")
        projected[name] = tuple(sorted(rows, key=lambda row: str(row[identity])))
    return PortfolioSnapshotRows(account, disabled_rows, tuple(command_rows),
                                 tuple(request_rows), tuple(reason_rows),
                                 projected["reservations"], projected["allocations"],
                                 projected["reconciliation"])
