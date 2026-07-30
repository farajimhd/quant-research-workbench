from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from src.backend.trading_runtime_service import (
    get_strategy_definition,
    list_strategy_assignments,
    trading_journal,
)
from src.trading_runtime.portfolio import (
    PortfolioGroupPolicy,
    PortfolioPolicy,
    portfolio_policy_from_payload,
)
from src.trading_runtime.strategy_engine import (
    STRATEGY_ID,
    STRATEGY_REVISION,
    resolve_long_momentum_parameters,
)


CONFIGURATION_SECTIONS = {
    "strategy",
    "assignments",
    "portfolio",
    "oms",
    "accounts",
}
SUPPORTED_URGENCIES = {
    "passive_limit",
    "aggressive_limit",
    "market",
    "patient",
    "regular",
    "urgent",
    "very_urgent",
}


def configuration_draft() -> dict[str, Any]:
    current = trading_journal().trading_configuration_draft()
    if current is not None:
        return current
    draft = _default_draft()
    return trading_journal().save_trading_configuration_draft(draft)


def update_configuration_section(section: str, payload: Any) -> dict[str, Any]:
    if section not in CONFIGURATION_SECTIONS:
        raise ValueError(f"Unknown trading configuration section: {section}")
    draft = configuration_draft()
    candidate = {key: deepcopy(value) for key, value in draft.items() if key != "updated_at"}
    candidate[section] = deepcopy(payload)
    _validate_draft(candidate)
    return trading_journal().save_trading_configuration_draft(candidate)


def configuration_revisions() -> list[dict[str, Any]]:
    return trading_journal().trading_configuration_revisions()


def approved_configuration(*, required: bool = False) -> dict[str, Any] | None:
    result = trading_journal().approved_trading_configuration()
    if result is None and required:
        raise ValueError(
            "No approved trading configuration exists. Publish one from Configuration > Revisions."
        )
    return result


def publish_configuration(
    *,
    label: str,
    canvas_revision: str,
    canvas_profile: dict[str, Any],
) -> dict[str, Any]:
    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("An approval label is required")
    if not canvas_revision.strip() or not canvas_profile:
        raise ValueError("Publishing requires the current configured Canvas profile")
    container_count = sum(
        len(list(dict(state).get("openIds") or []))
        for state in dict(canvas_profile.get("workspaceStates") or {}).values()
    )
    if container_count <= 0:
        raise ValueError("Publishing requires at least one open container in the Canvas profile")
    draft = configuration_draft()
    candidate = {key: deepcopy(value) for key, value in draft.items() if key != "updated_at"}
    _validate_draft(candidate)
    payload = {
        "schema_version": 1,
        **candidate,
        "canvas": {
            "revision": canvas_revision.strip(),
            "profile": deepcopy(canvas_profile),
        },
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    existing = configuration_revisions()
    if existing and existing[0]["content_hash"] == content_hash:
        return existing[0]
    revision = int(existing[0]["revision"]) + 1 if existing else 1
    result = trading_journal().publish_trading_configuration(
        revision_id=str(uuid4()),
        revision=revision,
        label=normalized_label,
        content_hash=content_hash,
        payload=payload,
    )
    return result


def replay_configuration_snapshot() -> dict[str, Any]:
    approved = approved_configuration(required=True)
    assert approved is not None
    payload = deepcopy(approved["payload"])
    _validate_draft(payload)
    if not payload.get("canvas", {}).get("profile"):
        raise ValueError("The approved trading configuration does not contain a Canvas profile")
    return {
        "revision_id": approved["revision_id"],
        "revision": approved["revision"],
        "label": approved["label"],
        "content_hash": approved["content_hash"],
        "approved_at": approved["approved_at"],
        "payload": payload,
    }


def _default_draft() -> dict[str, Any]:
    definition = get_strategy_definition(STRATEGY_ID, STRATEGY_REVISION)
    source_assignments = list_strategy_assignments(active_only=True)
    account_ids = list(dict.fromkeys(str(row["account_id"]) for row in source_assignments))
    if not account_ids:
        account_ids = ["replay"]
    account_keys = {account_id: _account_key(account_id, index) for index, account_id in enumerate(account_ids)}
    assignments = [
        {
            "assignment_id": str(row["assignment_id"]),
            "account_key": account_keys[str(row["account_id"])],
            "ticker": str(row["ticker"]).upper(),
            "conid": int(row["conid"]),
            "status": str(row["status"]),
            "permissions": dict(row.get("permissions") or {}),
            "parameters": dict(row.get("parameters") or {}),
        }
        for row in source_assignments
    ]
    policy = asdict(PortfolioPolicy())
    return {
        "strategy": {
            "strategy_id": definition["strategy_id"],
            "revision": int(definition["revision"]),
            "name": definition["name"],
            "parameters": deepcopy(definition.get("config", {}).get("parameters") or {}),
        },
        "assignments": assignments,
        "portfolio": {
            "policies": [policy],
            "groups": [],
        },
        "oms": {
            "entry_urgency": "urgent",
            "exit_urgency": "very_urgent",
            "limit_offset_bps": 5.0,
            "tick_size": 0.01,
            "time_in_force": "DAY",
            "outside_rth": False,
            "protection": {
                "stop_method": "hybrid",
                "structure_buffer_bps": 8.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 1.5,
                "trailing_enabled": True,
            },
        },
        "accounts": {
            "bindings": [
                {
                    "account_key": account_keys[account_id],
                    "source_account_id": account_id,
                    "account_class": "simulated",
                    "base_currency": "USD",
                    "session_key": "replay",
                    "portfolio_policy_id": policy["policy_id"],
                    "enabled": True,
                    "modes": ["replay", "backtest", "backtest_debug"],
                }
                for account_id in account_ids
            ]
        },
    }


def _validate_draft(draft: dict[str, Any]) -> None:
    missing = CONFIGURATION_SECTIONS - set(draft)
    if missing:
        raise ValueError(f"Trading configuration is missing sections: {', '.join(sorted(missing))}")
    strategy = dict(draft["strategy"])
    definition = get_strategy_definition(
        str(strategy.get("strategy_id") or ""),
        int(strategy.get("revision") or 0),
    )
    if (
        definition["strategy_id"] != STRATEGY_ID
        or int(definition["revision"]) != STRATEGY_REVISION
    ):
        raise ValueError(
            "The selected strategy revision has no registered shared runtime implementation"
        )
    if not definition.get("enabled", True):
        raise ValueError("The selected strategy revision is disabled")
    resolve_long_momentum_parameters(dict(strategy.get("parameters") or {}))

    accounts = list(dict(draft["accounts"]).get("bindings") or [])
    if not accounts:
        raise ValueError("At least one account/session binding is required")
    account_keys = [str(row.get("account_key") or "").strip() for row in accounts]
    if any(not key for key in account_keys) or len(set(account_keys)) != len(account_keys):
        raise ValueError("Account binding keys must be present and unique")
    replay_accounts = {
        str(row["account_key"])
        for row in accounts
        if bool(row.get("enabled", True)) and "replay" in list(row.get("modes") or [])
    }
    if not replay_accounts:
        raise ValueError("At least one enabled Replay account binding is required")

    policies = [
        portfolio_policy_from_payload(dict(row))
        for row in list(dict(draft["portfolio"]).get("policies") or [])
    ]
    if not policies:
        raise ValueError("At least one portfolio/risk policy is required")
    policy_ids = {policy.policy_id for policy in policies}
    if len(policy_ids) != len(policies):
        raise ValueError("Portfolio policy ids must be unique")
    for row in accounts:
        if str(row.get("portfolio_policy_id") or "") not in policy_ids:
            raise ValueError(
                f"Account {row.get('account_key')} references an unknown portfolio policy"
            )
    for raw in list(dict(draft["portfolio"]).get("groups") or []):
        group = PortfolioGroupPolicy(
            group_id=str(raw.get("group_id") or ""),
            account_keys=tuple(str(value) for value in raw.get("account_keys") or ()),
            maximum_gross_exposure=float(raw.get("maximum_gross_exposure") or 0),
            maximum_ticker_exposure=float(raw.get("maximum_ticker_exposure") or 0),
        )
        unknown = set(group.account_keys) - set(account_keys)
        if unknown:
            raise ValueError(
                f"Portfolio group {group.group_id} references unknown accounts: {', '.join(sorted(unknown))}"
            )

    assignment_ids: set[str] = set()
    for row in list(draft["assignments"] or []):
        assignment_id = str(row.get("assignment_id") or "").strip()
        account_key = str(row.get("account_key") or "").strip()
        if not assignment_id or assignment_id in assignment_ids:
            raise ValueError("Assignment ids must be present and unique")
        if account_key not in replay_accounts:
            raise ValueError(f"Assignment {assignment_id} references an unavailable Replay account")
        if not str(row.get("ticker") or "").strip() or int(row.get("conid") or 0) <= 0:
            raise ValueError(f"Assignment {assignment_id} requires a ticker and positive conid")
        resolve_long_momentum_parameters(dict(row.get("parameters") or {}))
        assignment_ids.add(assignment_id)

    oms = dict(draft["oms"])
    if str(oms.get("entry_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS entry urgency is unsupported")
    if str(oms.get("exit_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS exit urgency is unsupported")
    if float(oms.get("limit_offset_bps") or 0) < 0:
        raise ValueError("OMS limit offset cannot be negative")
    if float(oms.get("tick_size") or 0) <= 0:
        raise ValueError("OMS tick size must be positive")
    if str(oms.get("time_in_force") or "") not in {"DAY", "GTC", "IOC", "OPG"}:
        raise ValueError("OMS time in force is unsupported")
    protection = dict(oms.get("protection") or {})
    if str(protection.get("stop_method") or "") not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Protection stop method is unsupported")


def merged_assignment_parameters(configuration: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(dict(configuration["strategy"].get("parameters") or {}))
    _deep_merge(base, dict(assignment.get("parameters") or {}))
    oms = dict(configuration["oms"])
    execution = base.setdefault("execution", {})
    execution.update(
        {
            "entry_urgency": oms["entry_urgency"],
            "exit_urgency": oms["exit_urgency"],
            "limit_offset_bps": float(oms["limit_offset_bps"]),
            "tick_size": float(oms["tick_size"]),
            "time_in_force": oms["time_in_force"],
            "outside_rth": bool(oms.get("outside_rth", False)),
        }
    )
    protection = dict(oms.get("protection") or {})
    stop = base.setdefault("protection", {}).setdefault("stop", {})
    stop.update(
        {
            "method": protection["stop_method"],
            "structure_buffer_bps": float(protection.get("structure_buffer_bps") or 0),
            "volatility_multiple": float(protection.get("volatility_multiple") or 0),
            "maximum_risk_pct": float(protection.get("maximum_risk_pct") or 0),
        }
    )
    base["protection"].setdefault("trailing", {})["enabled"] = bool(
        protection.get("trailing_enabled", True)
    )
    return resolve_long_momentum_parameters(base)


def _deep_merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _account_key(account_id: str, index: int) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in account_id)
    return normalized.strip("-") or f"account-{index + 1}"
