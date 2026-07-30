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
    default_entry_decision_rules,
    default_long_momentum_parameters,
    resolve_long_momentum_parameters,
    strategy_input_catalog,
)


CONFIGURATION_SCHEMA_VERSION = 3
CONFIGURATION_SECTIONS = {"strategy", "assignments", "portfolio", "oms", "accounts"}
SUPPORTED_URGENCIES = {
    "passive_limit",
    "aggressive_limit",
    "market",
    "patient",
    "regular",
    "urgent",
    "very_urgent",
}
SUPPORTED_MODES = {"replay", "backtest", "backtest_debug", "paper", "live"}


def capability_catalog() -> list[dict[str, Any]]:
    """Code-owned capability definitions with system defaults and UI metadata."""

    return [
        {
            "capability_id": "profit-pocket",
            "revision": 1,
            "name": "Profit pocket",
            "category": "position_management",
            "summary": "Reduce a winning position when momentum slows or a configured gain is reached.",
            "order_entry_action": True,
            "autonomy": ["manual", "confirm", "automatic"],
            "defaults": {
                "mode": "automatic",
                "trigger": "acceleration_slowdown",
                "minimum_gain_pct": 0.75,
                "quantity_fraction": 0.5,
                "minimum_remaining_quantity": 1.0,
            },
            "parameters": [
                _choice("mode", "Authority", "Who may trigger the reduction.", ["manual", "confirm", "automatic"]),
                _choice(
                    "trigger",
                    "Trigger",
                    "Evidence that makes a profit pocket eligible.",
                    ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"],
                ),
                _number("minimum_gain_pct", "Minimum gain", "Gain required before reducing.", "%", 0, 100, 0.05),
                _number("quantity_fraction", "Position to sell", "Fraction of the open position to reduce.", "%", 0.01, 1, 0.05, display="fraction"),
                _number("minimum_remaining_quantity", "Minimum remainder", "Do not leave a smaller residual position.", "shares", 0, 1_000_000, 1),
            ],
        },
        {
            "capability_id": "exit-watch-reenter",
            "revision": 1,
            "name": "Exit, watch & re-enter",
            "category": "position_lifecycle",
            "summary": "Exit weakness, keep observing, and re-enter only after a new causal confirmation.",
            "order_entry_action": True,
            "autonomy": ["manual", "confirm", "automatic"],
            "defaults": {
                "mode": "confirm",
                "cooldown_ms": 1_000,
                "maximum_attempts": 3,
                "require_new_confirmation": True,
                "swing_break_timeframe": "1s",
            },
            "parameters": [
                _choice("mode", "Authority", "Whether re-entry is proposed, confirmed, or automatic.", ["manual", "confirm", "automatic"]),
                _number("cooldown_ms", "Cooldown", "Minimum wait after exit before another entry.", "ms", 0, 3_600_000, 100),
                _number("maximum_attempts", "Maximum attempts", "Maximum re-entries in one assignment lifecycle.", "attempts", 0, 20, 1),
                _boolean("require_new_confirmation", "Require new confirmation", "Prevents re-entry from reusing stale evidence."),
                _choice("swing_break_timeframe", "Swing timeframe", "Causal swing structure used for re-entry.", ["100ms", "1s", "5s", "10s"]),
            ],
        },
        {
            "capability_id": "confirmed-pullback-add",
            "revision": 1,
            "name": "Confirmed pullback add",
            "category": "position_management",
            "summary": "Add only after a configured pullback and renewed bullish structure.",
            "order_entry_action": True,
            "autonomy": ["manual", "confirm", "automatic"],
            "defaults": {
                "mode": "confirm",
                "maximum_adds": 2,
                "add_fraction": 0.5,
                "require_bullish_choch": True,
            },
            "parameters": [
                _choice("mode", "Authority", "Who may authorize an add.", ["manual", "confirm", "automatic"]),
                _number("maximum_adds", "Maximum adds", "Maximum additions during one position lifecycle.", "adds", 0, 10, 1),
                _number("add_fraction", "Add size", "Fraction of the initial approved allocation requested for each add.", "%", 0.01, 2, 0.05, display="fraction"),
                _boolean("require_bullish_choch", "Require bullish change of character", "Requires renewed causal structure after the pullback."),
            ],
        },
        {
            "capability_id": "adaptive-protection",
            "revision": 1,
            "name": "Adaptive protection",
            "category": "protection",
            "summary": "Attach shared OMS protection and tighten it as the position becomes profitable.",
            "order_entry_action": False,
            "autonomy": ["automatic"],
            "defaults": {
                "mode": "automatic",
                "stop_method": "hybrid",
                "trailing_enabled": True,
                "move_to_break_even_gain_pct": 0.5,
            },
            "parameters": [
                _choice("stop_method", "Stop method", "How the initial invalidation is constructed.", ["structure", "volatility", "hybrid"]),
                _boolean("trailing_enabled", "Enable trailing", "Allow the shared OMS to tighten protection."),
                _number("move_to_break_even_gain_pct", "Break-even activation", "Gain required before break-even protection becomes eligible.", "%", 0, 100, 0.05),
            ],
        },
    ]


def configuration_draft() -> dict[str, Any]:
    current = trading_journal().trading_configuration_draft()
    if current is None:
        return trading_journal().save_trading_configuration_draft(_default_draft())
    migrated = _migrate_draft(current)
    if _without_timestamp(migrated) != _without_timestamp(current):
        return trading_journal().save_trading_configuration_draft(_without_timestamp(migrated))
    return migrated


def update_configuration_section(section: str, payload: Any) -> dict[str, Any]:
    if section not in CONFIGURATION_SECTIONS:
        raise ValueError(f"Unknown trading configuration section: {section}")
    draft = configuration_draft()
    candidate = _without_timestamp(draft)
    candidate[section] = deepcopy(payload)
    _validate_draft(candidate, require_runtime_ready=False)
    return trading_journal().save_trading_configuration_draft(candidate)


def configuration_revisions() -> list[dict[str, Any]]:
    return trading_journal().trading_configuration_revisions()


def approved_configuration(*, required: bool = False) -> dict[str, Any] | None:
    result = trading_journal().approved_trading_configuration()
    if result is None and required:
        raise ValueError("No approved trading configuration exists. Publish one from Configuration > Approved Releases.")
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
    candidate = _without_timestamp(configuration_draft())
    _validate_draft(candidate)
    payload = {
        **candidate,
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "canvas": {"revision": canvas_revision.strip(), "profile": deepcopy(canvas_profile)},
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    existing = configuration_revisions()
    if existing and existing[0]["content_hash"] == content_hash:
        return existing[0]
    revision = int(existing[0]["revision"]) + 1 if existing else 1
    return trading_journal().publish_trading_configuration(
        revision_id=str(uuid4()),
        revision=revision,
        label=normalized_label,
        content_hash=content_hash,
        payload=payload,
    )


def replay_configuration_snapshot() -> dict[str, Any]:
    approved = approved_configuration(required=True)
    assert approved is not None
    model = _migrate_draft(deepcopy(approved["payload"]))
    _validate_draft(model)
    if not model.get("canvas", {}).get("profile"):
        raise ValueError("The approved trading configuration does not contain a Canvas profile")
    runtime_payload = resolve_runtime_configuration(model, mode="replay")
    runtime_payload["canvas"] = deepcopy(model["canvas"])
    return {
        "revision_id": approved["revision_id"],
        "revision": approved["revision"],
        "label": approved["label"],
        "content_hash": approved["content_hash"],
        "approved_at": approved["approved_at"],
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "deployment_id": runtime_payload["deployment"]["deployment_id"],
        "configuration_model": model,
        "payload": runtime_payload,
    }


def resolve_runtime_configuration(model: dict[str, Any], *, mode: str, deployment_id: str = "") -> dict[str, Any]:
    """Resolve one approved deployment into the existing shared runtime contracts."""

    model = _migrate_draft(model)
    deployments = list(dict(model["assignments"]).get("deployments") or [])
    eligible = [
        row for row in deployments
        if bool(row.get("enabled", True)) and mode in set(row.get("modes") or [])
    ]
    deployment = next(
        (row for row in eligible if str(row.get("deployment_id")) == deployment_id),
        eligible[0] if eligible else None,
    )
    if deployment is None:
        raise ValueError(f"No enabled strategy deployment supports {mode}")
    profiles = {
        str(row["profile_id"]): row
        for row in dict(model["strategy"]).get("profiles") or []
    }
    profile = profiles.get(str(deployment.get("profile_id") or ""))
    if profile is None:
        raise ValueError(f"Deployment {deployment.get('deployment_id')} references an unknown Strategy Profile")
    oms_profiles = {
        str(row["profile_id"]): row
        for row in dict(model["oms"]).get("profiles") or []
    }
    oms = oms_profiles.get(str(deployment.get("oms_profile_id") or ""))
    if oms is None:
        raise ValueError(f"Deployment {deployment.get('deployment_id')} references an unknown OMS profile")
    mandates = [
        row for row in dict(model["portfolio"]).get("mandates") or []
        if str(row.get("deployment_id")) == str(deployment["deployment_id"])
        and bool(row.get("enabled", True))
    ]
    account_keys = {str(row["account_key"]) for row in mandates}
    bindings = [
        deepcopy(row) for row in dict(model["accounts"]).get("bindings") or []
        if str(row.get("account_key")) in account_keys
    ]
    mandate_by_account = {str(row["account_key"]): row for row in mandates}
    for binding in bindings:
        mandate = mandate_by_account[str(binding["account_key"])]
        binding["strategy_allocation"] = float(mandate.get("maximum_cash_fraction", 1.0))
    runtime_assignments = [
        deepcopy(row) for row in deployment.get("runtime_assignments") or []
        if str(row.get("account_key")) in account_keys
    ]
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "deployment": deepcopy(deployment),
        "strategy_profile": deepcopy(profile),
        "strategy": {
            "strategy_id": profile["definition_id"],
            "revision": int(profile["definition_revision"]),
            "name": profile["name"],
            "profile_id": profile["profile_id"],
            "profile_revision": int(profile.get("revision") or 1),
            "parameters": _parameters_with_capabilities(profile),
            "capabilities": deepcopy(profile.get("capabilities") or []),
        },
        "assignments": runtime_assignments,
        "portfolio": {
            "policies": deepcopy(dict(model["portfolio"]).get("policies") or []),
            "groups": deepcopy(dict(model["portfolio"]).get("groups") or []),
            "mandates": deepcopy(mandates),
        },
        "oms": deepcopy(dict(oms.get("settings") or {})),
        "accounts": {"bindings": bindings},
    }


def _default_draft() -> dict[str, Any]:
    definition = get_strategy_definition(STRATEGY_ID, STRATEGY_REVISION)
    parameters = deepcopy(definition.get("config", {}).get("parameters") or default_long_momentum_parameters())
    system_profiles = [
        _strategy_profile(
            "long-momentum-balanced",
            "Long Momentum · Balanced",
            "System starting point balancing breakout confirmation, protection, and re-entry.",
            parameters,
            origin="system",
        ),
        _strategy_profile(
            "long-momentum-conservative",
            "Long Momentum · Conservative",
            "Higher confirmation and smaller initial size for controlled evaluation.",
            _overrides(parameters, {
                "entry_rules.confirmation.minimum_score": 0.65,
                "sizing.request_value": 50.0,
                "sizing.initial_quantity": 50.0,
                "sizing.maximum_position_quantity": 150.0,
                "reentry.maximum_attempts": 1,
            }),
            origin="system",
        ),
        _strategy_profile(
            "long-momentum-semi-auto",
            "Long Momentum · Semi-automatic",
            "Operator-confirmed entries with configurable automated position management.",
            _overrides(parameters, {"sizing.request_value": 100.0, "sizing.initial_quantity": 100.0}),
            origin="system",
            capability_modes={"profit-pocket": "confirm", "exit-watch-reenter": "confirm", "confirmed-pullback-add": "confirm"},
        ),
    ]
    source_assignments = list_strategy_assignments(active_only=True)
    account_ids = list(dict.fromkeys(str(row["account_id"]) for row in source_assignments)) or ["replay"]
    account_keys = {account_id: _account_key(account_id, index) for index, account_id in enumerate(account_ids)}
    runtime_assignments = [
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
    bindings = [
        {
            "account_key": account_keys[account_id],
            "name": "Replay account" if account_id == "replay" else account_id,
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
    mandates = [
        {
            "mandate_id": f"balanced-{binding['account_key']}",
            "deployment_id": "balanced-replay",
            "account_key": binding["account_key"],
            "enabled": True,
            "maximum_cash_fraction": 1.0,
            "maximum_planned_risk_fraction": 0.01,
            "maximum_positions": 10,
            "priority": 50,
            "autonomy": "confirm",
            "allow_replacement": False,
            "minimum_replacement_improvement_pct": 20.0,
        }
        for binding in bindings
    ]
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "strategy": {
            "definitions": [{
                "strategy_id": definition["strategy_id"],
                "revision": int(definition["revision"]),
                "name": definition["name"],
                "automatic": bool(definition.get("automatic", True)),
            }],
            "capability_catalog": capability_catalog(),
            "input_catalog": strategy_input_catalog(),
            "profiles": system_profiles,
        },
        "assignments": {
            "deployments": [{
                "deployment_id": "balanced-replay",
                "name": "Balanced Replay",
                "description": "Approved balanced strategy prepared for historical simulation.",
                "profile_id": "long-momentum-balanced",
                "oms_profile_id": "adaptive-regular",
                "mandate_ids": [row["mandate_id"] for row in mandates],
                "enabled": True,
                "modes": ["replay", "backtest", "backtest_debug"],
                "runtime_assignments": runtime_assignments,
            }]
        },
        "portfolio": {"policies": [policy], "groups": [], "mandates": mandates},
        "oms": {"profiles": [_default_oms_profile()]},
        "accounts": {"bindings": bindings},
    }


def _validate_draft(draft: dict[str, Any], *, require_runtime_ready: bool = True) -> None:
    missing = CONFIGURATION_SECTIONS - set(draft)
    if missing:
        raise ValueError(f"Trading configuration is missing sections: {', '.join(sorted(missing))}")
    strategy = dict(draft["strategy"])
    catalog = {str(row["capability_id"]): row for row in strategy.get("capability_catalog") or capability_catalog()}
    profiles = list(strategy.get("profiles") or [])
    if not profiles:
        raise ValueError("At least one Strategy Profile is required")
    profile_ids = _unique_ids(profiles, "profile_id", "Strategy Profile")
    for profile in profiles:
        definition = get_strategy_definition(
            str(profile.get("definition_id") or ""),
            int(profile.get("definition_revision") or 0),
        )
        if not definition.get("enabled", True):
            raise ValueError(f"Strategy definition for {profile.get('name')} is disabled")
        resolve_long_momentum_parameters(dict(profile.get("parameters") or {}))
        for binding in profile.get("capabilities") or []:
            capability_id = str(binding.get("capability_id") or "")
            if capability_id not in catalog:
                raise ValueError(f"Strategy Profile {profile.get('name')} references unknown capability {capability_id}")
            _validate_capability_settings(catalog[capability_id], dict(binding.get("settings") or {}))

    accounts = list(dict(draft["accounts"]).get("bindings") or [])
    if not accounts:
        raise ValueError("At least one account/session binding is required")
    account_keys = _unique_ids(accounts, "account_key", "Account")
    for row in accounts:
        modes = set(row.get("modes") or [])
        if not modes or not modes <= SUPPORTED_MODES:
            raise ValueError(f"Account {row.get('account_key')} has unsupported runtime modes")

    policies = [portfolio_policy_from_payload(dict(row)) for row in dict(draft["portfolio"]).get("policies") or []]
    if not policies:
        raise ValueError("At least one portfolio/risk policy is required")
    policy_ids = {policy.policy_id for policy in policies}
    if len(policy_ids) != len(policies):
        raise ValueError("Portfolio policy ids must be unique")
    for row in accounts:
        if str(row.get("portfolio_policy_id") or "") not in policy_ids:
            raise ValueError(f"Account {row.get('account_key')} references an unknown portfolio policy")

    oms_profiles = list(dict(draft["oms"]).get("profiles") or [])
    oms_ids = _unique_ids(oms_profiles, "profile_id", "OMS profile")
    for row in oms_profiles:
        _validate_oms_settings(dict(row.get("settings") or {}))

    deployments = list(dict(draft["assignments"]).get("deployments") or [])
    if require_runtime_ready and not deployments:
        raise ValueError("At least one Strategy Deployment is required")
    deployment_ids = _unique_ids(deployments, "deployment_id", "Strategy Deployment")
    mandates = list(dict(draft["portfolio"]).get("mandates") or [])
    mandate_ids = _unique_ids(mandates, "mandate_id", "Strategy-account mandate")
    for mandate in mandates:
        if str(mandate.get("deployment_id") or "") not in deployment_ids:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown deployment")
        if str(mandate.get("account_key") or "") not in account_keys:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown account")
        fraction = float(mandate.get("maximum_cash_fraction") or 0)
        if not 0 < fraction <= 1:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} maximum cash must be between 0 and 100 percent")
        if str(mandate.get("autonomy") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has unsupported autonomy")
    for deployment in deployments:
        if str(deployment.get("profile_id") or "") not in profile_ids:
            raise ValueError(f"Deployment {deployment.get('deployment_id')} references an unknown Strategy Profile")
        if str(deployment.get("oms_profile_id") or "") not in oms_ids:
            raise ValueError(f"Deployment {deployment.get('deployment_id')} references an unknown OMS profile")
        referenced_mandates = [
            row for row in mandates
            if str(row.get("deployment_id")) == str(deployment.get("deployment_id"))
        ]
        if require_runtime_ready and not referenced_mandates:
            raise ValueError(f"Deployment {deployment.get('deployment_id')} requires at least one account mandate")
        for assignment in deployment.get("runtime_assignments") or []:
            if str(assignment.get("account_key") or "") not in account_keys:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} references an unknown account")
            if not str(assignment.get("ticker") or "").strip() or int(assignment.get("conid") or 0) <= 0:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} requires ticker and conid")
    if require_runtime_ready and not any(
        bool(row.get("enabled", True)) and "replay" in set(row.get("modes") or [])
        for row in deployments
    ):
        raise ValueError("At least one enabled Strategy Deployment must support Replay")
    for raw in dict(draft["portfolio"]).get("groups") or []:
        group = PortfolioGroupPolicy(
            group_id=str(raw.get("group_id") or ""),
            account_keys=tuple(str(value) for value in raw.get("account_keys") or ()),
            maximum_gross_exposure=float(raw.get("maximum_gross_exposure") or 0),
            maximum_ticker_exposure=float(raw.get("maximum_ticker_exposure") or 0),
        )
        if set(group.account_keys) - account_keys:
            raise ValueError(f"Portfolio group {group.group_id} references unknown accounts")


def merged_assignment_parameters(configuration: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(dict(configuration["strategy"].get("parameters") or {}))
    _deep_merge(base, dict(assignment.get("parameters") or {}))
    oms = dict(configuration["oms"])
    execution = base.setdefault("execution", {})
    execution.update({
        "entry_urgency": oms["entry_urgency"],
        "exit_urgency": oms["exit_urgency"],
        "limit_offset_bps": float(oms["limit_offset_bps"]),
        "tick_size": float(oms["tick_size"]),
        "time_in_force": oms["time_in_force"],
        "outside_rth": bool(oms.get("outside_rth", False)),
    })
    protection = dict(oms.get("protection") or {})
    stop = base.setdefault("protection", {}).setdefault("stop", {})
    stop.update({
        "method": protection["stop_method"],
        "structure_buffer_bps": float(protection.get("structure_buffer_bps") or 0),
        "volatility_multiple": float(protection.get("volatility_multiple") or 0),
        "maximum_risk_pct": float(protection.get("maximum_risk_pct") or 0),
    })
    base["protection"].setdefault("trailing", {})["enabled"] = bool(protection.get("trailing_enabled", True))
    return resolve_long_momentum_parameters(base)


def _migrate_draft(raw: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw.get("assignments"), dict) and isinstance(raw.get("strategy"), dict):
        result = deepcopy(raw)
        defaults = _default_draft()
        result["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        result["strategy"].setdefault("profiles", [])
        result["strategy"].setdefault("capability_catalog", [])
        result["strategy"]["input_catalog"] = strategy_input_catalog()
        for profile in result["strategy"]["profiles"]:
            profile["definition_revision"] = STRATEGY_REVISION
            parameters = dict(profile.get("parameters") or {})
            if not isinstance(parameters.get("entry_rules"), dict):
                parameters["entry_rules"] = default_entry_decision_rules(parameters)
            _normalize_entry_rule_sources(parameters["entry_rules"])
            parameters.pop("entry", None)
            profile["parameters"] = resolve_long_momentum_parameters(parameters)
        for definition in result["strategy"].get("definitions") or []:
            if str(definition.get("strategy_id")) == STRATEGY_ID:
                definition["revision"] = STRATEGY_REVISION
        existing_profiles = {
            str(row.get("profile_id"))
            for row in dict(result.get("strategy") or {}).get("profiles") or []
        }
        result["strategy"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["strategy"]["profiles"]
            if str(row["profile_id"]) not in existing_profiles
        )
        existing_capabilities = {
            str(row.get("capability_id"))
            for row in dict(result.get("strategy") or {}).get("capability_catalog") or []
        }
        result["strategy"]["capability_catalog"].extend(
            deepcopy(row)
            for row in capability_catalog()
            if str(row["capability_id"]) not in existing_capabilities
        )
        existing_oms = {
            str(row.get("profile_id"))
            for row in dict(result.get("oms") or {}).get("profiles") or []
        }
        result["oms"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["oms"]["profiles"]
            if str(row["profile_id"]) not in existing_oms
        )
        for binding in dict(result.get("accounts") or {}).get("bindings") or []:
            _normalize_account_binding(binding)
        return result
    if not isinstance(raw.get("strategy"), dict) or "strategy_id" not in dict(raw.get("strategy") or {}):
        return deepcopy(raw)
    old = deepcopy(raw)
    base = _default_draft()
    legacy_strategy = dict(old["strategy"])
    profile = _strategy_profile(
        "migrated-primary",
        str(legacy_strategy.get("name") or "Migrated strategy"),
        "Migrated from the original application configuration.",
        dict(legacy_strategy.get("parameters") or default_long_momentum_parameters()),
        origin="user",
    )
    base["strategy"]["profiles"] = [
        profile,
        *[
            row for row in base["strategy"]["profiles"]
            if row["profile_id"] != profile["profile_id"]
        ],
    ]
    base["oms"]["profiles"] = [{
        "profile_id": "migrated-oms",
        "revision": 1,
        "name": "Migrated OMS",
        "description": "Migrated execution and protection behavior.",
        "origin": "user",
        "settings": deepcopy(old["oms"]),
    }]
    base["accounts"] = deepcopy(old["accounts"])
    for binding in base["accounts"].get("bindings") or []:
        _normalize_account_binding(binding)
    base["portfolio"]["policies"] = deepcopy(dict(old["portfolio"]).get("policies") or [])
    base["portfolio"]["groups"] = deepcopy(dict(old["portfolio"]).get("groups") or [])
    account_keys = [str(row["account_key"]) for row in dict(old["accounts"]).get("bindings") or []]
    mandates = [{
        "mandate_id": f"migrated-{key}",
        "deployment_id": "migrated-deployment",
        "account_key": key,
        "enabled": True,
        "maximum_cash_fraction": 1.0,
        "maximum_planned_risk_fraction": 0.01,
        "maximum_positions": 10,
        "priority": 50,
        "autonomy": "confirm",
        "allow_replacement": False,
        "minimum_replacement_improvement_pct": 20.0,
    } for key in account_keys]
    base["portfolio"]["mandates"] = mandates
    base["assignments"] = {"deployments": [{
        "deployment_id": "migrated-deployment",
        "name": "Migrated deployment",
        "description": "Runtime deployment migrated from the original assignment configuration.",
        "profile_id": profile["profile_id"],
        "oms_profile_id": "migrated-oms",
        "mandate_ids": [row["mandate_id"] for row in mandates],
        "enabled": True,
        "modes": ["replay", "backtest", "backtest_debug"],
        "runtime_assignments": deepcopy(old.get("assignments") or []),
    }]}
    if "canvas" in old:
        base["canvas"] = deepcopy(old["canvas"])
    return base


def _strategy_profile(
    profile_id: str,
    name: str,
    description: str,
    parameters: dict[str, Any],
    *,
    origin: str,
    capability_modes: dict[str, str] | None = None,
) -> dict[str, Any]:
    modes = capability_modes or {}
    capabilities = []
    for definition in capability_catalog():
        settings = deepcopy(definition["defaults"])
        if definition["capability_id"] in modes:
            settings["mode"] = modes[definition["capability_id"]]
        capabilities.append({
            "capability_id": definition["capability_id"],
            "revision": definition["revision"],
            "enabled": True,
            "settings": settings,
        })
    return {
        "profile_id": profile_id,
        "revision": 1,
        "name": name,
        "description": description,
        "definition_id": STRATEGY_ID,
        "definition_revision": STRATEGY_REVISION,
        "origin": origin,
        "editable": True,
        "enabled": True,
        "parameters": deepcopy(parameters),
        "capabilities": capabilities,
    }


def _normalize_account_binding(binding: dict[str, Any]) -> None:
    account_key = str(binding.get("account_key") or "")
    fallback = "Replay account" if account_key == "replay" else account_key or "Trading account"
    binding["name"] = str(binding.get("name") or binding.get("display_name") or fallback)


def _normalize_entry_rule_sources(entry_rules: dict[str, Any]) -> None:
    for stage in entry_rules.values():
        if not isinstance(stage, dict):
            continue
        for group in stage.get("groups") or []:
            for condition in group.get("conditions") or []:
                if (
                    str(condition.get("left_source_id") or "")
                    == "signal.vwap_transition.score"
                    and str(condition.get("left_timeframe") or "") == "5s"
                ):
                    condition["left_timeframe"] = "10s"
                if (
                    str(condition.get("right_source_id") or "")
                    == "signal.vwap_transition.score"
                    and str(condition.get("right_timeframe") or "") == "5s"
                ):
                    condition["right_timeframe"] = "10s"


def _default_oms_profile() -> dict[str, Any]:
    return {
        "profile_id": "adaptive-regular",
        "revision": 1,
        "name": "Adaptive regular",
        "description": "Balanced shared execution with hybrid protection and trailing enabled.",
        "origin": "system",
        "editable": True,
        "settings": {
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
    }


def _parameters_with_capabilities(profile: dict[str, Any]) -> dict[str, Any]:
    parameters = deepcopy(dict(profile.get("parameters") or {}))
    bindings = {str(row["capability_id"]): row for row in profile.get("capabilities") or [] if row.get("enabled", True)}
    pocket = bindings.get("profit-pocket")
    if pocket:
        _deep_merge(parameters.setdefault("profit_pocket", {}), dict(pocket.get("settings") or {}))
        parameters["profit_pocket"]["enabled"] = True
    reentry = bindings.get("exit-watch-reenter")
    if reentry:
        settings = dict(reentry.get("settings") or {})
        parameters.setdefault("reentry", {}).update({
            key: value for key, value in settings.items()
            if key in {"cooldown_ms", "maximum_attempts", "require_new_confirmation"}
        })
        parameters["reentry"]["enabled"] = True
    add = bindings.get("confirmed-pullback-add")
    if add:
        settings = dict(add.get("settings") or {})
        parameters.setdefault("add", {}).update({
            "enabled": True,
            "maximum_adds": int(settings.get("maximum_adds") or 0),
        })
        parameters.setdefault("sizing", {})["add_fraction"] = float(settings.get("add_fraction") or 0)
    return resolve_long_momentum_parameters(parameters)


def _validate_oms_settings(oms: dict[str, Any]) -> None:
    if str(oms.get("entry_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS entry urgency is unsupported")
    if str(oms.get("exit_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS exit urgency is unsupported")
    if float(oms.get("limit_offset_bps") or 0) < 0 or float(oms.get("tick_size") or 0) <= 0:
        raise ValueError("OMS offset cannot be negative and tick size must be positive")
    if str(oms.get("time_in_force") or "") not in {"DAY", "GTC", "IOC", "OPG"}:
        raise ValueError("OMS time in force is unsupported")
    if str(dict(oms.get("protection") or {}).get("stop_method") or "") not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Protection stop method is unsupported")


def _validate_capability_settings(definition: dict[str, Any], settings: dict[str, Any]) -> None:
    for parameter in definition.get("parameters") or []:
        key = str(parameter["key"])
        if key not in settings:
            raise ValueError(f"Capability {definition['name']} is missing {key}")
        value = settings[key]
        if parameter["type"] == "number":
            number = float(value)
            if number < float(parameter.get("minimum", number)) or number > float(parameter.get("maximum", number)):
                raise ValueError(f"Capability {definition['name']} {key} is outside its allowed range")
        if parameter["type"] == "choice" and value not in parameter.get("options", []):
            raise ValueError(f"Capability {definition['name']} {key} is unsupported")


def _unique_ids(rows: list[dict[str, Any]], key: str, label: str) -> set[str]:
    values = [str(row.get(key) or "").strip() for row in rows]
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ValueError(f"{label} ids must be present and unique")
    return set(values)


def _without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in payload.items() if key != "updated_at"}


def _deep_merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _overrides(parameters: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(parameters)
    for path, value in values.items():
        cursor = result
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return result


def _account_key(account_id: str, index: int) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in account_id)
    return normalized.strip("-") or f"account-{index + 1}"


def _choice(key: str, label: str, help_text: str, options: list[str]) -> dict[str, Any]:
    return {"key": key, "label": label, "help": help_text, "type": "choice", "options": options}


def _number(
    key: str,
    label: str,
    help_text: str,
    unit: str,
    minimum: float,
    maximum: float,
    step: float,
    *,
    display: str = "number",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "help": help_text,
        "type": "number",
        "unit": unit,
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
        "display": display,
    }


def _boolean(key: str, label: str, help_text: str) -> dict[str, Any]:
    return {"key": key, "label": label, "help": help_text, "type": "boolean"}
