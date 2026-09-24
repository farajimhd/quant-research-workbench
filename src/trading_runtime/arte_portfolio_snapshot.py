"""Strict relational projection of the live Portfolio recovery snapshot.

This module has no persistence authority. The writer must publish these typed
families under one fenced snapshot before SQLite can be retired. In particular,
it never serializes an unknown dict into a catchall column.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any, Mapping

from src.trading_runtime.arte_portfolio_policy import (
    _policy_rows, load_portfolio_policy, publish_portfolio_policy,
)
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _insert, _literal, _rows, _wire_row,
)
from src.trading_runtime.journal_contract import canonical_json
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


_SNAPSHOT_FAMILIES = (
    ("trading_portfolio_snapshot_v1", "account"),
    ("trading_portfolio_disabled_strategy_v1", "disabled_strategies"),
    ("trading_portfolio_command_v1", "commands"),
    ("trading_portfolio_request_v1", "requests"),
    ("trading_portfolio_request_reason_v1", "request_reasons"),
    ("trading_portfolio_reservation_v1", "reservations"),
    ("trading_portfolio_allocation_v1", "allocations"),
    ("trading_portfolio_reconciliation_v1", "reconciliation"),
)
_SNAPSHOT_COMMIT = "trading_portfolio_snapshot_commit_v1"
_COUNT_COLUMNS = {
    "disabled_strategies": "disabled_strategy_count",
    "commands": "command_count",
    "requests": "request_count",
    "request_reasons": "request_reason_count",
    "reservations": "reservation_count",
    "allocations": "allocation_count",
    "reconciliation": "reconciliation_count",
}


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


def _snapshot_rows(run_id: str, account_id: str, state_revision: int,
                   snapshot_month: str, projected: PortfolioSnapshotRows,
                   *, wire: bool = True,
                   ) -> dict[str, tuple[dict[str, Any], ...]]:
    identity = {"run_id": run_id, "snapshot_month": snapshot_month,
                "account_id": account_id, "state_revision": state_revision}
    result = {}
    for table, attribute in _SNAPSHOT_FAMILIES:
        values = getattr(projected, attribute)
        source = (values,) if attribute == "account" else values
        result[table] = tuple((_wire_row(table, {**identity, **row}) if wire
                               else {**identity, **row}) for row in source)
    return result


def _state_hash(families: Mapping[str, tuple[dict[str, Any], ...]]) -> str:
    return sha256(canonical_json({name: sorted(families[name], key=canonical_json)
                                  for name, _ in _SNAPSHOT_FAMILIES})
                  .encode("utf-8")).hexdigest()


def _stored_rows(client: Any, table: str, run_id: str, account_id: str,
                 state_revision: int) -> list[dict[str, Any]]:
    columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
    return _rows(client, f"SELECT {columns} FROM arte.{table} "
                 f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
                 f"AND state_revision={state_revision} FORMAT JSONEachRow")


def _canonical_family(table: str, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]
                      ) -> list[dict[str, Any]]:
    # ClickHouse renders DateTime64 without a timezone suffix. It is UTC by
    # column contract; reattach that authority before canonical hashing.
    from src.trading_runtime.arte_journal_writer import _canonical_typed_content

    return sorted((_canonical_typed_content(table, row, stored_utc=True)
                   for row in rows), key=canonical_json)


def _aware_utc(value: str | None) -> str | None:
    return None if value is None else value.replace(" ", "T") + "+00:00"


def _restore_state(families: Mapping[str, tuple[dict[str, Any], ...]],
                   policy: dict[str, Any] | None) -> dict[str, Any]:
    root = families["trading_portfolio_snapshot_v1"][0]
    account_id = root["account_id"]
    account_key = root["account_key"]
    identity = {"run_id", "snapshot_month", "account_id", "state_revision"}

    def children(table: str) -> list[dict[str, Any]]:
        rows = families[table]
        if any(row["account_id"] != account_id
               or row["run_id"] != root["run_id"]
               or row["state_revision"] != root["state_revision"] for row in rows):
            raise RuntimeError("Portfolio snapshot child identity differs from its account")
        return [{key: value for key, value in row.items() if key not in identity}
                for row in rows]

    disabled = children("trading_portfolio_disabled_strategy_v1")
    if len({row["strategy_id"] for row in disabled}) != len(disabled):
        raise RuntimeError("Portfolio snapshot disabled strategies are duplicated")
    commands = sorted(children("trading_portfolio_command_v1"),
                      key=lambda row: row["ordinal"])
    if ([row["ordinal"] for row in commands] != list(range(len(commands)))
            or len({row["command_id"] for row in commands}) != len(commands)):
        raise RuntimeError("Portfolio snapshot command order is invalid")
    for row in commands:
        row.pop("ordinal")
        row["completed_at"] = _aware_utc(row["completed_at"])
        if row["completed_at"] is None:
            row.pop("completed_at")
        if row["error"] is None:
            row.pop("error")
    requests = children("trading_portfolio_request_v1")
    if len({row["request_id"] for row in requests}) != len(requests):
        raise RuntimeError("Portfolio snapshot requests are duplicated")
    by_request = {row["request_id"]: row for row in requests}
    for row in requests:
        row["requested_at"] = _aware_utc(row["requested_at"])
        row["last_validated_at"] = _aware_utc(row["last_validated_at"])
        row["reasons"] = []
    reasons: dict[str, list[dict[str, Any]]] = {}
    for row in children("trading_portfolio_request_reason_v1"):
        if row["request_id"] not in by_request:
            raise RuntimeError("Portfolio snapshot reason lacks a request")
        reasons.setdefault(row["request_id"], []).append(row)
    for request_id, rows in reasons.items():
        rows.sort(key=lambda row: row["ordinal"])
        if [row["ordinal"] for row in rows] != list(range(len(rows))):
            raise RuntimeError("Portfolio snapshot reason order is invalid")
        by_request[request_id]["reasons"] = [row["reason"] for row in rows]

    restored: dict[str, list[dict[str, Any]]] = {}
    for attribute, table, numeric, timestamp, key in (
        ("reservations", "trading_portfolio_reservation_v1", _RESERVATION_NUMERIC,
         "created_at", "reservation_id"),
        ("allocations", "trading_portfolio_allocation_v1", _ALLOCATION_NUMERIC,
         "updated_at", "allocation_id"),
        ("reconciliation", "trading_portfolio_reconciliation_v1",
         _RECONCILIATION_NUMERIC, "observed_at", "ticker"),
    ):
        rows = children(table)
        if (len({row[key] for row in rows}) != len(rows)
                or any(row["account_key"] != account_key for row in rows)):
            raise RuntimeError(f"Portfolio snapshot {attribute} identity is invalid")
        for row in rows:
            if attribute != "reconciliation":
                row["account_id"] = account_id
            row[timestamp] = _aware_utc(row[timestamp])
            for name in numeric:
                row[name] = float(row[name])
        restored[attribute] = rows
    return {
        "account_key": account_key, "control_mode": root["control_mode"],
        "sync_state": root["sync_state"], "snapshot_id": root["snapshot_id"],
        "observed_at": _aware_utc(root["observed_at"]),
        "stale_reason": root["stale_reason"],
        "peak_net_liquidation": float(root["peak_net_liquidation"]),
        "realized_pnl_baseline": (float(root["realized_pnl_baseline"])
                                  if root["realized_pnl_baseline"] is not None else None),
        "selected_policy": policy,
        "disabled_strategy_allocations": sorted(row["strategy_id"] for row in disabled),
        "pending_operational_commands": commands,
        "pending_entry_requests": by_request,
        **restored,
    }


def publish_portfolio_snapshot(
    client: Any, *, run_id: str, account_id: str, state_revision: int,
    snapshot_at: datetime, state: Mapping[str, Any],
) -> str:
    """Worker-lane publication, retry-safe with a last-written commit fence.

    The caller must own a Keeper-fenced account writer and supply its strictly
    increasing journal state revision. Never invoke on a realtime callback.
    """
    if (not run_id or not account_id or type(state_revision) is not int
            or state_revision < 1 or snapshot_at.tzinfo is None):
        raise ValueError("Portfolio snapshot needs a causal writer identity")
    projected = project_portfolio_snapshot(account_id, state)
    selected = state["selected_policy"]
    if selected is not None:
        policy = portfolio_policy_from_payload(selected)
        if publish_portfolio_policy(client, policy) != projected.account["selected_policy_hash"]:
            raise RuntimeError("Selected portfolio policy differs from its catalog")
    month = snapshot_at.astimezone(timezone.utc).date().replace(day=1).isoformat()
    families = _snapshot_rows(run_id, account_id, state_revision, month, projected)
    source_families = _snapshot_rows(run_id, account_id, state_revision, month,
                                    projected, wire=False)
    digest = _state_hash(families)
    existing_fence = _stored_rows(client, _SNAPSHOT_COMMIT, run_id, account_id,
                                  state_revision)
    if existing_fence:
        loaded = load_portfolio_snapshot(client, run_id=run_id,
                                         account_id=account_id,
                                         state_revision=state_revision)
        if loaded is None or loaded["state_hash"] != digest:
            raise RuntimeError("Portfolio snapshot revision has conflicting content")
        return digest
    for table, expected in families.items():
        actual = _stored_rows(client, table, run_id, account_id, state_revision)
        if actual and _canonical_family(table, actual) != _canonical_family(table, expected):
            raise RuntimeError(f"Portfolio snapshot {table} has conflicting partial rows")
        if not actual and expected:
            _insert(client, table, source_families[table],
                    f"portfolio-state:{run_id}:{account_id}:{state_revision}:{table}")
            actual = _stored_rows(client, table, run_id, account_id, state_revision)
        if _canonical_family(table, actual) != _canonical_family(table, expected):
            raise RuntimeError(f"Portfolio snapshot {table} did not become durable")
    fence = {"run_id": run_id, "snapshot_month": month, "account_id": account_id,
             "state_revision": state_revision, "state_hash": digest,
             **{_COUNT_COLUMNS[attribute]: len(families[table])
                for table, attribute in _SNAPSHOT_FAMILIES if attribute != "account"},
             "committed_at": datetime.now(timezone.utc).isoformat()}
    if set(fence) != {name for name, _ in _CONTRACTS[_SNAPSHOT_COMMIT].columns}:
        raise RuntimeError("Portfolio snapshot fence lacks a typed family count")
    _insert(client, _SNAPSHOT_COMMIT, (fence,),
            f"portfolio-state:{run_id}:{account_id}:{state_revision}:commit")
    loaded = load_portfolio_snapshot(client, run_id=run_id, account_id=account_id,
                                     state_revision=state_revision)
    if loaded is None or loaded["state_hash"] != digest:
        raise RuntimeError("Portfolio snapshot fence did not become durable")
    return digest


def load_portfolio_snapshot(
    client: Any, *, run_id: str, account_id: str, state_revision: int,
) -> dict[str, Any] | None:
    """Cold-read exactly one committed, content-verified recovery revision."""
    if not run_id or not account_id or type(state_revision) is not int or state_revision < 1:
        raise ValueError("Portfolio snapshot identity is invalid")
    fences = _stored_rows(client, _SNAPSHOT_COMMIT, run_id, account_id, state_revision)
    if not fences:
        return None
    if len(fences) != 1:
        raise RuntimeError("Portfolio snapshot has duplicate commit fences")
    fence = fences[0]
    families: dict[str, tuple[dict[str, Any], ...]] = {}
    for table, attribute in _SNAPSHOT_FAMILIES:
        actual = _stored_rows(client, table, run_id, account_id, state_revision)
        count_name = _COUNT_COLUMNS.get(attribute)
        if (attribute == "account" and len(actual) != 1
                or attribute != "account" and len(actual) != int(fence[count_name])):
            raise RuntimeError(f"Portfolio snapshot {table} differs from its fence count")
        families[table] = tuple(_canonical_family(table, actual))
    if _state_hash(families) != str(fence["state_hash"]):
        raise RuntimeError("Portfolio snapshot content differs from its fence")
    root = families["trading_portfolio_snapshot_v1"][0]
    if any(row["snapshot_month"] != fence["snapshot_month"]
           for rows in families.values() for row in rows):
        raise RuntimeError("Portfolio snapshot month differs from its fence")
    policy = None
    if root["selected_policy_hash"] is not None:
        selected = load_portfolio_policy(client, root["selected_policy_hash"])
        if selected is None:
            raise RuntimeError("Portfolio snapshot selected policy is not committed")
        from dataclasses import asdict
        policy = {**asdict(selected), "identity": selected.identity}
    return {"state_hash": str(fence["state_hash"]),
            "state_revision": state_revision, "state": _restore_state(families, policy),
            "families": families}
