"""Inactive typed recovery of the Early Squeeze momentum purchase ledger only.

This does not recover the whole squeeze entry or breakout state. It is a
closed projection of purchase requests and successor consumed-level keys.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_IDENTITY = (("run_id", "String"), ("assignment_id", "String"),
             ("state_revision", "UInt64"), ("session", "Date"))
LEDGER_TABLE = TableContract(
    "trading_assignment_squeeze_purchase_ledger_v1",
    _IDENTITY + (("entry_present", "Bool"), ("breakout_present", "Bool"),
                 ("momentum_requests_present", "Bool"),
                 ("midpoint_add_requests_present", "Bool"),
                 ("successor_added_levels_present", "Bool"),
                 ("momentum_request_count", "UInt32"),
                 ("midpoint_request_count", "UInt32"),
                 ("successor_added_level_count", "UInt32"),
                 ("request_set_hash", "FixedString(64)"),
                 ("level_set_hash", "FixedString(64)"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision",
)
REQUEST_TABLE = TableContract(
    "trading_assignment_squeeze_purchase_request_v1",
    _IDENTITY + (("request_kind", "LowCardinality(String)"),
                 ("intent_id", "String"), ("lifecycle", "Nullable(Float64)"),
                 ("episode_id", "Nullable(Float64)"), ("filled", "Bool"),
                 ("terminal", "Bool"), ("key_count", "UInt32"),
                 ("key_set_hash", "FixedString(64)"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision, request_kind, intent_id",
)
KEY_TABLE = TableContract(
    "trading_assignment_squeeze_purchase_request_key_v1",
    _IDENTITY + (("request_kind", "LowCardinality(String)"),
                 ("intent_id", "String"), ("ordinal", "UInt32"),
                 ("unified_level_id", "String"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision, request_kind, intent_id, ordinal",
)
ADDED_LEVEL_TABLE = TableContract(
    "trading_assignment_squeeze_successor_added_level_v1",
    _IDENTITY + (("ordinal", "UInt32"), ("unified_level_id", "String"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision, ordinal",
)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _identity(*, run_id: str, assignment_id: str, state_revision: int,
              session: str) -> dict[str, Any]:
    if (not isinstance(run_id, str) or not run_id or not isinstance(assignment_id, str)
            or not assignment_id or type(state_revision) is not int or state_revision < 0):
        raise ValueError("invalid squeeze purchase identity")
    if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
        raise ValueError("invalid squeeze purchase session")
    return dict(run_id=run_id, assignment_id=assignment_id,
                state_revision=state_revision, session=session)


def _number(value: Any, name: str, *, nullable: bool = False) -> float | None:
    if nullable and value is None:
        return None
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be finite numeric")
    return float(value)


def _level_keys(values: Any) -> list[str]:
    if (not isinstance(values, list) or len(values) > 4096
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))):
        raise ValueError("level keys must be a bounded unique string list")
    return values


def _rows_for_request(kind: str, intent_id: str, request: Mapping[str, Any],
                      identity: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(intent_id, str) or not intent_id or not isinstance(request, Mapping):
        raise ValueError("invalid squeeze purchase request")
    if set(request) != {"lifecycle", "keys", "episode_id", "filled", "terminal"}:
        raise ValueError("squeeze purchase request has missing or unmodeled fields")
    if type(request["filled"]) is not bool or type(request["terminal"]) is not bool:
        raise ValueError("squeeze purchase request flags must be bool")
    keys = _level_keys(request["keys"])
    children = [_seal({**identity, "request_kind": kind, "intent_id": intent_id,
                       "ordinal": ordinal, "unified_level_id": key})
                for ordinal, key in enumerate(keys)]
    row = _seal({**identity, "request_kind": kind, "intent_id": intent_id,
                 "lifecycle": _number(request["lifecycle"], "lifecycle"),
                 "episode_id": _number(request["episode_id"], "episode_id", nullable=True),
                 "filled": request["filled"], "terminal": request["terminal"],
                 "key_count": len(children), "key_set_hash": _hash(children)})
    return row, children


def project_squeeze_purchase_state(state: Mapping[str, Any], *, run_id: str,
                                   assignment_id: str, state_revision: int,
                                   session: str) -> dict[str, Any]:
    """Project only the request-ledger keys from a complete assignment state."""
    identity = _identity(run_id=run_id, assignment_id=assignment_id,
                         state_revision=state_revision, session=session)
    if not isinstance(state, Mapping):
        raise ValueError("assignment state must be mapping")
    entry_present = "squeeze_entry" in state
    breakout_present = "squeeze_breakout" in state
    entry = state.get("squeeze_entry", {})
    breakout = state.get("squeeze_breakout", {})
    if not isinstance(entry, Mapping) or not isinstance(breakout, Mapping):
        raise ValueError("squeeze state families must be mappings")
    request_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    counts = {}
    for kind, source_key in (("momentum", "momentum_requests"),
                             ("midpoint_add", "midpoint_add_requests")):
        requests = breakout.get(source_key, {})
        if not isinstance(requests, Mapping) or len(requests) > 4096:
            raise ValueError("request ledger must be bounded mapping")
        counts[kind] = len(requests)
        for intent_id in sorted(requests):
            row, children = _rows_for_request(kind, intent_id, requests[intent_id], identity)
            request_rows.append(row)
            key_rows.extend(children)
    level_keys = _level_keys(entry.get("successor_added_levels", []))
    level_rows = [_seal({**identity, "ordinal": ordinal, "unified_level_id": key})
                  for ordinal, key in enumerate(level_keys)]
    ledger = _seal({**identity, "entry_present": entry_present,
                    "breakout_present": breakout_present,
                    "momentum_requests_present": "momentum_requests" in breakout,
                    "midpoint_add_requests_present": "midpoint_add_requests" in breakout,
                    "successor_added_levels_present": "successor_added_levels" in entry,
                    "momentum_request_count": counts["momentum"],
                    "midpoint_request_count": counts["midpoint_add"],
                    "successor_added_level_count": len(level_rows),
                    "request_set_hash": _hash({"requests": request_rows, "keys": key_rows}),
                    "level_set_hash": _hash(level_rows)})
    return dict(ledger=ledger, requests=request_rows, request_keys=key_rows,
                successor_added_levels=level_rows)


def _validate_row(row: Any, table: TableContract, identity: dict[str, Any]) -> None:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in table.columns}:
        raise ValueError(f"invalid {table.name} columns")
    if any(row[key] != value for key, value in identity.items()):
        raise ValueError("mixed squeeze purchase identity")
    if row != _seal({key: value for key, value in row.items() if key != "content_hash"}):
        raise ValueError("tampered squeeze purchase row")


def restore_squeeze_purchase_state(rows: Mapping[str, Any]) -> dict[str, Any]:
    """Restore only ledger-owned keys, rejecting partial or ambiguous row sets."""
    if not isinstance(rows, Mapping) or set(rows) != {
        "ledger", "requests", "request_keys", "successor_added_levels",
    }:
        raise ValueError("incomplete squeeze purchase row families")
    ledger = rows["ledger"]
    if not isinstance(ledger, Mapping):
        raise ValueError("invalid squeeze purchase ledger")
    identity = {key: ledger[key] for key, _ in _IDENTITY}
    _identity(**identity)
    _validate_row(ledger, LEDGER_TABLE, identity)
    requests = rows["requests"]
    keys = rows["request_keys"]
    levels = rows["successor_added_levels"]
    if not all(isinstance(group, list) for group in (requests, keys, levels)):
        raise ValueError("squeeze purchase children must be lists")
    for group, table in ((requests, REQUEST_TABLE), (keys, KEY_TABLE),
                         (levels, ADDED_LEVEL_TABLE)):
        for row in group:
            _validate_row(row, table, identity)
    if (ledger["request_set_hash"] != _hash({"requests": requests, "keys": keys})
            or ledger["level_set_hash"] != _hash(levels)):
        raise ValueError("incomplete squeeze purchase child set")
    result: dict[str, Any] = {}
    if ledger["entry_present"]:
        result["squeeze_entry"] = {}
    if ledger["breakout_present"]:
        result["squeeze_breakout"] = {}
    breakout = result.get("squeeze_breakout")
    for kind, source_key, count_key in (("momentum", "momentum_requests", "momentum_request_count"),
                                        ("midpoint_add", "midpoint_add_requests", "midpoint_request_count")):
        selected = [row for row in requests if row["request_kind"] == kind]
        if len(selected) != ledger[count_key] or selected != sorted(selected, key=lambda row: row["intent_id"]):
            raise ValueError("squeeze request count or order mismatch")
        if selected or ledger[f"{source_key}_present"]:
            if breakout is None:
                raise ValueError("request rows without breakout state")
            breakout[source_key] = {}
        for row in selected:
            intent_id = row["intent_id"]
            if intent_id in breakout[source_key]:
                raise ValueError("duplicate squeeze purchase intent")
            selected_keys = [key for key in keys if key["request_kind"] == kind
                             and key["intent_id"] == intent_id]
            if (len(selected_keys) != row["key_count"]
                    or any(key["ordinal"] != ordinal for ordinal, key in enumerate(selected_keys))
                    or _hash(selected_keys) != row["key_set_hash"]):
                raise ValueError("incomplete squeeze request keys")
            breakout[source_key][intent_id] = dict(
                lifecycle=row["lifecycle"],
                keys=[key["unified_level_id"] for key in selected_keys],
                episode_id=row["episode_id"], filled=row["filled"], terminal=row["terminal"])
    if len(levels) != ledger["successor_added_level_count"] or any(
        row["ordinal"] != ordinal for ordinal, row in enumerate(levels)
    ):
        raise ValueError("incomplete successor level keys")
    if levels or ledger["successor_added_levels_present"]:
        if "squeeze_entry" not in result:
            raise ValueError("successor levels without entry state")
        result["squeeze_entry"]["successor_added_levels"] = [row["unified_level_id"] for row in levels]
    if project_squeeze_purchase_state(result, **identity) != rows:
        raise ValueError("noncanonical squeeze purchase ledger")
    return result
