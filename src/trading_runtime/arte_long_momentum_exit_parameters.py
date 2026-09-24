"""Inactive normalized final-exit and ordered exit-route parameters v1."""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import (
    HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION,
)


_REVISIONS = frozenset((*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
_FINAL = frozenset({"qmd_score", "qmd_confidence", "require_macd_bearish", "exit_on_failed_breakout"})
_ROUTE = frozenset({"route_id", "name", "category", "mechanism", "action", "priority",
                    "enabled", "protected", "summary", "settings"})
_BEARISH_SETTINGS = frozenset({"qmd_score", "qmd_confidence", "require_macd_bearish"})
_MECHANISMS = frozenset({"protective_stop", "failed_breakout", "bearish_qmd_macd"})
_KEY = (("assignment_id", "String"), ("strategy_id", "String"),
        ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"), ("session", "Date"))
TABLES = (
    TableContract("trading_assignment_long_momentum_final_exit_v1",
                  _KEY + (("qmd_score", "Float64"), ("qmd_confidence", "Float64"),
                          ("require_macd_bearish", "UInt8"), ("exit_on_failed_breakout", "UInt8"),
                          ("route_count", "UInt32"), ("route_hash", "FixedString(64)"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id"),
    TableContract("trading_assignment_long_momentum_exit_route_v1",
                  _KEY + (("ordinal", "UInt32"), ("route_id", "String"),
                          ("name", "String"), ("category", "LowCardinality(String)"),
                          ("mechanism", "LowCardinality(String)"),
                          ("action", "LowCardinality(String)"), ("priority", "UInt8"),
                          ("enabled", "UInt8"), ("protected", "UInt8"), ("summary", "String"),
                          ("setting_qmd_score", "Nullable(Float64)"),
                          ("setting_qmd_confidence", "Nullable(Float64)"),
                          ("setting_require_macd_bearish", "Nullable(UInt8)"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id, ordinal"),
)


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _digest(row)}


def _finite(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def project_exit_parameters(
    final_exit: Mapping[str, Any], routes: list[dict[str, Any]], *,
    assignment_id: str, strategy_id: str, strategy_revision: int,
    snapshot_id: str, session: str,
) -> dict[str, list[dict[str, Any]]]:
    if (not assignment_id or strategy_id != STRATEGY_ID
            or type(strategy_revision) is not int or strategy_revision not in _REVISIONS):
        raise ValueError("exit parameter strategy identity/revision is unsupported")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("exit parameter session is invalid")
    if not isinstance(final_exit, Mapping) or set(final_exit) != _FINAL:
        raise ValueError("final exit has missing or unmodeled fields")
    if any(type(final_exit[key]) is not bool for key in ("require_macd_bearish", "exit_on_failed_breakout")):
        raise ValueError("final exit flags must be boolean")
    if not isinstance(routes, list) or not routes:
        raise ValueError("exit routes must be a nonempty list")
    common = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                  strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    route_rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    protective = 0
    for ordinal, route in enumerate(routes):
        if not isinstance(route, Mapping) or set(route) != _ROUTE:
            raise ValueError("exit route has missing or unmodeled fields")
        if (not isinstance(route["route_id"], str) or not route["route_id"]
                or route["route_id"] in ids):
            raise ValueError("exit route identity is missing or duplicated")
        ids.add(route["route_id"])
        for key in ("name", "category", "mechanism", "action", "summary"):
            if not isinstance(route[key], str):
                raise ValueError(f"exit route {key} must be text")
        if route["mechanism"] not in _MECHANISMS or route["action"] != "close":
            raise ValueError("exit route mechanism/action is unsupported")
        if type(route["priority"]) is not int or not 0 <= route["priority"] <= 100:
            raise ValueError("exit route priority is invalid")
        if any(type(route[key]) is not bool for key in ("enabled", "protected")):
            raise ValueError("exit route flags must be boolean")
        if route["mechanism"] == "protective_stop":
            protective += 1
            if not route["enabled"] or not route["protected"] or route["priority"] != 100:
                raise ValueError("protective stop cannot be disabled")
        settings = route["settings"]
        expected = _BEARISH_SETTINGS if route["mechanism"] == "bearish_qmd_macd" else frozenset()
        if not isinstance(settings, Mapping) or set(settings) != expected:
            raise ValueError("exit route settings are missing or unmodeled")
        if expected and type(settings["require_macd_bearish"]) is not bool:
            raise ValueError("bearish route MACD setting must be boolean")
        route_rows.append(_seal({
            **common, "ordinal": ordinal, "route_id": route["route_id"],
            "name": route["name"], "category": route["category"],
            "mechanism": route["mechanism"], "action": route["action"],
            "priority": route["priority"], "enabled": int(route["enabled"]),
            "protected": int(route["protected"]), "summary": route["summary"],
            "setting_qmd_score": _finite(settings["qmd_score"], "route qmd_score") if expected else None,
            "setting_qmd_confidence": _finite(settings["qmd_confidence"], "route qmd_confidence") if expected else None,
            "setting_require_macd_bearish": int(settings["require_macd_bearish"]) if expected else None,
        }))
    if protective != 1:
        raise ValueError("exactly one protective stop route is required")
    final_row = _seal({
        **common, "qmd_score": _finite(final_exit["qmd_score"], "final qmd_score"),
        "qmd_confidence": _finite(final_exit["qmd_confidence"], "final qmd_confidence"),
        "require_macd_bearish": int(final_exit["require_macd_bearish"]),
        "exit_on_failed_breakout": int(final_exit["exit_on_failed_breakout"]),
        "route_count": len(route_rows),
        "route_hash": _digest([row["content_hash"] for row in route_rows]),
    })
    return {"final_exit": [final_row], "exit_route": route_rows}


def restore_exit_parameters(rows: Mapping[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if set(rows) != {"final_exit", "exit_route"} or len(rows["final_exit"]) != 1:
        raise ValueError("exit parameter typed families are incomplete")
    parent = rows["final_exit"][0]
    if set(parent) != {name for name, _ in TABLES[0].columns}:
        raise ValueError("final exit typed columns differ")
    common = {key: parent[key] for key, _ in _KEY}
    ordered = sorted(rows["exit_route"], key=lambda row: row["ordinal"])
    if [row["ordinal"] for row in ordered] != list(range(len(ordered))):
        raise ValueError("exit route ordinal sequence is invalid")
    if any(set(row) != {name for name, _ in TABLES[1].columns}
           or any(row[key] != value for key, value in common.items()) for row in ordered):
        raise ValueError("exit route typed columns or fence differ")
    final = {"qmd_score": parent["qmd_score"], "qmd_confidence": parent["qmd_confidence"],
             "require_macd_bearish": bool(parent["require_macd_bearish"]),
             "exit_on_failed_breakout": bool(parent["exit_on_failed_breakout"])}
    routes = []
    for row in ordered:
        settings = ({"qmd_score": row["setting_qmd_score"],
                     "qmd_confidence": row["setting_qmd_confidence"],
                     "require_macd_bearish": bool(row["setting_require_macd_bearish"])}
                    if row["mechanism"] == "bearish_qmd_macd" else {})
        routes.append({key: bool(row[key]) if key in {"enabled", "protected"} else row[key]
                       for key in _ROUTE - {"settings"}} | {"settings": settings})
    projected = project_exit_parameters(final, routes, **common)
    if projected["final_exit"] != rows["final_exit"] or projected["exit_route"] != ordered:
        raise ValueError("exit parameter content hash or family fence mismatch")
    return final, routes
