from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

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
from src.trading_runtime.execution_policies import (
    execution_policy_from_payload,
    protection_profile_from_payload,
)
from src.trading_runtime.strategy_engine import (
    STRATEGY_ID,
    STRATEGY_REVISION,
    default_entry_decision_rules,
    default_long_momentum_parameters,
    resolve_long_momentum_parameters,
    strategy_input_catalog,
)
from src.trading_runtime.strategy_campaign import validate_campaign_policy


CONFIGURATION_SCHEMA_VERSION = 9
CONFIGURATION_SECTIONS = {"strategy", "run_plans", "portfolio", "oms", "accounts"}
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
ACTION_AUTHORITIES = {"disabled", "manual", "confirm", "automatic", "inherit"}


def _load_configuration_env() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for env_path in (Path.cwd() / ".env", repo_root / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def _resolved_source_account_id(binding: dict[str, Any]) -> str:
    environment_key = str(binding.get("source_account_env") or "").strip()
    if environment_key:
        _load_configuration_env()
        return os.environ.get(environment_key, "").strip()
    return str(binding.get("source_account_id") or "").strip()


def _runtime_account_binding(binding: dict[str, Any]) -> dict[str, Any]:
    resolved = deepcopy(binding)
    if str(resolved.get("source_account_env") or "").strip():
        resolved["source_account_id"] = _resolved_source_account_id(resolved)
    return resolved


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
    if section == "assignments":
        section = "run_plans"
        payload = {
            "universes": deepcopy(dict(payload or {}).get("universes") or []),
            "plans": deepcopy(
                dict(payload or {}).get("plans")
                or dict(payload or {}).get("deployments")
                or []
            ),
        }
    if section not in CONFIGURATION_SECTIONS:
        raise ValueError(f"Unknown trading configuration section: {section}")
    draft = configuration_draft()
    candidate = _without_timestamp(draft)
    candidate[section] = deepcopy(payload)
    _validate_draft(candidate, require_runtime_ready=False)
    return trading_journal().save_trading_configuration_draft(candidate)


def replace_configuration_draft(payload: Any) -> dict[str, Any]:
    """Validate and replace one complete mutable draft atomically."""
    if not isinstance(payload, dict):
        raise TypeError("Trading configuration draft must be an object")
    candidate = _without_timestamp(_migrate_draft(deepcopy(payload)))
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
    return approved_runtime_configuration_snapshot("replay")


def approved_runtime_configuration_snapshot(mode: str) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    approved = approved_configuration(required=True)
    assert approved is not None
    model = _migrate_draft(deepcopy(approved["payload"]))
    _validate_draft(model)
    if not model.get("canvas", {}).get("profile"):
        raise ValueError("The approved trading configuration does not contain a Canvas profile")
    runtimes = resolve_runtime_configurations(model, mode=mode)
    if not runtimes:
        raise ValueError(f"No enabled Strategy Run Plan supports {mode}")
    runtime_payload = deepcopy(runtimes[0])
    runtime_payload["run_plans"] = [
        deepcopy(runtime["run_plan"]) for runtime in runtimes
    ]
    runtime_payload["universes"] = [
        deepcopy(runtime["universe"]) for runtime in runtimes
    ]
    runtime_payload["assignments"] = [
        deepcopy(assignment)
        for runtime in runtimes
        for assignment in runtime["assignments"]
    ]
    runtime_payload["portfolio"]["mandates"] = [
        deepcopy(mandate)
        for runtime in runtimes
        for mandate in runtime["portfolio"]["mandates"]
    ]
    binding_by_key = {
        str(binding["account_key"]): deepcopy(binding)
        for runtime in runtimes
        for binding in runtime["accounts"]["bindings"]
    }
    runtime_payload["accounts"]["bindings"] = list(binding_by_key.values())
    runtime_payload["canvas"] = deepcopy(model["canvas"])
    return {
        "revision_id": approved["revision_id"],
        "revision": approved["revision"],
        "label": approved["label"],
        "content_hash": approved["content_hash"],
        "approved_at": approved["approved_at"],
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "run_plan_id": runtime_payload["run_plan"]["run_plan_id"],
        "configuration_model": model,
        "payload": runtime_payload,
    }


def effective_configuration_snapshot(
    *,
    mode: str,
    use_approved: bool = False,
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    revision = approved_configuration(required=True) if use_approved else None
    model = (
        deepcopy(dict(revision.get("payload") or {}))
        if revision
        else configuration_draft()
    )
    model = _migrate_draft(model)
    _validate_draft(model, require_runtime_ready=False)
    runtimes = resolve_runtime_configurations(model, mode=mode)
    policies = {
        str(row.get("policy_id") or ""): portfolio_policy_from_payload(dict(row))
        for row in dict(model["portfolio"]).get("policies") or []
    }
    accounts = []
    for binding in dict(model["accounts"]).get("bindings") or []:
        if mode not in set(binding.get("modes") or []):
            continue
        policy = policies.get(str(binding.get("portfolio_policy_id") or ""))
        accounts.append({
            **deepcopy(binding),
            "policy_identity": policy.identity if policy else "",
            "run_plan_ids": [
                str(runtime["run_plan"]["run_plan_id"])
                for runtime in runtimes
                if any(
                    str(row.get("account_key")) == str(binding.get("account_key"))
                    for row in runtime["accounts"]["bindings"]
                )
            ],
        })
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "source": "approved_release" if revision else "draft",
        "revision_id": str(revision.get("revision_id") or "") if revision else "",
        "runtime_count": len(runtimes),
        "accounts": accounts,
        "runtimes": runtimes,
    }


def resolve_runtime_configurations(
    model: dict[str, Any],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    migrated = _migrate_draft(model)
    eligible = [
        row
        for row in dict(migrated["run_plans"]).get("plans") or []
        if bool(row.get("enabled", True))
        and mode in set(row.get("allowed_environments") or [])
    ]
    eligible.sort(
        key=lambda row: (
            str(row.get("run_plan_id") or ""),
        )
    )
    return [
        resolve_runtime_configuration(
            migrated,
            mode=mode,
            run_plan_id=str(row["run_plan_id"]),
        )
        for row in eligible
    ]


def resolve_runtime_configuration(model: dict[str, Any], *, mode: str, run_plan_id: str = "", deployment_id: str = "") -> dict[str, Any]:
    """Resolve one approved Strategy Run Plan into shared runtime contracts."""

    model = _migrate_draft(model)
    run_plans = list(dict(model["run_plans"]).get("plans") or [])
    eligible = [
        row for row in run_plans
        if bool(row.get("enabled", True))
        and mode in set(row.get("allowed_environments") or [])
    ]
    requested_id = run_plan_id or deployment_id
    run_plan = next(
        (row for row in eligible if str(row.get("run_plan_id")) == requested_id),
        eligible[0] if eligible else None,
    )
    if run_plan is None:
        raise ValueError(f"No enabled Strategy Run Plan supports {mode}")
    profiles = {
        str(row["profile_id"]): row
        for row in dict(model["strategy"]).get("profiles") or []
    }
    profile = profiles.get(str(run_plan.get("profile_id") or ""))
    if profile is None:
        raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown Strategy Profile")
    oms_profiles = {
        str(row["profile_id"]): row
        for row in dict(model["oms"]).get("profiles") or []
    }
    oms = oms_profiles.get(str(run_plan.get("oms_profile_id") or ""))
    if oms is None:
        raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown OMS profile")
    mandates = [
        row for row in dict(model["portfolio"]).get("mandates") or []
        if str(row.get("run_plan_id")) == str(run_plan["run_plan_id"])
        and bool(row.get("enabled", True))
    ]
    account_keys = {str(row["account_key"]) for row in mandates}
    bindings = [
        _runtime_account_binding(dict(row)) for row in dict(model["accounts"]).get("bindings") or []
        if str(row.get("account_key")) in account_keys
    ]
    mandate_by_account = {str(row["account_key"]): row for row in mandates}
    assignment_mode = str(mandates[0].get("assignment_mode") or "single") if mandates else "single"
    total_weight = sum(float(row.get("allocation_weight") or 1.0) for row in mandates)
    for binding in bindings:
        mandate = mandate_by_account[str(binding["account_key"])]
        allocation = float(mandate.get("maximum_cash_fraction", 1.0))
        if assignment_mode == "weighted":
            allocation *= float(mandate.get("allocation_weight") or 1.0) / max(total_weight, 1e-12)
        binding["strategy_allocation"] = allocation
        binding["run_plan_assignment_mode"] = assignment_mode
        binding["allocation_weight"] = float(mandate.get("allocation_weight") or 1.0)
    runtime_assignments = [
        deepcopy(row) for row in run_plan.get("runtime_assignments") or []
        if str(row.get("account_key")) in account_keys
    ]
    universes = {
        str(row["universe_id"]): row
        for row in dict(model["run_plans"]).get("universes") or []
    }
    universe = universes.get(str(run_plan.get("universe_id") or ""))
    if universe is None:
        raise ValueError(
            f"Run Plan {run_plan.get('run_plan_id')} references an unknown Watch Universe"
        )
    for assignment in runtime_assignments:
        ticker = str(assignment.get("ticker") or "").upper()
        side = str(
            dict(dict(profile.get("lifecycle") or {}).get("trading_behavior") or {}).get("side")
            or "long"
        )
        assignment.setdefault(
            "campaign_id",
            f"{run_plan['run_plan_id']}:{ticker}:{side}",
        )
        policy = _effective_campaign_policy(run_plan)
        existing_permissions = dict(assignment.get("permissions") or {})
        assignment["permissions"] = {
            **existing_permissions,
            "observe": True,
            "enter": str(policy.get("initial_entry_authority")) != "disabled",
            "reenter": str(policy.get("reentry_authority")) != "disabled",
            "exit": str(policy.get("exit_authority")) != "disabled",
        }
        assignment["strategy_id"] = str(profile["definition_id"])
        assignment["strategy_revision"] = int(profile["definition_revision"])
        assignment["profile_id"] = str(profile["profile_id"])
        assignment["run_plan_id"] = str(run_plan["run_plan_id"])
        assignment["deployment_id"] = str(run_plan["run_plan_id"])
        assignment["universe_id"] = str(run_plan["universe_id"])
        assignment["book_id"] = str(run_plan["book_id"])
        assignment["side"] = side
        assignment["campaign_policy"] = deepcopy(policy)
        assignment["resolved_parameters"] = merged_assignment_parameters(
            {
                "strategy": {"parameters": _parameters_with_capabilities(profile)},
                "oms": {
                    "settings": deepcopy(dict(oms.get("settings") or {})),
                    "execution_policies": deepcopy(
                        dict(model["oms"]).get("execution_policies") or []
                    ),
                    "protection_profiles": deepcopy(
                        dict(model["oms"]).get("protection_profiles") or []
                    ),
                },
                "campaign_policy": deepcopy(policy),
            },
            assignment,
        )
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "run_plan": deepcopy(run_plan),
        "deployment": {**deepcopy(run_plan), "deployment_id": str(run_plan["run_plan_id"])},
        "universe": deepcopy(universe),
        "campaign_policy": deepcopy(
            _effective_campaign_policy(run_plan)
        ),
        "account_topology": {
            "mode": assignment_mode,
            "legs": [
                {
                    "account_key": str(row.get("account_key") or ""),
                    "allocation_weight": float(row.get("allocation_weight") or 1.0),
                    "maximum_cash_fraction": float(row.get("maximum_cash_fraction") or 0),
                    "maximum_action_authority": str(row.get("maximum_action_authority") or "manual"),
                }
                for row in mandates
            ],
        },
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
        "oms": {
            **deepcopy(dict(oms.get("settings") or {})),
            "profile_id": str(oms.get("profile_id") or ""),
            "profile_revision": int(oms.get("revision") or 1),
            "execution_policies": deepcopy(
                dict(model["oms"]).get("execution_policies") or []
            ),
            "protection_profiles": deepcopy(
                dict(model["oms"]).get("protection_profiles") or []
            ),
        },
        "accounts": {"bindings": bindings},
    }


def _default_strategy_lifecycle(parameters: dict[str, Any]) -> dict[str, Any]:
    rules = deepcopy(
        dict(parameters.get("entry_rules") or default_entry_decision_rules(parameters))
    )
    reentry = dict(parameters.get("reentry") or {})
    final_exit = dict(parameters.get("final_exit") or {})
    initial_rules = {
        "opportunity": deepcopy(dict(rules.get("trigger") or {})),
        "confirmation": deepcopy(dict(rules.get("confirmation") or {})),
        "blockers": deepcopy(dict(rules.get("veto") or {})),
    }
    lifecycle = {
        "trading_behavior": {
            "side": "long",
            "eligible_sessions": ["regular"],
            "evaluation_trigger": "indicator_update",
            "adopt_manual_positions": True,
        },
        "initial_entry": {
            **deepcopy(initial_rules),
            "capital_request": _default_capital_request("mandate_fraction", 0.20),
            "order_intent": _default_order_intent("adaptive_urgent"),
            "add_steps": [
                {
                    "step_id": "confirmed-position-add",
                    "name": "Confirmed position add",
                    "enabled": True,
                    "rules": _single_rule_stage(
                        "bullish-structure-add",
                        "Bullish structure continuation",
                        "indicator.structure.bullish_choch",
                        "1s",
                        "is_true",
                    ),
                    "capital_request": _default_capital_request(
                        "mandate_fraction", 0.50
                    ),
                    "order_intent": _default_order_intent("adaptive_urgent"),
                    "maximum_uses": 2,
                }
            ],
        },
        "reentry": {
            "enabled": bool(reentry.get("enabled", True)),
            "cooldown_ms": int(reentry.get("cooldown_ms") or 0),
            "maximum_attempts": int(reentry.get("maximum_attempts") or 0),
            "require_new_confirmation": bool(
                reentry.get("require_new_confirmation", True)
            ),
            "rules": deepcopy(initial_rules),
            "capital_request": _default_capital_request("mandate_fraction", 0.20),
            "order_intent": _default_order_intent("adaptive_urgent"),
        },
        "exit": {"rule_sets": _default_exit_rule_sets(final_exit)},
    }
    return lifecycle


def _default_capital_request(mode: str, value: float) -> dict[str, Any]:
    return {
        "mode": mode,
        "value": value,
        "allow_replacement": False,
    }


def _default_order_intent(policy: str) -> dict[str, Any]:
    return {
        "execution_policy": policy,
        "protection_profile": "hybrid-single",
        "partial_fill_policy": "complete_remainder",
        "deadline_ms": 750,
    }


def _single_rule_stage(
    group_id: str,
    label: str,
    source_id: str,
    timeframe: str,
    comparator: str,
    *,
    value: float | None = None,
) -> dict[str, Any]:
    return {
        "operator": "any",
        "groups": [{
            "group_id": group_id,
            "label": label,
            "operator": "all",
            "required_score": 1.0,
            "enabled": True,
            "conditions": [{
                "condition_id": f"{group_id}-condition",
                "left_source_id": source_id,
                "left_timeframe": timeframe,
                "comparator": comparator,
                "right_source_id": "",
                "right_timeframe": "",
                "value": value,
                "enabled": True,
            }],
        }],
    }


def _default_exit_rule_stage(mechanism: str) -> dict[str, Any]:
    if mechanism == "failed_breakout":
        stage = _single_rule_stage(
            "lose-entry-structure",
            "Lose entry structure",
            "market.last_price",
            "1s",
            "less_than",
        )
        condition = stage["groups"][0]["conditions"][0]
        condition["right_source_id"] = "indicator.structure.swing_high"
        condition["right_timeframe"] = "1s"
        return stage
    if mechanism == "bearish_qmd_macd":
        return _single_rule_stage(
            "adverse-qmd",
            "Adverse QMD momentum",
            "indicator.flow_structure.score",
            "100ms",
            "less_or_equal",
            value=-0.35,
        )
    return {"operator": "any", "groups": []}


def _default_exit_rule_sets(final_exit: dict[str, Any]) -> list[dict[str, Any]]:
    failed = {
        "rule_set_id": "failed-entry-thesis",
        "name": "Failed entry thesis",
        "summary": "Exit when price loses the configured entry reference during its validity window.",
        "enabled": bool(final_exit.get("exit_on_failed_breakout", True)),
        "rules": _default_exit_rule_stage("failed_breakout"),
        "timing": {"active_after_ms": 0, "expires_after_ms": 60_000},
        "action": "close",
        "position_fraction": 1.0,
        "order_intent": _default_order_intent("adaptive_urgent"),
    }
    adverse = {
        "rule_set_id": "adverse-momentum",
        "name": "Adverse momentum",
        "summary": "Exit when the configured QMD and MACD evidence turns against the position.",
        "enabled": bool(final_exit.get("bearish_momentum_enabled", True)),
        "rules": {
            "operator": "all",
            "groups": [
                _single_rule_stage(
                    "adverse-qmd-score",
                    "Adverse QMD score",
                    "indicator.flow_structure.score",
                    "100ms",
                    "less_or_equal",
                    value=float(final_exit.get("qmd_score") or -0.35),
                )["groups"][0],
                _single_rule_stage(
                    "qmd-confidence",
                    "QMD confidence",
                    "indicator.flow_structure.confidence",
                    "100ms",
                    "greater_or_equal",
                    value=float(final_exit.get("qmd_confidence") or 0.55),
                )["groups"][0],
                _single_rule_stage(
                    "adverse-macd-line",
                    "MACD line below signal",
                    "indicator.macd.line",
                    "5s",
                    "less_than",
                )["groups"][0],
                _single_rule_stage(
                    "adverse-macd-histogram",
                    "Negative MACD histogram",
                    "indicator.macd.histogram",
                    "5s",
                    "less_than",
                    value=0,
                )["groups"][0],
            ],
        },
        "timing": {"active_after_ms": 0, "expires_after_ms": 0},
        "action": "close",
        "position_fraction": 1.0,
        "order_intent": _default_order_intent("adaptive_urgent"),
    }
    adverse["rules"]["groups"][2]["conditions"][0].update({
        "right_source_id": "indicator.macd.signal",
        "right_timeframe": "5s",
        "value": None,
    })
    if not bool(final_exit.get("require_macd_bearish", True)):
        adverse["rules"]["groups"] = adverse["rules"]["groups"][:2]
    return [failed, adverse]


def _parameters_without_lifecycle(parameters: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(parameters)
    for key in ("entry_rules", "reentry", "final_exit", "exit_routes", "sizing", "add", "execution"):
        result.pop(key, None)
    return result


def _migrate_lifecycle_v5(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(lifecycle)
    defaults = _default_strategy_lifecycle(parameters)
    behavior = result.setdefault("trading_behavior", {})
    behavior.setdefault("side", "long")
    if behavior.get("side") == "both":
        behavior["side"] = "long"
    initial = result.setdefault("initial_entry", {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        initial.setdefault(stage_name, deepcopy(defaults["initial_entry"][stage_name]))
    legacy_sizing = dict(parameters.get("sizing") or {})
    initial.setdefault(
        "capital_request",
        _default_capital_request(
            str(legacy_sizing.get("request_mode") or "mandate_fraction"),
            float(legacy_sizing.get("request_value") or 0.20),
        ),
    )
    initial.setdefault("order_intent", deepcopy(defaults["initial_entry"]["order_intent"]))
    initial.setdefault("add_steps", deepcopy(defaults["initial_entry"]["add_steps"]))
    reentry = result.setdefault("reentry", {})
    reentry.pop("reuse_initial_entry", None)
    reentry.setdefault("rules", {
        stage_name: deepcopy(initial[stage_name])
        for stage_name in ("opportunity", "confirmation", "blockers")
    })
    reentry.setdefault("capital_request", deepcopy(initial["capital_request"]))
    reentry.setdefault("order_intent", deepcopy(initial["order_intent"]))
    reentry.setdefault("enabled", True)
    reentry.setdefault("cooldown_ms", 0)
    reentry.setdefault("maximum_attempts", 0)
    reentry.setdefault("require_new_confirmation", True)
    exit_config = result.setdefault("exit", {})
    if "routes" not in exit_config and "rule_sets" not in exit_config:
        exit_config["rule_sets"] = deepcopy(defaults["exit"]["rule_sets"])
    return result


def _migrate_rule_stage_v6(stage: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(stage)
    result["operator"] = (
        str(result.get("operator"))
        if str(result.get("operator")) in {"all", "any"}
        else "all"
    )
    result.pop("minimum_score", None)
    for group in result.get("groups") or []:
        legacy_weight = float(group.pop("weight", 1.0) or 1.0)
        if str(group.get("operator") or "") not in {"all", "any", "score"}:
            group["operator"] = "all"
        group.setdefault("required_score", max(0.01, min(1.0, legacy_weight)))
    return result


def _migrate_lifecycle_v6(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v5(lifecycle, parameters)
    initial = dict(result.get("initial_entry") or {})
    for key in ("opportunity", "confirmation", "blockers"):
        initial[key] = _migrate_rule_stage_v6(dict(initial.get(key) or {}))
    for step in initial.get("add_steps") or []:
        step["rules"] = _migrate_rule_stage_v6(dict(step.get("rules") or {}))
    result["initial_entry"] = initial
    reentry = dict(result.get("reentry") or {})
    reentry_rules = dict(reentry.get("rules") or {})
    for key in ("opportunity", "confirmation", "blockers"):
        reentry_rules[key] = _migrate_rule_stage_v6(
            dict(reentry_rules.get(key) or {})
        )
    reentry["rules"] = reentry_rules
    result["reentry"] = reentry

    exit_config = dict(result.get("exit") or {})
    if not isinstance(exit_config.get("rule_sets"), list):
        migrated: list[dict[str, Any]] = []
        for route in sorted(
            list(exit_config.get("routes") or []),
            key=lambda row: -int(row.get("priority") or 0),
        ):
            mechanism = str(route.get("mechanism") or "")
            if mechanism == "protective_stop":
                continue
            rules = _migrate_rule_stage_v6(dict(route.get("rules") or {}))
            settings = dict(route.get("settings") or {})
            if mechanism == "bearish_qmd_macd":
                defaults = _default_exit_rule_sets({
                    "qmd_score": settings.get("qmd_score", -0.35),
                    "qmd_confidence": settings.get("qmd_confidence", 0.55),
                    "require_macd_bearish": settings.get(
                        "require_macd_bearish", True
                    ),
                    "bearish_momentum_enabled": route.get("enabled", True),
                })[1]
                rules = defaults["rules"]
            migrated.append({
                "rule_set_id": str(
                    route.get("route_id") or f"exit-rule-{len(migrated) + 1}"
                ),
                "name": str(route.get("name") or "Exit rule set"),
                "summary": str(route.get("summary") or ""),
                "enabled": bool(route.get("enabled", True)),
                "rules": rules,
                "timing": {
                    "active_after_ms": 0,
                    "expires_after_ms": (
                        60_000 if mechanism == "failed_breakout" else 0
                    ),
                },
                "action": str(route.get("action") or "close"),
                "position_fraction": float(
                    route.get("position_fraction") or 1.0
                ),
                "order_intent": deepcopy(
                    route.get("order_intent")
                    or _default_order_intent("adaptive_urgent")
                ),
            })
        exit_config["rule_sets"] = (
            migrated
            or deepcopy(
                _default_exit_rule_sets(
                    dict(parameters.get("final_exit") or {})
                )
            )
        )
    exit_config.pop("routes", None)
    for rule_set in exit_config.get("rule_sets") or []:
        rule_set["rules"] = _migrate_rule_stage_v6(
            dict(rule_set.get("rules") or {})
        )
        rule_set.setdefault(
            "timing", {"active_after_ms": 0, "expires_after_ms": 0}
        )
        rule_set.setdefault("action", "close")
        rule_set.setdefault("position_fraction", 1.0)
        rule_set.setdefault(
            "order_intent", _default_order_intent("adaptive_urgent")
        )
    result["exit"] = exit_config
    return result


def _normalize_smart_order_intent(intent: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(intent)
    result.pop("time_in_force", None)
    result.pop("outside_rth", None)
    return result


def _migrate_lifecycle_v7(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v6(lifecycle, parameters)
    initial = dict(result.get("initial_entry") or {})
    initial["order_intent"] = _normalize_smart_order_intent(
        dict(initial.get("order_intent") or {})
    )
    for step in initial.get("add_steps") or []:
        step["order_intent"] = _normalize_smart_order_intent(
            dict(step.get("order_intent") or {})
        )
    result["initial_entry"] = initial
    reentry = dict(result.get("reentry") or {})
    reentry["order_intent"] = _normalize_smart_order_intent(
        dict(reentry.get("order_intent") or {})
    )
    result["reentry"] = reentry
    exit_config = dict(result.get("exit") or {})
    for rule_set in exit_config.get("rule_sets") or []:
        rule_set["order_intent"] = _normalize_smart_order_intent(
            dict(rule_set.get("order_intent") or {})
        )
    result["exit"] = exit_config
    return result


def _default_campaign_policy() -> dict[str, Any]:
    return {
        "initial_entry_authority": "confirm",
        "reentry_authority": "confirm",
        "exit_authority": "automatic",
        "protective_exit_authority": "automatic",
        "maximum_reentries": 3,
        "reentry_cooldown_ms": 1_000,
        "maximum_initial_watch_ms": 0,
        "session_end_behavior": "keep_watching",
        "retain_ticker_while_paused": True,
    }


def _default_action_authority() -> dict[str, str]:
    return {
        "default": "confirm",
        "initial_entry": "inherit",
        "add": "inherit",
        "reentry": "inherit",
        "strategic_exit": "automatic",
        "protective_exit": "automatic",
        "emergency_exit": "automatic",
    }


def _default_safety_supervisor() -> dict[str, Any]:
    return {
        "enabled_by_environment": {
            "replay": True,
            "backtest": True,
            "backtest_debug": True,
            "paper": True,
            "live": True,
        }
    }


def _effective_campaign_policy(run_plan: dict[str, Any]) -> dict[str, Any]:
    lifecycle = dict(
        run_plan.get("campaign_lifecycle")
        or run_plan.get("campaign_policy")
        or {}
    )
    authority = dict(run_plan.get("action_authority") or {})
    default = str(authority.get("default") or "confirm")

    def resolved(key: str, legacy_key: str, fallback: str) -> str:
        value = str(authority.get(key) or lifecycle.get(legacy_key) or "inherit")
        if value == "inherit":
            value = default
        return value or fallback

    return {
        **_default_campaign_policy(),
        **lifecycle,
        "default_action_authority": default,
        "initial_entry_authority": resolved(
            "initial_entry", "initial_entry_authority", "confirm"
        ),
        "add_authority": resolved("add", "add_authority", "confirm"),
        "reentry_authority": resolved("reentry", "reentry_authority", "confirm"),
        "exit_authority": resolved(
            "strategic_exit", "exit_authority", "automatic"
        ),
        "protective_exit_authority": "automatic",
        "emergency_exit_authority": "automatic",
    }


def _default_universe(runtime_assignments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "universe_id": "configured-watch-universe",
        "name": "Configured watch universe",
        "description": "Symbols explicitly approved for strategy evaluation.",
        "source": "configured_symbols",
        "symbols": sorted(
            {
                str(row.get("ticker") or "").upper()
                for row in runtime_assignments
                if str(row.get("ticker") or "").strip()
            }
        ),
        "scanner_view_id": "",
        "enabled": True,
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
            _overrides(parameters, {"reentry.maximum_attempts": 1}),
            origin="system",
        ),
        _strategy_profile(
            "long-momentum-semi-auto",
            "Long Momentum · Semi-automatic",
            "Operator-confirmed entries with configurable automated position management.",
            parameters,
            origin="system",
            capability_modes={"profit-pocket": "confirm", "exit-watch-reenter": "confirm", "confirmed-pullback-add": "confirm"},
        ),
    ]
    system_profiles[1]["lifecycle"]["initial_entry"]["capital_request"] = (
        _default_capital_request("mandate_fraction", 0.10)
    )
    system_profiles[1]["lifecycle"]["reentry"]["capital_request"] = (
        _default_capital_request("mandate_fraction", 0.10)
    )
    system_profiles[0]["protected"] = True
    profile_templates = deepcopy(system_profiles[1:])
    system_profiles = system_profiles[:1]
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
            "name": "Backtest account" if account_id in {"replay", "backtest"} else account_id,
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
    _ensure_environment_account_bindings(bindings, policy["policy_id"])
    mandates = [
        {
            "mandate_id": f"balanced-{binding['account_key']}",
            "run_plan_id": "balanced-replay",
            "account_key": binding["account_key"],
            "enabled": True,
            "maximum_cash_fraction": 1.0,
            "maximum_planned_risk_fraction": 0.01,
            "maximum_positions": 10,
            "assignment_mode": "single",
            "allocation_weight": 1.0,
            "maximum_action_authority": "automatic",
            "allow_replacement": False,
            "minimum_replacement_improvement_pct": 20.0,
        }
        for binding in bindings
        if "replay" in binding["modes"]
    ]
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "strategy": {
            "default_profile_id": "long-momentum-balanced",
            "definitions": [{
                "strategy_id": definition["strategy_id"],
                "revision": int(definition["revision"]),
                "name": definition["name"],
                "automatic": bool(definition.get("automatic", True)),
                "direction": str(dict(definition.get("config") or {}).get("direction") or ""),
                "supported_sides": list(
                    dict(definition.get("config") or {}).get("supported_sides")
                    or ["long"]
                ),
            }],
            "capability_catalog": capability_catalog(),
            "input_catalog": strategy_input_catalog(),
            "profile_templates": profile_templates,
            "profiles": system_profiles,
        },
        "run_plans": {
            "universes": [_default_universe(runtime_assignments)],
            "plans": [{
                "run_plan_id": "balanced-replay",
                "name": "Balanced Replay",
                "description": "Approved balanced strategy prepared for historical simulation.",
                "profile_id": "long-momentum-balanced",
                "oms_profile_id": "adaptive-regular",
                "universe_id": "configured-watch-universe",
                "book_id": "default",
                "action_authority": _default_action_authority(),
                "campaign_lifecycle": _default_campaign_policy(),
                "safety_supervisor": _default_safety_supervisor(),
                "mandate_ids": [row["mandate_id"] for row in mandates],
                "enabled": True,
                "allowed_environments": ["replay", "backtest", "backtest_debug"],
                "runtime_assignments": runtime_assignments,
            }]
        },
        "portfolio": {"policies": [policy], "groups": [], "mandates": mandates},
        "oms": {
            "profiles": [_default_oms_profile()],
            "execution_policies": _default_execution_policies(),
            "protection_profiles": _default_protection_profiles(),
        },
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
    default_profile_id = str(strategy.get("default_profile_id") or "")
    if default_profile_id not in profile_ids:
        raise ValueError("The protected default Strategy Profile is required")
    for profile in profiles:
        definition = get_strategy_definition(
            str(profile.get("definition_id") or ""),
            int(profile.get("definition_revision") or 0),
        )
        if not definition.get("enabled", True):
            raise ValueError(f"Strategy definition for {profile.get('name')} is disabled")
        if str(profile.get("profile_id")) == default_profile_id and not bool(
            profile.get("protected")
        ):
            raise ValueError("The default Strategy Profile must remain protected")
        lifecycle = dict(profile.get("lifecycle") or {})
        _validate_strategy_lifecycle(lifecycle)
        definition_config = dict(definition.get("config") or {})
        direction = str(definition_config.get("direction") or "")
        configured_side = str(dict(lifecycle.get("trading_behavior") or {}).get("side") or "")
        supported_sides = set(definition_config.get("supported_sides") or ["long"])
        if configured_side not in supported_sides:
            raise ValueError(
                f"Strategy Profile {profile.get('name')} does not support the {configured_side} side"
            )
        _parameters_with_capabilities(profile)
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
        if require_runtime_ready and modes.intersection({"paper", "live"}) and not _resolved_source_account_id(row):
            reference = str(row.get("source_account_env") or "broker account id")
            raise ValueError(f"Account {row.get('account_key')} requires an exact broker account id ({reference}) for Paper or Live")
        if not str(row.get("session_key") or "").strip():
            raise ValueError(f"Account {row.get('account_key')} requires a session key")

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
    execution_policies = list(dict(draft["oms"]).get("execution_policies") or [])
    protection_profiles = list(dict(draft["oms"]).get("protection_profiles") or [])
    if not execution_policies or not protection_profiles:
        raise ValueError("OMS requires execution policies and protection profiles")
    execution_ids = _unique_ids(execution_policies, "policy_id", "Execution policy")
    protection_ids = _unique_ids(protection_profiles, "profile_id", "Protection profile")
    execution_references: set[str] = set()
    protection_references: set[str] = set()
    for row in execution_policies:
        policy = _validate_execution_policy_config(row)
        execution_references.update({policy.policy_id, policy.identity})
    for row in protection_profiles:
        profile = _validate_protection_profile_config(row)
        protection_references.update({profile.profile_id, profile.identity})
    for row in oms_profiles:
        _validate_oms_settings(dict(row.get("settings") or {}))
        settings = dict(row.get("settings") or {})
        for key in ("entry_execution_policy_id", "exit_execution_policy_id"):
            if str(settings.get(key) or "") not in execution_references:
                raise ValueError(f"OMS profile {row.get('name')} references unknown {key}")
        if str(settings.get("protection_profile_id") or "") not in protection_references:
            raise ValueError(f"OMS profile {row.get('name')} references an unknown protection profile")
    for profile in profiles:
        lifecycle = dict(profile.get("lifecycle") or {})
        for intent in _lifecycle_order_intents(lifecycle):
            if str(intent.get("execution_policy") or "") not in execution_references:
                raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown execution policy")
            protection_reference = str(intent.get("protection_profile") or "")
            if protection_reference and protection_reference not in protection_references:
                raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown protection profile")
    for policy in policies:
        unknown_execution = set(policy.allowed_execution_policies) - {"*"} - execution_references
        unknown_protection = set(policy.allowed_protection_profiles) - {"*"} - protection_references
        if unknown_execution:
            raise ValueError(f"Portfolio policy {policy.identity} allows unknown execution policies")
        if unknown_protection:
            raise ValueError(f"Portfolio policy {policy.identity} allows unknown protection profiles")

    run_plans = list(dict(draft["run_plans"]).get("plans") or [])
    if require_runtime_ready and not run_plans:
        raise ValueError("At least one Strategy Run Plan is required")
    run_plan_ids = _unique_ids(run_plans, "run_plan_id", "Strategy Run Plan")
    universes = list(dict(draft["run_plans"]).get("universes") or [])
    if require_runtime_ready and not universes:
        raise ValueError("At least one Watch Universe is required")
    universe_ids = _unique_ids(universes, "universe_id", "Watch Universe")
    for universe in universes:
        source = str(universe.get("source") or "")
        if source not in {
            "configured_symbols",
            "scanner_view",
            "watchlist",
        }:
            raise ValueError(
                f"Watch Universe {universe.get('name')} has an unsupported source"
            )
        if require_runtime_ready and source != "configured_symbols":
            raise ValueError(
                f"Watch Universe {universe.get('name')} cannot be published until its {source} runtime resolver is available"
            )
    mandates = list(dict(draft["portfolio"]).get("mandates") or [])
    mandate_ids = _unique_ids(mandates, "mandate_id", "Strategy-account mandate")
    for mandate in mandates:
        if str(mandate.get("run_plan_id") or "") not in run_plan_ids:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown Run Plan")
        if str(mandate.get("account_key") or "") not in account_keys:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown account")
        fraction = float(mandate.get("maximum_cash_fraction") or 0)
        if not 0 < fraction <= 1:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} maximum cash must be between 0 and 100 percent")
        if str(mandate.get("assignment_mode") or "") not in {"single", "replicated", "weighted", "partitioned"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has unsupported account assignment mode")
        if float(mandate.get("allocation_weight") or 0) <= 0:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} allocation weight must be positive")
        if str(mandate.get("maximum_action_authority") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has unsupported maximum action authority")
    for run_plan in run_plans:
        environments = set(run_plan.get("allowed_environments") or [])
        if not environments or not environments <= SUPPORTED_MODES:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} has unsupported environments")
        if str(run_plan.get("profile_id") or "") not in profile_ids:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown Strategy Profile")
        if str(run_plan.get("oms_profile_id") or "") not in oms_ids:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown OMS profile")
        if str(run_plan.get("universe_id") or "") not in universe_ids:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} references an unknown Watch Universe"
            )
        if not str(run_plan.get("book_id") or "").strip():
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} requires a portfolio book"
            )
        action_authority = dict(run_plan.get("action_authority") or {})
        if set(str(value) for value in action_authority.values()) - ACTION_AUTHORITIES:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} has unsupported action authority")
        if str(action_authority.get("default") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} requires a default action authority")
        for action in ("initial_entry", "add", "reentry", "strategic_exit"):
            if action != "reentry" and str(action_authority.get(action) or "") == "disabled":
                raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} cannot disable {action}")
        if str(action_authority.get("protective_exit")) != "automatic" or str(action_authority.get("emergency_exit")) != "automatic":
            raise ValueError("Protective and emergency exits must remain automatic")
        safety = dict(dict(run_plan.get("safety_supervisor") or {}).get("enabled_by_environment") or {})
        if not bool(safety.get("live", True)) or not bool(safety.get("paper", True)):
            raise ValueError("Trading Safety Supervisor cannot be disabled for Live or Paper")
        policy = _effective_campaign_policy(run_plan)
        validate_campaign_policy(policy)
        if require_runtime_ready and str(policy.get("initial_entry_authority") or "") == "automatic":
            universe = next(
                row for row in universes
                if str(row.get("universe_id")) == str(run_plan.get("universe_id"))
            )
            universe_symbols = {
                str(value).strip().upper()
                for value in universe.get("symbols") or []
                if str(value).strip()
            }
            identity_bound_symbols = {
                str(row.get("ticker") or "").strip().upper()
                for row in run_plan.get("runtime_assignments") or []
                if str(row.get("ticker") or "").strip() and int(row.get("conid") or 0) > 0
            }
            missing_identity = sorted(universe_symbols - identity_bound_symbols)
            if missing_identity:
                raise ValueError(
                    f"Automatic Run Plan {run_plan.get('name')} requires identity-bound assignments for: {', '.join(missing_identity)}"
                )
        referenced_mandates = [
            row for row in mandates
            if str(row.get("run_plan_id")) == str(run_plan.get("run_plan_id"))
        ]
        if require_runtime_ready and not referenced_mandates:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} requires at least one account mandate")
        enabled_mandates = [row for row in referenced_mandates if bool(row.get("enabled", True))]
        assignment_modes = {str(row.get("assignment_mode") or "") for row in enabled_mandates}
        if len(assignment_modes) > 1:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} account mandates must use one assignment mode")
        if assignment_modes == {"single"} and len(enabled_mandates) != 1:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} single assignment mode requires exactly one account")
        authority_rank = {"disabled": 0, "manual": 1, "confirm": 2, "automatic": 3}
        default_authority = str(action_authority.get("default") or "confirm")
        effective_authorities = [
            default_authority if str(action_authority.get(action) or "inherit") == "inherit" else str(action_authority.get(action))
            for action in ("initial_entry", "add", "reentry", "strategic_exit")
        ]
        for mandate in enabled_mandates:
            maximum_authority = str(mandate.get("maximum_action_authority") or "manual")
            if any(authority_rank.get(value, 99) > authority_rank[maximum_authority] for value in effective_authorities):
                raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} exceeds mandate {mandate.get('mandate_id')} action authority cap")
        for assignment in run_plan.get("runtime_assignments") or []:
            if str(assignment.get("account_key") or "") not in account_keys:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} references an unknown account")
            if not str(assignment.get("ticker") or "").strip() or int(assignment.get("conid") or 0) <= 0:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} requires ticker and conid")
    if require_runtime_ready:
        mandate_pairs = {
            (str(mandate.get("account_key") or ""), str(mandate.get("run_plan_id") or ""))
            for mandate in mandates
            if bool(mandate.get("enabled", True))
        }
        for account in accounts:
            if not bool(account.get("enabled", True)):
                continue
            for mode in account.get("modes") or []:
                eligible = any(
                    bool(run_plan.get("enabled", True))
                    and mode in set(run_plan.get("allowed_environments") or [])
                    and (
                        str(account.get("account_key") or ""),
                        str(run_plan.get("run_plan_id") or ""),
                    ) in mandate_pairs
                    for run_plan in run_plans
                )
                if not eligible:
                    raise ValueError(
                        f"Account {account.get('account_key')} requires an enabled {mode} Run Plan mandate"
                    )
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
    campaign_policy = dict(configuration.get("campaign_policy") or {})
    reentry = base.setdefault("reentry", {})
    if campaign_policy:
        reentry["maximum_attempts"] = min(
            int(reentry.get("maximum_attempts") or 0),
            int(campaign_policy.get("maximum_reentries") or 0),
        )
        reentry["cooldown_ms"] = max(
            int(reentry.get("cooldown_ms") or 0),
            int(campaign_policy.get("reentry_cooldown_ms") or 0),
        )
        reentry["enabled"] = (
            bool(reentry.get("enabled", True))
            and str(campaign_policy.get("reentry_authority")) != "disabled"
        )
    oms_contract = dict(configuration["oms"])
    oms = dict(oms_contract.get("settings") or oms_contract)
    execution_policies = list(oms_contract.get("execution_policies") or [])
    protection_profiles = list(oms_contract.get("protection_profiles") or [])
    base["execution_policy_catalog"] = _policy_catalog_payload(
        execution_policies, "policy_id"
    )
    base["protection_profile_catalog"] = _policy_catalog_payload(
        protection_profiles, "profile_id"
    )
    execution = base.setdefault("execution", {})
    execution.update({
        "entry_urgency": oms["entry_urgency"],
        "exit_urgency": oms["exit_urgency"],
        "limit_offset_bps": float(oms["limit_offset_bps"]),
        "tick_size": float(oms["tick_size"]),
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


def _policy_catalog_payload(
    rows: list[dict[str, Any]],
    identity_key: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = deepcopy(row)
        policy_id = str(row.get(identity_key) or "")
        revision = int(row.get("revision") or 1)
        references = {policy_id, f"{policy_id}@{revision}"}
        for reference in references - {""}:
            result[reference] = payload
    return result


def _migrate_draft(raw: dict[str, Any]) -> dict[str, Any]:
    if (
        isinstance(raw.get("run_plans"), dict)
        or isinstance(raw.get("assignments"), dict)
    ) and isinstance(raw.get("strategy"), dict):
        result = deepcopy(raw)
        defaults = _default_draft()
        result["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        legacy_run_plans = dict(
            result.get("run_plans") or result.pop("assignments", {}) or {}
        )
        result["run_plans"] = {
            "universes": deepcopy(legacy_run_plans.get("universes") or []),
            "plans": deepcopy(
                legacy_run_plans.get("plans")
                or legacy_run_plans.get("deployments")
                or []
            ),
        }
        result.pop("assignments", None)
        for run_plan in result["run_plans"]["plans"]:
            legacy_policy = dict(run_plan.pop("campaign_policy", {}) or {})
            run_plan["run_plan_id"] = str(
                run_plan.get("run_plan_id")
                or run_plan.pop("deployment_id", "")
            )
            run_plan["allowed_environments"] = list(
                run_plan.get("allowed_environments")
                or run_plan.pop("modes", [])
                or []
            )
            run_plan.pop("selection_priority", None)
            authority = dict(run_plan.get("action_authority") or {})
            authority.setdefault("default", "confirm")
            authority.setdefault(
                "initial_entry",
                str(legacy_policy.get("initial_entry_authority") or "inherit"),
            )
            authority.setdefault("add", str(legacy_policy.get("add_authority") or "inherit"))
            authority.setdefault(
                "reentry", str(legacy_policy.get("reentry_authority") or "inherit")
            )
            authority.setdefault(
                "strategic_exit", str(legacy_policy.get("exit_authority") or "automatic")
            )
            authority["protective_exit"] = "automatic"
            authority["emergency_exit"] = "automatic"
            run_plan["action_authority"] = authority
            run_plan["campaign_lifecycle"] = {
                **_default_campaign_policy(),
                **dict(run_plan.get("campaign_lifecycle") or legacy_policy),
            }
            safety = dict(run_plan.get("safety_supervisor") or {})
            run_plan["safety_supervisor"] = {
                "enabled_by_environment": {
                    **_default_safety_supervisor()["enabled_by_environment"],
                    **dict(safety.get("enabled_by_environment") or {}),
                    "paper": True,
                    "live": True,
                }
            }
        for mandate in dict(result.get("portfolio") or {}).get("mandates") or []:
            mandate["run_plan_id"] = str(
                mandate.get("run_plan_id")
                or mandate.pop("deployment_id", "")
            )
            mandate.setdefault("assignment_mode", "single")
            mandate.setdefault("allocation_weight", 1.0)
            mandate.setdefault("maximum_action_authority", "automatic")
            mandate.pop("autonomy", None)
            mandate.pop("priority", None)
        result["strategy"].setdefault("profiles", [])
        result["strategy"].setdefault("capability_catalog", [])
        result["strategy"]["default_profile_id"] = str(
            result["strategy"].get("default_profile_id")
            or "long-momentum-balanced"
        )
        result["strategy"]["profile_templates"] = deepcopy(
            defaults["strategy"]["profile_templates"]
        )
        result["strategy"]["definitions"] = deepcopy(
            defaults["strategy"]["definitions"]
        )
        result["strategy"]["input_catalog"] = strategy_input_catalog()
        for profile in result["strategy"]["profiles"]:
            profile["definition_revision"] = STRATEGY_REVISION
            parameters = resolve_long_momentum_parameters(
                dict(profile.get("parameters") or {})
            )
            if not isinstance(parameters.get("entry_rules"), dict):
                parameters["entry_rules"] = default_entry_decision_rules(parameters)
            _normalize_entry_rule_sources(parameters["entry_rules"])
            parameters.pop("entry", None)
            lifecycle = deepcopy(
                dict(profile.get("lifecycle") or _default_strategy_lifecycle(parameters))
            )
            legacy_reentry = next(
                (
                    row
                    for row in profile.get("capabilities") or []
                    if str(row.get("capability_id")) == "exit-watch-reenter"
                ),
                None,
            )
            if legacy_reentry is not None and not profile.get("lifecycle"):
                settings = dict(legacy_reentry.get("settings") or {})
                lifecycle.setdefault("reentry", {}).update(
                    {
                        key: value
                        for key, value in settings.items()
                        if key
                        in {
                            "cooldown_ms",
                            "maximum_attempts",
                            "require_new_confirmation",
                        }
                    }
                )
                lifecycle["reentry"]["enabled"] = bool(
                    legacy_reentry.get("enabled", True)
                )
            lifecycle = _migrate_lifecycle_v7(lifecycle, parameters)
            initial_entry = dict(lifecycle.get("initial_entry") or {})
            initial_capital = dict(initial_entry.get("capital_request") or {})
            initial_capital.pop("priority", None)
            initial_entry["capital_request"] = initial_capital
            for step in initial_entry.get("add_steps") or []:
                step_capital = dict(step.get("capital_request") or {})
                step_capital.pop("priority", None)
                step["capital_request"] = step_capital
            lifecycle["initial_entry"] = initial_entry
            reentry = dict(lifecycle.get("reentry") or {})
            reentry_capital = dict(reentry.get("capital_request") or {})
            reentry_capital.pop("priority", None)
            reentry["capital_request"] = reentry_capital
            lifecycle["reentry"] = reentry
            for intent in _lifecycle_order_intents(lifecycle):
                intent.setdefault("protection_profile", "hybrid-single")
            initial_entry = dict(lifecycle.get("initial_entry") or {})
            _normalize_entry_rule_sources({
                key: value
                for key, value in initial_entry.items()
                if key in {"opportunity", "confirmation", "blockers"}
            })
            reentry_rules = dict(
                dict(lifecycle.get("reentry") or {}).get("rules") or {}
            )
            _normalize_entry_rule_sources(reentry_rules)
            for step in initial_entry.get("add_steps") or []:
                _normalize_entry_rule_sources(
                    {"rules": dict(step.get("rules") or {})}
                )
            for route in dict(lifecycle.get("exit") or {}).get("rule_sets") or []:
                _normalize_entry_rule_sources(
                    {"rules": dict(route.get("rules") or {})}
                )
            profile["lifecycle"] = lifecycle
            profile["parameters"] = _parameters_without_lifecycle(parameters)
            profile["protected"] = (
                str(profile.get("profile_id"))
                == result["strategy"]["default_profile_id"]
            )
            profile["capabilities"] = [
                row
                for row in profile.get("capabilities") or []
                if str(row.get("capability_id")) != "exit-watch-reenter"
            ]
        template_ids = {
            str(row.get("profile_id"))
            for row in result["strategy"]["profile_templates"]
        }
        referenced_profile_ids = {
            str(row.get("profile_id"))
            for row in dict(result.get("run_plans") or {}).get("plans") or []
        }
        migrated_profiles: list[dict[str, Any]] = []
        for profile in result["strategy"]["profiles"]:
            profile_id = str(profile.get("profile_id"))
            if profile_id in template_ids and profile_id not in referenced_profile_ids:
                continue
            if profile_id in template_ids:
                profile["origin"] = "user"
            migrated_profiles.append(profile)
        result["strategy"]["profiles"] = migrated_profiles
        existing_profiles = {
            str(row.get("profile_id"))
            for row in dict(result.get("strategy") or {}).get("profiles") or []
        }
        result["strategy"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["strategy"]["profiles"]
            if str(row["profile_id"]) == result["strategy"]["default_profile_id"]
            and str(row["profile_id"]) not in existing_profiles
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
        result["strategy"]["capability_catalog"] = [
            row
            for row in result["strategy"]["capability_catalog"]
            if str(row.get("capability_id")) != "exit-watch-reenter"
        ]
        result["run_plans"].setdefault(
            "universes",
            deepcopy(defaults["run_plans"]["universes"]),
        )
        universe_ids = {
            str(row.get("universe_id"))
            for row in result["run_plans"]["universes"]
        }
        fallback_universe = (
            next(iter(universe_ids))
            if universe_ids
            else "configured-watch-universe"
        )
        for run_plan in result["run_plans"].get("plans") or []:
            run_plan.setdefault("universe_id", fallback_universe)
            run_plan.setdefault("book_id", "default")
        existing_oms = {
            str(row.get("profile_id"))
            for row in dict(result.get("oms") or {}).get("profiles") or []
        }
        result["oms"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["oms"]["profiles"]
            if str(row["profile_id"]) not in existing_oms
        )
        for key in ("execution_policies", "protection_profiles"):
            result["oms"].setdefault(key, deepcopy(defaults["oms"][key]))
        for oms_profile in result["oms"]["profiles"]:
            settings = dict(oms_profile.get("settings") or {})
            settings.pop("time_in_force", None)
            settings.pop("outside_rth", None)
            settings["session_routing"] = "smart"
            settings.setdefault("entry_execution_policy_id", "adaptive_urgent")
            settings.setdefault("exit_execution_policy_id", "adaptive_very_urgent")
            settings.setdefault("protection_profile_id", "hybrid-single")
            oms_profile["settings"] = settings
        for binding in dict(result.get("accounts") or {}).get("bindings") or []:
            _normalize_account_binding(binding)
        _ensure_environment_account_bindings(
            result["accounts"]["bindings"],
            str(result["portfolio"]["policies"][0]["policy_id"]),
        )
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
    base["oms"]["profiles"][0]["settings"].pop("time_in_force", None)
    base["oms"]["profiles"][0]["settings"].pop("outside_rth", None)
    base["oms"]["profiles"][0]["settings"]["session_routing"] = "smart"
    base["oms"]["profiles"][0]["settings"].setdefault(
        "entry_execution_policy_id", "adaptive_urgent"
    )
    base["oms"]["profiles"][0]["settings"].setdefault(
        "exit_execution_policy_id", "adaptive_very_urgent"
    )
    base["oms"]["profiles"][0]["settings"].setdefault(
        "protection_profile_id", "hybrid-single"
    )
    base["accounts"] = deepcopy(old["accounts"])
    for binding in base["accounts"].get("bindings") or []:
        _normalize_account_binding(binding)
    base["portfolio"]["policies"] = deepcopy(dict(old["portfolio"]).get("policies") or [])
    base["portfolio"]["groups"] = deepcopy(dict(old["portfolio"]).get("groups") or [])
    account_keys = [str(row["account_key"]) for row in dict(old["accounts"]).get("bindings") or []]
    mandates = [{
        "mandate_id": f"migrated-{key}",
        "run_plan_id": "migrated-run-plan",
        "account_key": key,
        "enabled": True,
        "maximum_cash_fraction": 1.0,
        "maximum_planned_risk_fraction": 0.01,
        "maximum_positions": 10,
        "assignment_mode": "single" if len(account_keys) == 1 else "replicated",
        "allocation_weight": 1.0,
        "maximum_action_authority": "automatic",
        "allow_replacement": False,
        "minimum_replacement_improvement_pct": 20.0,
    } for key in account_keys]
    base["portfolio"]["mandates"] = mandates
    base["run_plans"] = {
        "universes": deepcopy(base["run_plans"]["universes"]),
        "plans": [{
        "run_plan_id": "migrated-run-plan",
        "name": "Migrated Run Plan",
        "description": "Run Plan migrated from the original assignment configuration.",
        "profile_id": profile["profile_id"],
        "oms_profile_id": "migrated-oms",
        "universe_id": "configured-watch-universe",
        "book_id": "default",
        "action_authority": _default_action_authority(),
        "campaign_lifecycle": _default_campaign_policy(),
        "safety_supervisor": _default_safety_supervisor(),
        "mandate_ids": [row["mandate_id"] for row in mandates],
        "enabled": True,
        "allowed_environments": ["replay", "backtest", "backtest_debug"],
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
    protected: bool = False,
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
        "protected": protected,
        "enabled": True,
        "lifecycle": _default_strategy_lifecycle(parameters),
        "parameters": _parameters_without_lifecycle(parameters),
        "capabilities": capabilities,
    }


def _normalize_account_binding(binding: dict[str, Any]) -> None:
    account_key = str(binding.get("account_key") or "")
    fallback = "Backtest account" if account_key in {"replay", "backtest"} else account_key or "Trading account"
    if account_key == "replay" and str(binding.get("name") or "") == "Replay account":
        binding["name"] = fallback
        return
    binding["name"] = str(binding.get("name") or binding.get("display_name") or fallback)


def _ensure_environment_account_bindings(bindings: list[dict[str, Any]], policy_id: str) -> None:
    managed = [
        {
            "account_key": "paper",
            "name": "IBKR Paper account",
            "source_account_env": "IBKR_PAPER_ACCOUNT_ID",
            "source_account_id": "",
            "account_class": "paper",
            "base_currency": "USD",
            "session_key": "ibkr-paper",
            "portfolio_policy_id": policy_id,
            "enabled": False,
            "system_managed": True,
            "modes": ["paper"],
        },
        {
            "account_key": "cash",
            "name": "IBKR Cash account",
            "source_account_env": "IBKR_CASH_ACCOUNT_ID",
            "source_account_id": "",
            "account_class": "cash",
            "base_currency": "USD",
            "session_key": "ibkr-live",
            "portfolio_policy_id": policy_id,
            "enabled": False,
            "system_managed": True,
            "modes": ["live"],
        },
    ]
    managed_by_key = {binding["account_key"]: binding for binding in managed}
    existing = {str(binding.get("account_key") or "") for binding in bindings}
    for binding in bindings:
        template = managed_by_key.get(str(binding.get("account_key") or ""))
        if template is None or str(binding.get("source_account_env") or "") != template["source_account_env"]:
            continue
        if not bool(binding.get("system_managed")):
            binding["enabled"] = False
            binding["system_managed"] = True
    bindings.extend(deepcopy(binding) for binding in managed if binding["account_key"] not in existing)


def _lifecycle_order_intents(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    initial = lifecycle.setdefault("initial_entry", {})
    reentry = lifecycle.setdefault("reentry", {})
    exit_section = lifecycle.setdefault("exit", {})
    return [
        initial.setdefault("order_intent", {}),
        *[
            step.setdefault("order_intent", {})
            for step in initial.get("add_steps") or []
        ],
        reentry.setdefault("order_intent", {}),
        *[
            route.setdefault("order_intent", {})
            for route in exit_section.get("rule_sets") or []
        ],
    ]


def _validate_strategy_lifecycle(lifecycle: dict[str, Any]) -> None:
    required = {"trading_behavior", "initial_entry", "reentry", "exit"}
    missing = required - set(lifecycle)
    if missing:
        raise ValueError(
            f"Strategy lifecycle is missing: {', '.join(sorted(missing))}"
        )
    behavior = dict(lifecycle["trading_behavior"])
    if str(behavior.get("side") or "") not in {"long", "short"}:
        raise ValueError("Each Strategy Profile must use exactly one side: long or short")
    sessions = set(behavior.get("eligible_sessions") or [])
    if not sessions or not sessions <= {"premarket", "regular", "after_hours"}:
        raise ValueError("Strategy eligible sessions are unsupported")
    initial_entry = dict(lifecycle["initial_entry"])
    runtime_rules = {
        "trigger": deepcopy(dict(initial_entry.get("opportunity") or {})),
        "confirmation": deepcopy(dict(initial_entry.get("confirmation") or {})),
        "veto": deepcopy(dict(initial_entry.get("blockers") or {})),
    }
    parameters = default_long_momentum_parameters()
    parameters["entry_rules"] = runtime_rules
    resolve_long_momentum_parameters(parameters)
    _validate_capital_request(dict(initial_entry.get("capital_request") or {}), "Initial entry")
    _validate_order_intent(dict(initial_entry.get("order_intent") or {}), "Initial entry")
    add_steps = list(initial_entry.get("add_steps") or [])
    _unique_ids(add_steps, "step_id", "Initial-entry add step")
    for step in add_steps:
        _validate_rule_stage(dict(step.get("rules") or {}), f"Add step {step.get('name')}")
        _validate_capital_request(dict(step.get("capital_request") or {}), f"Add step {step.get('name')}")
        _validate_order_intent(dict(step.get("order_intent") or {}), f"Add step {step.get('name')}")
        if int(step.get("maximum_uses") or 0) < 1:
            raise ValueError(f"Add step {step.get('name')} maximum uses must be positive")
    reentry = dict(lifecycle["reentry"])
    if int(reentry.get("cooldown_ms") or 0) < 0:
        raise ValueError("Strategy reentry cooldown cannot be negative")
    if int(reentry.get("maximum_attempts") or 0) < 0:
        raise ValueError("Strategy maximum reentries cannot be negative")
    reentry_rules = dict(reentry.get("rules") or {})
    runtime_reentry_rules = {
        "trigger": deepcopy(dict(reentry_rules.get("opportunity") or {})),
        "confirmation": deepcopy(dict(reentry_rules.get("confirmation") or {})),
        "veto": deepcopy(dict(reentry_rules.get("blockers") or {})),
    }
    reentry_parameters = default_long_momentum_parameters()
    reentry_parameters["entry_rules"] = runtime_reentry_rules
    resolve_long_momentum_parameters(reentry_parameters)
    _validate_capital_request(dict(reentry.get("capital_request") or {}), "Reentry")
    _validate_order_intent(dict(reentry.get("order_intent") or {}), "Reentry")
    routes = list(dict(lifecycle["exit"]).get("rule_sets") or [])
    _unique_ids(routes, "rule_set_id", "Strategy exit rule set")
    if not routes:
        raise ValueError("Strategy exit requires at least one rule set")
    for route in routes:
        if str(route.get("action") or "") not in {"close", "reduce"}:
            raise ValueError(f"Exit rule set {route.get('name')} has an unsupported action")
        timing = dict(route.get("timing") or {})
        if int(timing.get("active_after_ms") or 0) < 0 or int(
            timing.get("expires_after_ms") or 0
        ) < 0:
            raise ValueError("Exit rule-set timing cannot be negative")
        _validate_rule_stage(dict(route.get("rules") or {}), f"Exit rule set {route.get('name')}")
        _validate_order_intent(dict(route.get("order_intent") or {}), f"Exit rule set {route.get('name')}")
        position_fraction = float(route.get("position_fraction") or 0)
        if not 0 < position_fraction <= 1:
            raise ValueError(f"Exit rule set {route.get('name')} position fraction must be between zero and one")


def _validate_rule_stage(stage: dict[str, Any], label: str) -> None:
    if str(stage.get("operator") or "") not in {"all", "any"}:
        raise ValueError(f"{label} has unsupported rule-set logic")
    for group in stage.get("groups") or []:
        operator = str(group.get("operator") or "")
        if operator not in {"all", "any", "score"}:
            raise ValueError(f"{label} has unsupported condition logic")
        if operator == "score" and not 0 < float(group.get("required_score") or 0) <= 1:
            raise ValueError(f"{label} required score must be between zero and one")
        if not list(group.get("conditions") or []):
            raise ValueError(f"{label} rule sets require at least one condition")


def _validate_capital_request(request: dict[str, Any], label: str) -> None:
    mode = str(request.get("mode") or "")
    if mode not in {"fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"}:
        raise ValueError(f"{label} capital request mode is unsupported")
    value = float(request.get("value") or 0)
    if mode in {"mandate_fraction", "risk_fraction"} and not 0 < value <= 1:
        raise ValueError(f"{label} capital request fraction must be between zero and one")
    if mode == "fixed_quantity" and value <= 0:
        raise ValueError(f"{label} fixed quantity must be positive")
    if mode == "all_available" and value not in {0.0, 1.0}:
        raise ValueError(f"{label} all-available requests do not accept a custom value")


def _validate_order_intent(intent: dict[str, Any], label: str) -> None:
    if not str(intent.get("execution_policy") or "").strip():
        raise ValueError(f"{label} execution policy is required")
    if "time_in_force" in intent or "outside_rth" in intent:
        raise ValueError(
            f"{label} session routing must be derived by OMS from Trading Behavior"
        )
    if str(intent.get("partial_fill_policy") or "") not in {
        "complete_remainder",
        "accept_partial",
        "cancel_remainder",
    }:
        raise ValueError(f"{label} partial-fill policy is unsupported")
    if int(intent.get("deadline_ms") or 0) < 0:
        raise ValueError(f"{label} execution deadline cannot be negative")


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
            "entry_execution_policy_id": "adaptive_urgent",
            "exit_execution_policy_id": "adaptive_very_urgent",
            "protection_profile_id": "hybrid-single",
            "entry_urgency": "urgent",
            "exit_urgency": "very_urgent",
            "limit_offset_bps": 5.0,
            "tick_size": 0.01,
            "session_routing": "smart",
            "protection": {
                "stop_method": "hybrid",
                "structure_buffer_bps": 8.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 1.5,
                "trailing_enabled": True,
            },
        },
    }


def _default_execution_policies() -> list[dict[str, Any]]:
    timing = {
        "passive": (1_000, 2, 250),
        "midpoint": (750, 3, 150),
        "adaptive_patient": (1_000, 3, 250),
        "adaptive_regular": (750, 4, 100),
        "adaptive_urgent": (500, 6, 50),
        "adaptive_very_urgent": (300, 8, 25),
        "immediate_with_limit": (250, 3, 25),
        "ibkr_native_adaptive": (1_000, 0, 0),
        "cancel_if_not_filled": (500, 3, 100),
    }
    return [
        {
            "policy_id": name,
            "revision": 1,
            "name": name,
            "description": f"System {name.replace('_', ' ')} execution policy.",
            "origin": "system",
            "editable": True,
            "quote_source": "qmd",
            "partial_fill_policy": "complete_remainder",
            "envelope": {
                "maximum_buy_price": None,
                "minimum_sell_price": None,
                "deadline_ms": values[0],
                "maximum_reprices": values[1],
                "minimum_reprice_interval_ms": values[2],
            },
        }
        for name, values in timing.items()
    ]


def _default_protection_profiles() -> list[dict[str, Any]]:
    return [{
        "profile_id": "hybrid-single",
        "revision": 1,
        "name": "Hybrid single stop",
        "description": "One full-position hybrid stop using the current causal strategy swing and volatility.",
        "origin": "system",
        "editable": True,
        "add_policy": "independent_slice",
        "profit_pocket_transition": "move_to_breakeven",
        "mandatory_catastrophic_backstop": True,
        "emergency_repair_deadline_ms": 500,
        "slices": [{
            "slice_id": "position",
            "quantity_fraction": 1.0,
            "profit_target_price": None,
            "use_strategy_profit_target": True,
            "stop": {
                "rule_type": "hybrid",
                "order_type": "STP",
                "price": None,
                "distance_percent": None,
                "distance_bps": None,
                "maximum_cash_risk": None,
                "volatility_multiple": 1.25,
                "buffer_bps": 8.0,
                "anchor_source": "strategy_swing",
                "anchor_ordinal": "most_recent",
                "structural_timeframe": "strategy",
                "stop_limit_offset_bps": None,
            },
            "trailing": {
                "rule_type": "none",
                "amount": None,
                "percent": None,
                "volatility_multiple": None,
                "activation_gain_percent": 0.0,
                "breakeven_buffer_bps": 0.0,
                "structural_timeframe": "",
            },
        }],
    }]


def _parameters_with_capabilities(profile: dict[str, Any]) -> dict[str, Any]:
    parameters = deepcopy(dict(profile.get("parameters") or {}))
    lifecycle = dict(profile.get("lifecycle") or {})
    initial_entry = dict(lifecycle.get("initial_entry") or {})
    parameters["entry_rules"] = {
        "trigger": deepcopy(dict(initial_entry.get("opportunity") or {})),
        "confirmation": deepcopy(dict(initial_entry.get("confirmation") or {})),
        "veto": deepcopy(dict(initial_entry.get("blockers") or {})),
    }
    behavior = deepcopy(dict(lifecycle.get("trading_behavior") or {}))
    reentry = deepcopy(dict(lifecycle.get("reentry") or {}))
    reentry_rules = dict(reentry.pop("rules", {}) or {})
    parameters["reentry"] = reentry
    exit_rule_sets = list(
        dict(lifecycle.get("exit") or {}).get("rule_sets") or []
    )
    parameters["strategy_behavior"] = behavior
    parameters["phase_policy"] = {
        "initial_entry": {
            "capital_request": deepcopy(dict(initial_entry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(initial_entry.get("order_intent") or {})),
            "add_steps": deepcopy(list(initial_entry.get("add_steps") or [])),
        },
        "reentry": {
            "rules": {
                "trigger": deepcopy(dict(reentry_rules.get("opportunity") or {})),
                "confirmation": deepcopy(dict(reentry_rules.get("confirmation") or {})),
                "veto": deepcopy(dict(reentry_rules.get("blockers") or {})),
            },
            "capital_request": deepcopy(dict(reentry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(reentry.get("order_intent") or {})),
        },
        "exit": {"rule_sets": deepcopy(exit_rule_sets)},
    }
    bindings = {str(row["capability_id"]): row for row in profile.get("capabilities") or [] if row.get("enabled", True)}
    pocket = bindings.get("profit-pocket")
    if pocket:
        _deep_merge(parameters.setdefault("profit_pocket", {}), dict(pocket.get("settings") or {}))
        parameters["profit_pocket"]["enabled"] = True
    add = bindings.get("confirmed-pullback-add")
    if add:
        settings = dict(add.get("settings") or {})
        add_steps = list(
            parameters.setdefault("phase_policy", {})
            .setdefault("initial_entry", {})
            .get("add_steps") or []
        )
        if add_steps:
            add_steps[0]["maximum_uses"] = int(
                settings.get("maximum_adds") or add_steps[0].get("maximum_uses") or 1
            )
            parameters["phase_policy"]["initial_entry"]["add_steps"] = add_steps
    return resolve_long_momentum_parameters(parameters)


def _validate_oms_settings(oms: dict[str, Any]) -> None:
    if str(oms.get("entry_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS entry urgency is unsupported")
    if str(oms.get("exit_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS exit urgency is unsupported")
    if float(oms.get("limit_offset_bps") or 0) < 0 or float(oms.get("tick_size") or 0) <= 0:
        raise ValueError("OMS offset cannot be negative and tick size must be positive")
    if str(oms.get("session_routing") or "") != "smart":
        raise ValueError("OMS session routing must use smart broker-aware mode")
    if str(dict(oms.get("protection") or {}).get("stop_method") or "") not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Protection stop method is unsupported")


def _validate_execution_policy_config(payload: dict[str, Any]):
    try:
        return execution_policy_from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Execution policy {payload.get('policy_id') or '<unknown>'} is invalid: {exc}"
        ) from exc


def _validate_protection_profile_config(payload: dict[str, Any]):
    resolved = deepcopy(payload)
    for raw_slice in resolved.get("slices") or []:
        raw_slice.pop("use_strategy_profit_target", None)
        stop = dict(raw_slice.get("stop") or {})
        anchor_source = str(stop.pop("anchor_source", "") or "")
        stop.pop("anchor_ordinal", None)
        stop.pop("structural_timeframe", None)
        rule_type = str(stop.get("rule_type") or "")
        if anchor_source and anchor_source not in {"strategy_swing", "explicit"}:
            raise ValueError("Protection anchor source must be strategy_swing or explicit")
        if rule_type in {"swing_anchored", "hybrid"} and anchor_source == "strategy_swing":
            stop["anchor"] = {
                "observation_id": "configuration-validation",
                "price": 90.0,
                "confirmed_at": "2000-01-01T00:00:00+00:00",
                "timeframe": "strategy",
                "ordinal": "most_recent",
            }
        if rule_type in {"fixed_price", "catastrophic"} and not stop.get("price"):
            stop["price"] = 90.0
        raw_slice["stop"] = stop
    try:
        return protection_profile_from_payload(resolved)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Protection profile {payload.get('profile_id') or '<unknown>'} is invalid: {exc}"
        ) from exc


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
