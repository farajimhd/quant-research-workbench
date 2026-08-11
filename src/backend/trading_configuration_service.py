from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from src.backend.application_registry import (
    DISCOVERY_FIELD_PRESENTATIONS,
    FIELD_DEFINITIONS,
)
from src.backend.qmd_gateway_client import qmd_catalogs
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
from src.trading_runtime.taxonomy import StrategyTaxonomy


CONFIGURATION_SCHEMA_VERSION = 19
CONFIGURATION_SECTIONS = {
    "strategy",
    "market_discovery",
    "run_plans",
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
SUPPORTED_MODES = {"replay", "backtest", "backtest_debug", "paper", "live"}
ACTION_AUTHORITIES = {"disabled", "manual", "confirm", "automatic", "inherit"}
DISCOVERY_EXECUTION_SCOPES = {
    "universal_ingest",
    "core_scan",
    "watchlist",
    "strategy_run",
    "request",
    "offline",
}
DISCOVERY_CONFIGURATION_POLICIES = {"locked", "configurable", "generated", "retired"}
_QMD_RUNTIME_CATALOG_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])


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


def public_configuration_revision(revision: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime-only broker identities from a configuration response."""

    public = deepcopy(revision)

    def scrub(model: dict[str, Any]) -> None:
        for binding in dict(model.get("accounts") or {}).get("bindings") or []:
            if str(binding.get("source_account_env") or "").strip() or set(
                binding.get("modes") or []
            ).intersection({"paper", "live"}):
                binding["source_account_id"] = ""

    payload = public.get("payload")
    if isinstance(payload, dict):
        scrub(payload)
    configuration_model = public.get("configuration_model")
    if isinstance(configuration_model, dict):
        scrub(configuration_model)
    return public


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


def configuration_base() -> dict[str, Any]:
    journal = trading_journal()
    approved = journal.approved_trading_configuration()
    if approved is not None:
        return _migrate_draft(deepcopy(dict(approved.get("payload") or {})))
    return _default_draft()


def configuration_revisions() -> list[dict[str, Any]]:
    return trading_journal().trading_configuration_revisions()


def approved_configuration(*, required: bool = False) -> dict[str, Any] | None:
    result = trading_journal().approved_trading_configuration()
    if result is None and required:
        raise ValueError("No approved trading configuration exists. Publish one from Configuration > Approved Releases.")
    return result


def approved_canvas_profile() -> dict[str, Any]:
    """Project the published Canvas default without exposing the full release."""

    approved = approved_configuration()
    if approved is None:
        return {
            "schema_version": 1,
            "available": False,
            "revision_id": "",
            "configuration_revision": 0,
            "content_hash": "",
            "canvas_revision": "",
            "profile": {},
        }
    canvas = dict(dict(approved.get("payload") or {}).get("canvas") or {})
    profile = deepcopy(dict(canvas.get("profile") or {}))
    return {
        "schema_version": 1,
        "available": bool(profile),
        "revision_id": str(approved.get("revision_id") or ""),
        "configuration_revision": int(approved.get("revision") or 0),
        "content_hash": str(approved.get("content_hash") or ""),
        "canvas_revision": str(canvas.get("revision") or ""),
        "profile": profile,
    }


def publish_configuration(
    *,
    label: str,
    canvas_revision: str,
    canvas_profile: dict[str, Any],
    configuration: dict[str, Any],
    strategy_profile_id: str = "",
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
    if not isinstance(configuration, dict):
        raise TypeError("Publishing requires the complete session configuration")
    base_configuration = configuration_base()
    draft_candidate = _without_timestamp(_migrate_draft(deepcopy(configuration)))
    _assert_published_profiles_unchanged(base_configuration, draft_candidate)
    profiles = list(dict(draft_candidate["strategy"]).get("profiles") or [])
    active_profile_id = str(dict(draft_candidate["strategy"]).get("active_profile_id") or "")
    first_user_draft_id = next(
        (
            str(row.get("profile_id") or "")
            for row in profiles
            if str(row.get("publication_status") or "draft") == "draft"
            and str(row.get("origin") or "user") == "user"
        ),
        "",
    )
    selected_profile_id = (
        strategy_profile_id.strip()
        or active_profile_id
        or first_user_draft_id
        or str(dict(draft_candidate["strategy"]).get("default_profile_id") or "")
    )
    selected_profile = next(
        (row for row in profiles if str(row.get("profile_id")) == selected_profile_id),
        None,
    )
    if selected_profile is None:
        raise ValueError("Publishing requires a selected Strategy")
    selected_profile["publication_status"] = "published"
    selected_profile["editable"] = False
    draft_candidate["strategy"]["active_profile_id"] = selected_profile_id
    _validate_draft(draft_candidate, require_runtime_ready=False)

    # Publishing compiles one immutable runtime projection without replacing the
    # reusable Portfolio, OMS, account, or discovery catalogs in the working draft.
    runtime_candidate = deepcopy(draft_candidate)
    runtime_profile = next(
        row for row in runtime_candidate["strategy"]["profiles"]
        if str(row.get("profile_id")) == selected_profile_id
    )
    _compile_profile_run_plan(runtime_candidate, runtime_profile)
    _validate_draft(runtime_candidate)
    payload = {
        **runtime_candidate,
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "canvas": {"revision": canvas_revision.strip(), "profile": deepcopy(canvas_profile)},
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    existing = configuration_revisions()
    if existing and existing[0]["content_hash"] == content_hash:
        return existing[0]
    revision = int(existing[0]["revision"]) + 1 if existing else 1
    published = trading_journal().publish_trading_configuration(
        revision_id=str(uuid4()),
        revision=revision,
        label=normalized_label,
        content_hash=content_hash,
        payload=payload,
    )
    return published


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
    configuration: dict[str, Any] | None = None,
    use_approved: bool = False,
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    revision = approved_configuration(required=True) if use_approved else None
    model = (
        deepcopy(dict(revision.get("payload") or {}))
        if revision
        else deepcopy(configuration) if configuration is not None
        else configuration_base()
    )
    model = _migrate_draft(model)
    _validate_draft(model, require_runtime_ready=False)
    runtimes = resolve_runtime_configurations(
        model,
        mode=mode,
        resolve_broker_ids=False,
    )
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
    resolve_broker_ids: bool = True,
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
            resolve_broker_ids=resolve_broker_ids,
        )
        for row in eligible
    ]


def resolve_runtime_configuration(
    model: dict[str, Any],
    *,
    mode: str,
    run_plan_id: str = "",
    deployment_id: str = "",
    resolve_broker_ids: bool = True,
) -> dict[str, Any]:
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
        (
            _runtime_account_binding(dict(row))
            if resolve_broker_ids
            else deepcopy(dict(row))
        )
        for row in dict(model["accounts"]).get("bindings") or []
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
    universe = deepcopy(universe)
    if str(universe.get("source") or "") == "watchlist":
        universe = _resolve_watchlist_universe(universe, mode=mode)
        if mode in {"live", "paper"}:
            from src.backend.real_live_trading_service import tradable_symbol_map

            identities = tradable_symbol_map(list(universe.get("symbols") or []))
            existing_pairs = {
                (
                    str(row.get("account_key") or ""),
                    str(row.get("ticker") or "").upper(),
                )
                for row in runtime_assignments
            }
            for account_key in sorted(account_keys):
                for ticker in universe.get("symbols") or []:
                    identity = identities.get(str(ticker).upper(), {})
                    conid = int(identity.get("ibkr_conid") or 0)
                    if not conid or (account_key, str(ticker).upper()) in existing_pairs:
                        continue
                    runtime_assignments.append(
                        {
                            "assignment_id": f"{run_plan['run_plan_id']}:{account_key}:{str(ticker).upper()}",
                            "account_key": account_key,
                            "ticker": str(ticker).upper(),
                            "conid": conid,
                            "status": "watching",
                            "permissions": {},
                            "parameters": {},
                            "source": "watchlist_runtime",
                        }
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
        "universe": universe,
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


def _resolve_watchlist_universe(
    universe: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    result = deepcopy(universe)
    if mode not in {"live", "paper"}:
        result["symbols"] = []
        result["resolved"] = False
        result["resolution_status"] = "historical_membership_required"
        return result
    from src.backend.watchlist_runtime_service import WATCHLIST_RUNTIME

    runtime = WATCHLIST_RUNTIME.snapshot()
    watchlist_id = str(result.get("scanner_view_id") or "")
    snapshot = next(
        (
            row
            for row in runtime.get("watchlists") or []
            if str(row.get("watchlist_id") or "") == watchlist_id
        ),
        None,
    )
    result["symbols"] = sorted(
        {
            str(row.get("ticker") or "").upper()
            for row in dict(snapshot or {}).get("members") or []
            if str(row.get("ticker") or "").strip()
        }
    )
    result["resolved"] = snapshot is not None
    result["resolved_at"] = runtime.get("as_of")
    result["resolution_status"] = "ready" if snapshot is not None else "awaiting_watchlist_snapshot"
    return result


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
        "phase_modes": {
            "initial_entry": "automatic",
            "manage": "automatic",
            "reentry": "automatic",
            "exit": "automatic",
        },
        "trading_behavior": {
            "side": "long",
            "eligible_sessions": ["regular"],
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


def _rule_set_identifier(context: str, group_id: str, existing: set[str]) -> str:
    base = "-".join(
        part for part in "".join(
            character.lower() if character.isalnum() else " "
            for character in f"{context}-{group_id}"
        ).split() if part
    ) or "rule-set"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _catalog_stage_rules(
    stage: dict[str, Any],
    context: str,
    catalog: list[dict[str, Any]],
    existing: set[str],
) -> dict[str, Any]:
    if isinstance(stage.get("expression"), dict):
        return deepcopy(stage)
    children: list[dict[str, Any]] = []
    for index, raw_group in enumerate(stage.get("groups") or []):
        group = deepcopy(dict(raw_group))
        rule_set_id = _rule_set_identifier(
            context,
            str(group.pop("group_id", "") or f"rule-{index + 1}"),
            existing,
        )
        catalog.append({
            "rule_set_id": rule_set_id,
            "name": str(group.pop("label", "") or f"Rule set {index + 1}"),
            "description": "",
            **group,
        })
        children.append({"kind": "rule_set", "rule_set_id": rule_set_id})
    return {
        "expression": {
            "kind": "operator",
            "operator": "and" if str(stage.get("operator") or "any") == "all" else "or",
            "children": children,
        }
    }


def _migrate_profile_rule_catalog(profile: dict[str, Any]) -> None:
    catalog = list(deepcopy(profile.get("rule_set_catalog") or []))
    existing = {
        str(rule_set.get("rule_set_id") or "")
        for rule_set in catalog
        if str(rule_set.get("rule_set_id") or "")
    }
    lifecycle = dict(profile.get("lifecycle") or {})
    initial = dict(lifecycle.get("initial_entry") or {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        initial[stage_name] = _catalog_stage_rules(
            dict(initial.get(stage_name) or {}),
            f"initial-entry-{stage_name}",
            catalog,
            existing,
        )
    for step in initial.get("add_steps") or []:
        step["rules"] = _catalog_stage_rules(
            dict(step.get("rules") or {}),
            f"add-{step.get('step_id') or 'step'}",
            catalog,
            existing,
        )
    lifecycle["initial_entry"] = initial
    reentry = dict(lifecycle.get("reentry") or {})
    reentry_rules = dict(reentry.get("rules") or {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        reentry_rules[stage_name] = _catalog_stage_rules(
            dict(reentry_rules.get(stage_name) or {}),
            f"reentry-{stage_name}",
            catalog,
            existing,
        )
    reentry["rules"] = reentry_rules
    lifecycle["reentry"] = reentry
    exit_section = dict(lifecycle.get("exit") or {})
    for route in exit_section.get("rule_sets") or []:
        route["rules"] = _catalog_stage_rules(
            dict(route.get("rules") or {}),
            f"exit-{route.get('rule_set_id') or 'route'}",
            catalog,
            existing,
        )
    lifecycle["exit"] = exit_section
    canonical_by_fingerprint: dict[str, str] = {}
    remapped_ids: dict[str, str] = {}
    deduplicated: list[dict[str, Any]] = []
    for rule_set in catalog:
        fingerprint = json.dumps(
            {
                "name": str(rule_set.get("name") or ""),
                "enabled": bool(rule_set.get("enabled", True)),
                "operator": str(rule_set.get("operator") or "all"),
                "required_score": float(rule_set.get("required_score") or 1),
                "conditions": rule_set.get("conditions") or [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        canonical_id = canonical_by_fingerprint.get(fingerprint)
        if canonical_id:
            remapped_ids[rule_set_id] = canonical_id
            continue
        canonical_by_fingerprint[fingerprint] = rule_set_id
        remapped_ids[rule_set_id] = rule_set_id
        deduplicated.append(rule_set)

    def remap_expression(expression: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(expression)
        if str(result.get("kind") or "") == "rule_set":
            rule_set_id = str(result.get("rule_set_id") or "")
            result["rule_set_id"] = remapped_ids.get(rule_set_id, rule_set_id)
            return result
        result["children"] = [
            remap_expression(dict(child))
            for child in result.get("children") or []
        ]
        return result

    for stage in (
        *(initial.get(name) or {} for name in ("opportunity", "confirmation", "blockers")),
        *(step.get("rules") or {} for step in initial.get("add_steps") or []),
        *(reentry_rules.get(name) or {} for name in ("opportunity", "confirmation", "blockers")),
        *(route.get("rules") or {} for route in exit_section.get("rule_sets") or []),
    ):
        stage["expression"] = remap_expression(dict(stage.get("expression") or {}))
    profile["rule_set_catalog"] = deduplicated
    profile["lifecycle"] = lifecycle


def _expression_rule_set_ids(expression: dict[str, Any]) -> set[str]:
    if str(expression.get("kind") or "") == "rule_set":
        rule_set_id = str(expression.get("rule_set_id") or "")
        return {rule_set_id} if rule_set_id else set()
    result: set[str] = set()
    for child in expression.get("children") or []:
        result.update(_expression_rule_set_ids(dict(child)))
    return result


def _materialize_rule_stage(
    stage: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expression = deepcopy(dict(stage.get("expression") or {}))
    return {
        "expression": expression,
        "rule_sets": [
            deepcopy(catalog[rule_set_id])
            for rule_set_id in sorted(_expression_rule_set_ids(expression))
            if rule_set_id in catalog
        ],
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
    for key in ("entry_rules", "re_evaluation", "strategy_behavior", "reentry", "final_exit", "exit_routes", "sizing", "add", "execution"):
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


def _migrate_lifecycle_v10(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v7(lifecycle, parameters)
    behavior = dict(result.get("trading_behavior") or {})
    legacy_trigger = str(behavior.pop("evaluation_trigger", "") or "indicator_update")
    result["trading_behavior"] = behavior
    result.setdefault(
        "re_evaluation",
        {
            "rule_sets": [
                {
                    "rule_set_id": f"{legacy_trigger.replace('_', '-')}-events",
                    "name": legacy_trigger.replace("_", " ").title(),
                    "enabled": True,
                    "event_type": legacy_trigger,
                    "source_id": "",
                    "campaign_states": ["flat", "position_open"],
                }
            ]
        },
    )
    return result


def _migrate_lifecycle_v13(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    had_phase_modes = isinstance(lifecycle.get("phase_modes"), dict)
    legacy_reentry_enabled = bool(
        dict(lifecycle.get("reentry") or {}).get("enabled", True)
    )
    result = _migrate_lifecycle_v10(lifecycle, parameters)
    modes = result.setdefault("phase_modes", {})
    for phase_name in ("initial_entry", "manage", "reentry", "exit"):
        if str(modes.get(phase_name) or "") not in {"automatic", "manual"}:
            modes[phase_name] = "automatic"
    if not had_phase_modes and not legacy_reentry_enabled:
        modes["reentry"] = "manual"
    result["reentry"]["enabled"] = modes["reentry"] == "automatic"
    return result


def _migrate_lifecycle_v14(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v13(lifecycle, parameters)
    result.setdefault("trading_behavior", {}).pop("adopt_manual_positions", None)
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


def _qmd_family_capabilities() -> list[dict[str, Any]]:
    """Project only the QMD-owned runtime catalog into configuration records.

    Saved configurations carry their own immutable capability rows and remain
    reviewable during a QMD outage. New/default configuration never invents a
    second backend-owned availability catalog when QMD authority is unavailable.
    """

    return _qmd_runtime_capabilities()


def _qmd_runtime_capabilities() -> list[dict[str, Any]]:
    """Project the QMD-owned runtime catalog into configuration UI records.

    QMD Gateway is the sole availability authority. A missing or invalid
    catalog produces no rows, so callers cannot mistake a backend snapshot for
    current QMD behavior.
    """

    global _QMD_RUNTIME_CATALOG_CACHE
    now = time.monotonic()
    expires_at, cached = _QMD_RUNTIME_CATALOG_CACHE
    if now < expires_at:
        return deepcopy(cached)
    try:
        catalogs = qmd_catalogs()
    except Exception:
        _QMD_RUNTIME_CATALOG_CACHE = (now + 5.0, [])
        return []
    capability_rows = [
        dict(row)
        for row in catalogs.get("capability_catalog") or []
        if isinstance(row, dict) and str(row.get("key") or "").strip()
    ]
    if not capability_rows:
        _QMD_RUNTIME_CATALOG_CACHE = (now + 5.0, [])
        return []
    indicators = {
        str(row.get("key") or ""): dict(row)
        for row in catalogs.get("indicator_catalog") or []
        if isinstance(row, dict)
    }
    signals = {
        str(row.get("key") or ""): dict(row)
        for row in catalogs.get("signal_catalog") or []
        if isinstance(row, dict)
    }
    tier_by_scope = {
        "universal_ingest": "universal",
        "core_scan": "core",
        "watchlist": "watchlist",
        "strategy_run": "strategy",
        "request": "request",
        "offline": "offline",
    }
    result: list[dict[str, Any]] = []
    for row in capability_rows:
        key = str(row["key"])
        kind = str(row.get("kind") or "")
        indicator = indicators.get(key, {})
        signal = signals.get(key, {})
        detail = indicator or signal
        scope = str(row.get("execution_scope") or "watchlist")
        policy = str(row.get("configuration_policy") or "generated")
        status = str(row.get("implementation_status") or "unknown")
        priority = str(
            detail.get("priority")
            or ("p0" if scope in {"universal_ingest", "core_scan"} else "p2")
        )
        timeframes = list(
            detail.get("typical_timeframes")
            or detail.get("working_timeframes")
            or []
        )
        capability_id = (
            f"qmd.primitive.{key.replace('_', '-')}"
            if kind == "primitive"
            else f"qmd.signal.{key}"
            if kind == "market_observation"
            else f"qmd.family.{key}"
        )
        category = str(detail.get("category") or "QMD runtime")
        capability_type = (
            "system"
            if kind == "primitive"
            else "signal"
            if kind == "market_observation"
            else "market_data"
            if category in {"candles", "core"} and key in {"core_bars", "quote_mid_spread_bars"}
            else "reference"
            if category in {"reference_context", "session"}
            else "indicator"
        )
        description = str(
            detail.get("rationale")
            or detail.get("input_basis")
            or f"QMD runtime capability {row.get('label') or key}."
        )
        system_required = policy == "locked"
        enabled = status in {"implemented", "reference_only"} and scope not in {"offline"}
        result.append({
            "capability_id": capability_id,
            "capability_key": key,
            "name": str(row.get("label") or key),
            "description": description,
            "calculation": description,
            "category": category.replace("_", " ").title(),
            "provider": "QMD",
            "owner": str(row.get("producer") or "qmd"),
            "output_type": "family",
            "capability_type": capability_type,
            "priority": priority,
            "availability": status,
            "inputs": list(row.get("inputs") or []),
            "fields": list(row.get("outputs") or []),
            "timeframes": timeframes,
            "selected_timeframes": timeframes,
            "enabled": enabled,
            "configurable": policy == "configurable" and status in {"implemented", "reference_only", "strategy_specific"},
            "system_required": system_required,
            "tier": tier_by_scope.get(scope, "watchlist"),
            "execution_scope": scope,
            "allowed_scopes": list(row.get("allowed_scopes") or []),
            "configuration_policy": policy,
            "implementation_status": status,
            "operational_status": str(row.get("operational_status") or "unknown"),
            "coverage_status": "runtime_catalog",
            "cost_class": str(row.get("cost_class") or "unknown"),
            "stateful": bool(row.get("stateful")),
            "implementation_version": int(row.get("implementation_version") or 1),
            "cadence": str(row.get("cadence") or "service_owned"),
            "warm_up_bars": (
                int(row["warm_up_bars"])
                if row.get("warm_up_bars") is not None
                else None
            ),
            "persistence_policy": str(row.get("persistence_policy") or "no_default"),
            "consumers": list(row.get("allowed_scopes") or []),
            "catalog_authority": "qmd_runtime_catalog",
        })
    _QMD_RUNTIME_CATALOG_CACHE = (now + 60.0, deepcopy(result))
    return result


def _universal_ingest_capabilities() -> list[dict[str, Any]]:
    """Locked primitives applied to every accepted market event.

    These are integrity and distribution responsibilities, not optional
    Scanner indicators. Keeping them explicit prevents expensive analytical
    families from being justified as part of universal ingestion.
    """

    rows = [
        (
            "event-validation-encoding",
            "Canonical event validation and encoding",
            "Validate source fields and encode the canonical compact event without changing source evidence.",
            ["Massive quote/trade event", "condition and exchange references"],
            ["canonical compact event", "rejection reason"],
        ),
        (
            "point-in-time-source-identity",
            "Point-in-time source identity",
            "Preserve the event's source ticker and identity interval needed for causal downstream resolution.",
            ["source ticker", "event timestamp", "identity intervals"],
            ["stable source identity", "identity validity evidence"],
        ),
        (
            "event-order-sequence",
            "Event ordering and sequencing",
            "Assign deterministic arrival/order evidence and retain bounded reordering state.",
            ["source sequence", "SIP timestamp", "arrival timestamp"],
            ["ordered event", "sequence gap state", "continuation cursor"],
        ),
        (
            "nbbo-trade-state",
            "Current NBBO and eligible-trade state",
            "Maintain only the current quote/trade state required by Core Scan, bars, and live consumers.",
            ["canonical quotes", "canonical trades", "aggregation rules"],
            ["current NBBO", "last eligible trade", "market state revision"],
        ),
        (
            "freshness-quality",
            "Freshness and market-data quality",
            "Track stale, crossed, locked, gap, halt, and source-quality state without inventing replacement values.",
            ["ordered canonical events", "market clock"],
            ["freshness", "quality flags", "degradation reason"],
        ),
        (
            "compact-persistence-fanout",
            "Compact persistence and bounded fanout",
            "Persist accepted compact events and publish bounded downstream notifications with explicit durability lag.",
            ["accepted compact event", "coverage checkpoint"],
            ["q_live event row", "coverage update", "live event notification"],
        ),
    ]
    return [
        {
            "capability_id": f"qmd.primitive.{key}",
            "name": name,
            "description": description,
            "calculation": description,
            "category": "Universal ingest primitives",
            "provider": "QMD",
            "output_type": "system",
            "capability_type": "system",
            "priority": "p0",
            "availability": "implemented",
            "inputs": inputs,
            "fields": fields,
            "timeframes": [],
            "selected_timeframes": [],
            "enabled": True,
            "configurable": False,
            "system_required": True,
            "tier": "universal",
            "execution_scope": "universal_ingest",
            "allowed_scopes": ["universal_ingest"],
            "configuration_policy": "locked",
            "cost_class": "minimal",
            "stateful": key in {"event-order-sequence", "nbbo-trade-state", "freshness-quality"},
        }
        for key, name, description, inputs, fields in rows
    ]


def _normalize_discovery_capability_contract(row: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(row)
    tier = str(normalized.get("tier") or "core")
    scope_by_tier = {
        "universal": "universal_ingest",
        "core": "core_scan",
        "watchlist": "watchlist",
        "strategy": "strategy_run",
        "request": "request",
        "offline": "offline",
    }
    execution_scope = str(normalized.get("execution_scope") or scope_by_tier.get(tier, "request"))
    allowed_by_scope = {
        "universal_ingest": ["universal_ingest"],
        "core_scan": ["core_scan", "watchlist", "strategy_run", "request", "offline"],
        "watchlist": ["watchlist", "strategy_run", "request", "offline"],
        "strategy_run": ["strategy_run", "request", "offline"],
        "request": ["request", "offline"],
        "offline": ["offline"],
    }
    availability = str(normalized.get("availability") or "implemented")
    normalized.update({
        "tier": tier,
        "execution_scope": execution_scope,
        "allowed_scopes": list(normalized.get("allowed_scopes") or allowed_by_scope[execution_scope]),
        "configuration_policy": str(
            normalized.get("configuration_policy")
            or (
                "locked"
                if bool(normalized.get("system_required"))
                else "configurable"
                if bool(normalized.get("configurable"))
                else "generated"
            )
        ),
        "implementation_status": availability,
        "operational_status": str(
            normalized.get("operational_status")
            or (
                "integration_pending"
                if availability == "integration_pending"
                else "planned"
                if availability == "planned_realtime"
                else "ready"
            )
        ),
        "coverage_status": str(normalized.get("coverage_status") or "unknown"),
        "cost_class": str(
            normalized.get("cost_class")
            or {"p0": "low", "p1": "medium", "p2": "high", "p3": "offline"}.get(
                str(normalized.get("priority") or "p2"), "high"
            )
        ),
        "stateful": bool(normalized.get("stateful", execution_scope != "offline")),
        "owner": str(normalized.get("owner") or normalized.get("provider") or "unknown"),
        "implementation_version": max(1, int(normalized.get("implementation_version") or 1)),
        "cadence": str(normalized.get("cadence") or "service_owned"),
        "persistence_policy": str(normalized.get("persistence_policy") or "not_registered"),
        "consumers": list(normalized.get("consumers") or normalized.get("allowed_scopes") or []),
    })
    return normalized


def _strategy_input_capability_type(source_id: str) -> str:
    """Classify strategy inputs by semantic authority, not provider name."""

    if source_id == "market.last_price":
        return "market_data"
    if source_id.startswith("market."):
        return "reference"
    if source_id == "indicator.structure.bullish_choch":
        return "signal"
    if source_id.startswith("signal."):
        return "signal"
    if source_id.startswith("indicator."):
        return "indicator"
    return "system"


def _pending_text_intelligence_label_capabilities() -> list[dict[str, Any]]:
    """Expose future causal label signals without claiming runtime support.

    TODO(text-intelligence-label-events): After the canonical News and SEC label
    event contracts are finalized, connect their validated event projections to
    StrategyObservation.news_labeled/sec_labeled, retain label provenance in
    source_signal_ids, and promote these rows to ``implemented``. Do not derive
    either flag from process health or from the mere arrival of source text.
    """

    rows = []
    fields = {field.field_id: field for field in FIELD_DEFINITIONS}
    for source_id, name, runtime_field, inputs, description in [
        (
            "signal.news_labeled",
            "News labeled",
            "news_labeled",
            ["causally available company-news event", "validated Text Intelligence news label"],
            "Turns true only on a news event for which Text Intelligence publishes a valid point-in-time label. Missing, failed, stale, or unavailable labeling remains false.",
        ),
        (
            "signal.sec_labeled",
            "SEC labeled",
            "sec_labeled",
            ["causally available SEC filing event", "validated Text Intelligence SEC label"],
            "Turns true only on an SEC event for which Text Intelligence publishes a valid point-in-time label. Missing, failed, stale, or unavailable labeling remains false.",
        ),
    ]:
        field = fields[source_id]
        rows.append({
            "capability_id": source_id,
            "name": name,
            "description": description,
            "category": "Text Intelligence labels",
            "provider": field.owner,
            "owner": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "available_at": field.available_at,
            "output_type": "boolean",
            "capability_type": "signal",
            "priority": "p1",
            "availability": field.status,
            "inputs": inputs,
            "fields": [runtime_field],
            "calculation": description,
            "timeframes": ["event"],
            "selected_timeframes": ["event"],
            "enabled": False,
            "configurable": False,
            "system_required": False,
            "tier": "watchlist",
        })
    return rows


def _discovery_reference_capabilities() -> list[dict[str, Any]]:
    """Point-in-time scanner fields used by reusable Watchlist templates."""

    rows = [
        ("market.change_pct", "Session change", "market_data", "percent", "market.change_pct", "Last price divided by the completed previous-session close, minus one, expressed as a percentage.", "QMD bars + previous-session reference", ["1s", "10s", "30s", "1m"]),
        ("market.volume", "Session volume", "market_data", "shares", "market.volume", "Cumulative eligible trade size for the current session.", "QMD eligible trades", ["1s", "10s", "30s", "1m"]),
        ("market.relative_volume", "Relative volume", "indicator", "multiple", "market.relative_volume", "Current cumulative session volume divided by the point-in-time 20-session baseline for the same elapsed session interval.", "QMD volume + 20-session baseline", ["10s", "30s", "1m"]),
        ("reference.market_cap", "Market capitalization", "reference", "currency", "reference.market_cap", "Latest point-in-time provider market capitalization available before evaluation.", "DB-managed market snapshot", ["1d"]),
        ("reference.float_shares", "Public float", "reference", "shares", "reference.float_shares", "Tradable share supply from DB-managed reference data, with the SEC public-float estimate available as a provenance-preserving fallback.", "DB reference + SEC facts", ["1d"]),
        ("reference.short_interest", "Short interest", "reference", "shares", "reference.short_interest", "Open short positions from the latest exchange settlement report published before evaluation.", "DB-managed short-interest history", ["settlement"]),
        ("reference.short_interest_pct", "Short interest of float", "reference", "percent", "reference.short_interest_pct", "Reported short interest divided by the point-in-time public float; unavailable denominators remain unavailable.", "Short interest + public float", ["settlement"]),
        ("reference.days_to_cover", "Days to cover", "reference", "days", "reference.days_to_cover", "Reported short interest divided by the reporting source's average daily volume.", "DB-managed short-interest history", ["settlement"]),
        ("fundamental.trajectory_score", "Fundamental trajectory", "reference", "score", "fundamental.trajectory_score", "Composite 0-100 trajectory score derived from causally available SEC profitability, cash generation, balance-sheet, growth, and share-base evidence.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.quality_score", "Fundamental data quality", "reference", "score", "fundamental.quality_score", "0-100 coverage and comparability score for the SEC facts supporting the fundamental trajectory.", "SEC XBRL fact service", ["filing"]),
        ("event.ipo.days_to_event", "IPO event distance", "event", "days", "event.ipo.days_to_event", "Signed calendar days from evaluation to a point-in-time IPO event; negative values are recent IPOs and positive values are upcoming IPOs.", "DB-managed corporate-event calendar", ["event"]),
        ("event.split.days_to_event", "Split event distance", "event", "days", "event.split.days_to_event", "Signed calendar days from evaluation to the latest published stock-split execution date.", "DB-managed stock-split history", ["event"]),
    ]
    core_ids = {
        "market.change_pct",
        "market.volume",
        "reference.market_cap",
        "reference.float_shares",
    }
    required_ids = {"market.change_pct", "market.volume"}
    registered = {field.field_id: field for field in FIELD_DEFINITIONS}
    capabilities = []
    for capability_id, name, capability_type, output_type, field_id, calculation, provider, timeframes in rows:
        field = registered.get(field_id)
        availability = field.status if field is not None else "implemented"
        runnable = availability == "implemented"
        capabilities.append({
            "capability_id": capability_id,
            "name": name,
            "description": calculation,
            "category": "Scanner enrichment" if capability_id in core_ids else "Watchlist fields",
            "provider": field.owner if field is not None else provider,
            "owner": field.owner if field is not None else provider,
            "source_path": field.source_path if field is not None else "qmd://core-scanner",
            "query_plan_id": field.query_plan_id if field is not None else "qmd.runtime-capability-catalog",
            "available_at": field.available_at if field is not None else "qmd event/bar clock",
            "output_type": output_type,
            "capability_type": capability_type,
            "priority": "p0" if capability_id in required_ids else "p1",
            "availability": availability,
            "inputs": [field.source_path if field is not None else provider],
            "fields": [field_id],
            "calculation": calculation,
            "timeframes": timeframes,
            "selected_timeframes": timeframes,
            "enabled": runnable,
            "configurable": runnable and capability_id not in required_ids,
            "system_required": capability_id in required_ids,
            "tier": "core" if capability_id in core_ids else "watchlist",
        })
    return capabilities


def _market_discovery_classifications() -> list[dict[str, Any]]:
    """Reusable, non-overlapping classification definitions for Watchlist rules."""

    return [
        {"classification_id": "price.penny", "group": "Price", "name": "Penny Stocks", "description": "Last price is positive and below $1. This category is independent of market capitalization.", "minimum": 0, "maximum": 1, "unit": "usd", "source_id": "market.last_price"},
        {"classification_id": "market_cap.small", "group": "Market capitalization", "name": "Small Caps", "description": "Market capitalization is positive and below $2 billion. This consolidated bucket intentionally includes micro- and nano-cap issuers.", "minimum": 0, "maximum": 2_000_000_000, "unit": "usd", "source_id": "reference.market_cap"},
        {"classification_id": "market_cap.mid", "group": "Market capitalization", "name": "Mid Caps", "description": "Market capitalization is at least $2 billion and below $10 billion.", "minimum": 2_000_000_000, "maximum": 10_000_000_000, "unit": "usd", "source_id": "reference.market_cap"},
        {"classification_id": "market_cap.large", "group": "Market capitalization", "name": "Large Caps", "description": "Market capitalization is at least $10 billion.", "minimum": 10_000_000_000, "maximum": None, "unit": "usd", "source_id": "reference.market_cap"},
        *[
            {"classification_id": identifier, "group": "Public float", "name": name, "description": description, "minimum": minimum, "maximum": maximum, "unit": "shares", "source_id": "reference.float_shares"}
            for identifier, name, description, minimum, maximum in [
                ("float.tiny", "Tiny", "Public float below 0.5 million shares.", 0, 500_000),
                ("float.extra_small", "Extra Small", "Public float from 0.5 million up to 2 million shares.", 500_000, 2_000_000),
                ("float.small", "Small", "Public float from 2 million up to 5 million shares.", 2_000_000, 5_000_000),
                ("float.medium", "Medium", "Public float from 5 million up to 10 million shares.", 5_000_000, 10_000_000),
                ("float.medium_plus", "Medium+", "Public float from 10 million up to 20 million shares.", 10_000_000, 20_000_000),
                ("float.large", "Large", "Public float from 20 million up to 50 million shares.", 20_000_000, 50_000_000),
                ("float.extra_large", "Extra Large", "Public float from 50 million up to 100 million shares.", 50_000_000, 100_000_000),
                ("float.broad", "Broad Float", "Public float of at least 100 million shares.", 100_000_000, None),
            ]
        ],
    ]


def _watchlist_condition(condition_id: str, source_id: str, comparator: str, value: float | bool, timeframe: str = "") -> dict[str, Any]:
    return {"condition_id": condition_id, "left_source_id": source_id, "left_timeframe": timeframe, "comparator": comparator, "right_source_id": "", "right_timeframe": "", "value": value, "enabled": True}


def _watchlist_rule(rule_set_id: str, name: str, description: str, conditions: list[dict[str, Any]], *, operator: str = "all") -> dict[str, Any]:
    return {"rule_set_id": rule_set_id, "name": name, "description": description, "enabled": True, "operator": operator, "required_score": 1.0, "conditions": conditions, "scope": "watchlist"}


def _default_watchlist_rule_sets() -> list[dict[str, Any]]:
    categories = [
        _watchlist_rule("watchlist-penny-stocks", "Penny Stocks", "Retains positive-priced instruments trading below $1.", [_watchlist_condition("penny-positive", "market.last_price", "greater_than", 0, "1s"), _watchlist_condition("penny-under-one", "market.last_price", "less_than", 1, "1s")]),
        _watchlist_rule("watchlist-small-caps", "Small Caps", "Retains issuers with positive market capitalization below $2 billion.", [_watchlist_condition("small-cap-positive", "reference.market_cap", "greater_than", 0, "1d"), _watchlist_condition("small-cap-maximum", "reference.market_cap", "less_than", 2_000_000_000, "1d")]),
        _watchlist_rule("watchlist-mid-caps", "Mid Caps", "Retains issuers from $2 billion up to $10 billion in market capitalization.", [_watchlist_condition("mid-cap-minimum", "reference.market_cap", "greater_or_equal", 2_000_000_000, "1d"), _watchlist_condition("mid-cap-maximum", "reference.market_cap", "less_than", 10_000_000_000, "1d")]),
        _watchlist_rule("watchlist-large-caps", "Large Caps", "Retains issuers with at least $10 billion in market capitalization.", [_watchlist_condition("large-cap-minimum", "reference.market_cap", "greater_or_equal", 10_000_000_000, "1d")]),
    ]
    float_rules = []
    for row in _market_discovery_classifications():
        if row["group"] != "Public float":
            continue
        conditions = [_watchlist_condition(f"{row['classification_id']}-minimum", "reference.float_shares", "greater_or_equal", row["minimum"], "1d")]
        if row["maximum"] is not None:
            conditions.append(_watchlist_condition(f"{row['classification_id']}-maximum", "reference.float_shares", "less_than", row["maximum"], "1d"))
        float_name = str(row["name"])
        if not float_name.endswith("Float"):
            float_name = f"{float_name} Float"
        float_rules.append(_watchlist_rule(f"watchlist-{row['classification_id'].replace('.', '-')}", float_name, row["description"], conditions))
    return [
        *categories,
        *float_rules,
        _watchlist_rule("watchlist-positive-gainer", "Positive session gainer", "Requires a positive percentage change from the completed previous-session close.", [_watchlist_condition("positive-session-change", "market.change_pct", "greater_than", 0, "1s")]),
        _watchlist_rule("watchlist-relative-volume-gainer", "Elevated relative volume", "Requires current volume to exceed the aligned 20-session baseline.", [_watchlist_condition("relative-volume-over-baseline", "market.relative_volume", "greater_than", 1, "10s")]),
        _watchlist_rule("watchlist-price-or-volume-squeeze", "Price or Volume Squeeze", "Passes when price expands at least 5% or aligned relative volume reaches 3x.", [_watchlist_condition("squeeze-price", "market.change_pct", "greater_or_equal", 5, "1s"), _watchlist_condition("squeeze-volume", "market.relative_volume", "greater_or_equal", 3, "10s")], operator="any"),
        _watchlist_rule("watchlist-vwap-breakout", "VWAP breakout", "Requires last price to trade at least 5 basis points above current VWAP.", [{**_watchlist_condition("vwap-breakout-price", "market.last_price", "above_by_bps", 5, "1s"), "right_source_id": "indicator.vwap.value", "right_timeframe": "1s"}]),
        _watchlist_rule("watchlist-news-bullish", "Bullish news sentiment", "Requires a validated news label and a positive sentiment score of at least 0.35.", [_watchlist_condition("news-labeled-positive", "signal.news_labeled", "is_true", True, "event"), _watchlist_condition("news-positive-score", "signal.company_news.score", "greater_or_equal", 0.35, "event")]),
        _watchlist_rule("watchlist-news-bearish", "Bearish news sentiment", "Requires a validated news label and a negative sentiment score of -0.35 or lower.", [_watchlist_condition("news-labeled-negative", "signal.news_labeled", "is_true", True, "event"), _watchlist_condition("news-negative-score", "signal.company_news.score", "less_or_equal", -0.35, "event")]),
        _watchlist_rule("watchlist-sec-bullish", "Bullish SEC sentiment", "Requires a validated SEC label and a positive filing score of at least 0.35.", [_watchlist_condition("sec-labeled-positive", "signal.sec_labeled", "is_true", True, "event"), _watchlist_condition("sec-positive-score", "signal.sec_filing.score", "greater_or_equal", 0.35, "event")]),
        _watchlist_rule("watchlist-sec-bearish", "Bearish SEC sentiment", "Requires a validated SEC label and a negative filing score of -0.35 or lower.", [_watchlist_condition("sec-labeled-negative", "signal.sec_labeled", "is_true", True, "event"), _watchlist_condition("sec-negative-score", "signal.sec_filing.score", "less_or_equal", -0.35, "event")]),
        _watchlist_rule("watchlist-fundamental-bullish", "Fundamental Bullish", "Requires reliable SEC evidence and a trajectory score of at least 65.", [_watchlist_condition("fundamental-bull-quality", "fundamental.quality_score", "greater_or_equal", 60, "filing"), _watchlist_condition("fundamental-bull-score", "fundamental.trajectory_score", "greater_or_equal", 65, "filing")]),
        _watchlist_rule("watchlist-fundamental-bearish", "Fundamental Bearish", "Requires reliable SEC evidence and a trajectory score of 35 or lower.", [_watchlist_condition("fundamental-bear-quality", "fundamental.quality_score", "greater_or_equal", 60, "filing"), _watchlist_condition("fundamental-bear-score", "fundamental.trajectory_score", "less_or_equal", 35, "filing")]),
        _watchlist_rule("watchlist-ipo-window", "Past or Upcoming IPO", "Retains IPOs from 30 days before through 90 days after their event date.", [_watchlist_condition("ipo-window-start", "event.ipo.days_to_event", "greater_or_equal", -90, "event"), _watchlist_condition("ipo-window-end", "event.ipo.days_to_event", "less_or_equal", 30, "event")]),
        _watchlist_rule("watchlist-split-window", "Stock split window", "Retains symbols from 10 days before through 5 days after a published split execution date.", [_watchlist_condition("split-window-start", "event.split.days_to_event", "greater_or_equal", -5, "event"), _watchlist_condition("split-window-end", "event.split.days_to_event", "less_or_equal", 10, "event")]),
    ]


def _market_discovery_field_catalog(
    calculation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve one registry-owned field catalog for columns and filters."""

    registered = {field.field_id: field for field in FIELD_DEFINITIONS}
    capabilities = {
        str(row.get("capability_id") or ""): row for row in calculation_rows
    }
    presented_field_ids = {
        row.field_id for row in DISCOVERY_FIELD_PRESENTATIONS if row.field_id
    }
    rows: list[dict[str, Any]] = []
    for presentation in DISCOVERY_FIELD_PRESENTATIONS:
        field = registered.get(presentation.field_id)
        capability = capabilities.get(presentation.source_id, {})
        implementation_status = str(
            field.status
            if field is not None
            else capability.get("implementation_status")
            or capability.get("availability")
            or "integration_pending"
        )
        rows.append({
            "source_id": presentation.source_id,
            "field_id": presentation.field_id,
            "column_id": presentation.column_id,
            "name": presentation.label,
            "description": presentation.description,
            "semantic_type": presentation.semantic_type,
            "source": str(
                field.owner
                if field is not None
                else capability.get("owner") or capability.get("provider") or "QMD"
            ),
            "source_path": str(
                field.source_path
                if field is not None
                else capability.get("source_path") or "qmd://runtime-capability"
            ),
            "query_plan_id": str(
                field.query_plan_id
                if field is not None
                else capability.get("query_plan_id") or "qmd.runtime-capability-catalog"
            ),
            "available_at": str(
                field.available_at
                if field is not None
                else capability.get("available_at") or "QMD publication clock"
            ),
            "provenance": str(field.provenance if field is not None else "qmd"),
            "value_type": str(
                field.value_type
                if field is not None
                else capability.get("output_type") or "number"
            ),
            "unit": str(field.unit if field is not None else capability.get("output_type") or "scalar"),
            "default_visible": presentation.default_visible,
            "filterable": presentation.filterable,
            "sortable": presentation.sortable,
            "filter_operators": list(presentation.filter_operators),
            "timeframes": list(presentation.timeframes),
            "implementation_status": implementation_status,
            "registry_authority": "application_registry",
        })
    for field in sorted(FIELD_DEFINITIONS, key=lambda row: row.field_id):
        if field.field_id in presented_field_ids:
            continue
        rows.append({
            "source_id": field.field_id,
            "field_id": field.field_id,
            "column_id": "",
            "name": field.label,
            "description": f"Registered {field.group} field from {field.owner}.",
            "semantic_type": "reference",
            "source": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "available_at": field.available_at,
            "provenance": field.provenance,
            "value_type": field.value_type,
            "unit": field.unit,
            "default_visible": False,
            "filterable": False,
            "sortable": False,
            "filter_operators": [],
            "timeframes": [],
            "implementation_status": field.status,
            "registry_authority": "application_registry",
        })
    known_source_ids = {str(row["source_id"]) for row in rows}
    for capability_id, capability in sorted(capabilities.items()):
        if not capability_id or capability_id in known_source_ids:
            continue
        rows.append({
            "source_id": capability_id,
            "field_id": "",
            "column_id": "",
            "name": str(capability.get("name") or capability_id),
            "description": str(
                capability.get("calculation")
                or capability.get("description")
                or "Registered runtime capability."
            ),
            "semantic_type": str(capability.get("capability_type") or "system"),
            "source": str(capability.get("owner") or capability.get("provider") or "QMD"),
            "source_path": str(capability.get("source_path") or "qmd://runtime-capability"),
            "query_plan_id": str(
                capability.get("query_plan_id") or "qmd.runtime-capability-catalog"
            ),
            "available_at": str(
                capability.get("available_at") or "QMD publication clock"
            ),
            "provenance": "qmd",
            "value_type": str(capability.get("output_type") or "number"),
            "unit": str(capability.get("output_type") or "scalar"),
            "default_visible": False,
            "filterable": False,
            "sortable": False,
            "filter_operators": [],
            "timeframes": list(capability.get("timeframes") or []),
            "implementation_status": str(
                capability.get("implementation_status")
                or capability.get("availability")
                or "unknown"
            ),
            "registry_authority": str(
                capability.get("catalog_authority") or "application_registry"
            ),
        })
    return rows


def _watchlist_column_catalog(
    field_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [deepcopy(row) for row in field_catalog if str(row.get("column_id") or "")]


def _default_watchlist_templates(symbols: list[str], calculation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    focused = [row["capability_id"] for row in calculation_rows if row["tier"] == "watchlist" and row["enabled"]]
    common_columns = ["symbol", "company_name", "last_price", "change_pct", "volume", "relative_volume", "market_cap", "market_cap_category", "float_shares", "float_category", "short_interest_pct"]
    def template(identifier: str, name: str, description: str, rules: list[str], ranking: str, *, direction: str = "descending", refresh: int = 1000, enabled: bool = True, columns: list[str] | None = None, availability: str = "available", availability_detail: str = "") -> dict[str, Any]:
        return {"watchlist_id": identifier, "name": name, "description": description, "enabled": enabled, "origin": "system", "template": True, "availability": availability, "availability_detail": availability_detail, "source_scan_id": "qmd-core-scan", "inclusion_rule_sets": rules, "inclusion_operator": "all", "exclusion_rule_sets": [], "ranking_field": ranking, "ranking_direction": direction, "maximum_size": 10, "refresh_interval_ms": refresh, "membership_expiry": "end_of_trading_day", "membership_ttl_ms": 300000, "manual_inclusions": [], "manual_exclusions": [], "columns": columns or common_columns, "calculations": focused, "membership_history": []}
    gainers = []
    for slug, label, category_rule in [("penny", "Penny Stock", "watchlist-penny-stocks"), ("small-cap", "Small Cap", "watchlist-small-caps"), ("mid-cap", "Mid Cap", "watchlist-mid-caps"), ("large-cap", "Large Cap", "watchlist-large-caps")]:
        gainers.append(template(f"top-{slug}-gainers", f"Top {label} Gainers", f"Top positive session performers in the {label.lower()} category, ranked by percentage change.", [category_rule, "watchlist-positive-gainer"], "market.change_pct"))
        gainers.append(template(f"top-{slug}-volume-gainers", f"Top {label} Volume Gainers", f"Most unusually active {label.lower()} instruments, ranked by aligned relative volume.", [category_rule, "watchlist-relative-volume-gainer"], "market.relative_volume"))
    return [
        {"watchlist_id": "core-candidates", "name": "Core candidates", "description": "Candidate instruments produced from the Core Scan for strategy evaluation.", "enabled": True, "origin": "system", "template": False, "availability": "available", "availability_detail": "", "source_scan_id": "qmd-core-scan", "inclusion_rule_sets": [], "inclusion_operator": "all", "exclusion_rule_sets": [], "ranking_field": "liquidity-rank", "ranking_direction": "descending", "maximum_size": 250, "refresh_interval_ms": 1000, "membership_expiry": "end_of_trading_day", "membership_ttl_ms": 300000, "manual_inclusions": symbols, "manual_exclusions": [], "columns": common_columns, "calculations": focused, "membership_history": []},
        *gainers,
        template("price-or-volume-squeeze", "Price or Volume Squeeze", "Symbols with at least 5% price expansion or 3x aligned relative volume.", ["watchlist-price-or-volume-squeeze"], "market.relative_volume"),
        template("vwap-breakout", "VWAP Breakout", "Symbols trading at least 5 basis points above causal session VWAP.", ["watchlist-vwap-breakout"], "market.change_pct"),
        template("news-bullish-sentiment", "News Bullish Sentiment", "New company-news events with a validated positive Text Intelligence label.", ["watchlist-news-bullish"], "signal.company_news.score", refresh=5000, enabled=False, columns=[*common_columns, "news_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence news-label events."),
        template("news-bearish-sentiment", "News Bearish Sentiment", "New company-news events with a validated negative Text Intelligence label.", ["watchlist-news-bearish"], "signal.company_news.score", direction="ascending", refresh=5000, enabled=False, columns=[*common_columns, "news_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence news-label events."),
        template("sec-bullish-sentiment", "SEC Bullish Sentiment", "New SEC filing events with a validated positive Text Intelligence label.", ["watchlist-sec-bullish"], "signal.sec_filing.score", refresh=5000, enabled=False, columns=[*common_columns, "sec_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence SEC-label events."),
        template("sec-bearish-sentiment", "SEC Bearish Sentiment", "New SEC filing events with a validated negative Text Intelligence label.", ["watchlist-sec-bearish"], "signal.sec_filing.score", direction="ascending", refresh=5000, enabled=False, columns=[*common_columns, "sec_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence SEC-label events."),
        template("fundamental-bullish", "Fundamental Bullish", "Issuers with reliable SEC evidence and a financial trajectory score of at least 65.", ["watchlist-fundamental-bullish"], "fundamental.trajectory_score", refresh=60_000, columns=[*common_columns, "fundamental_trajectory", "fundamental_quality"]),
        template("fundamental-bearish", "Fundamental Bearish", "Issuers with reliable SEC evidence and a financial trajectory score of 35 or lower.", ["watchlist-fundamental-bearish"], "fundamental.trajectory_score", direction="ascending", refresh=60_000, columns=[*common_columns, "fundamental_trajectory", "fundamental_quality"]),
        template("past-upcoming-ipos", "Past and Upcoming IPOs", "IPOs from 30 days before through 90 days after the event date.", ["watchlist-ipo-window"], "event.ipo.days_to_event", refresh=60_000, columns=[*common_columns, "ipo_event"]),
        template("stock-splits", "Stock Splits", "Published stock splits from 10 days before through 5 days after execution.", ["watchlist-split-window"], "event.split.days_to_event", refresh=60_000, columns=[*common_columns, "split_event"]),
    ]


def _default_market_discovery(
    runtime_assignments: list[dict[str, Any]],
    rule_sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """QMD-owned discovery configuration exposed without moving calculations into Strategy."""

    qmd_rows = _qmd_family_capabilities()
    if not any(row.get("catalog_authority") == "qmd_runtime_catalog" for row in qmd_rows):
        qmd_rows = [*_universal_ingest_capabilities(), *qmd_rows]
    calculation_rows: list[dict[str, Any]] = [
        *qmd_rows,
        *_pending_text_intelligence_label_capabilities(),
        *_discovery_reference_capabilities(),
    ]
    seen: set[str] = {str(row["capability_id"]) for row in calculation_rows}
    for source in strategy_input_catalog():
        capability_id = str(source.get("source_id") or "")
        if not capability_id or capability_id in seen:
            continue
        seen.add(capability_id)
        provider = str(source.get("provider") or "").lower()
        category = str(source.get("category") or "").lower()
        tier = (
            "core"
            if capability_id
            in {
                "market.last_price",
                "market.previous_close",
                "market.change_pct",
                "market.volume",
            }
            else "watchlist"
        )
        calculation_rows.append({
            "capability_id": capability_id,
            "name": str(source.get("label") or capability_id),
            "description": str(source.get("summary") or "Published QMD observation."),
            "category": str(source.get("category") or source.get("provider") or "Market observations"),
            "provider": str(source.get("provider") or "QMD"),
            "output_type": str(source.get("value_type") or "number"),
            "capability_type": _strategy_input_capability_type(capability_id),
            "priority": "p0" if tier == "core" else "p2",
            "availability": "implemented",
            "inputs": [str(source.get("provider") or "QMD")],
            "fields": [str(source.get("field") or source.get("source_id") or capability_id)],
            "calculation": str(source.get("summary") or "Published from causally available QMD observations."),
            "timeframes": list(source.get("timeframes") or []),
            "selected_timeframes": list(source.get("timeframes") or []),
            "enabled": True,
            "configurable": tier == "watchlist",
            "system_required": tier == "core",
            "tier": tier,
        })
    operational = [
        ("instrument-identity", "Instrument eligibility and identity", "Reference-backed identity, listing, venue, and tradability eligibility.", "Reference and eligibility", "core"),
        ("market-quality", "Market-data freshness and quality", "Freshness, completeness, crossed-market, halt, and stale-data checks.", "Data quality", "core"),
        ("liquidity-rank", "Liquidity and candidate ranking", "Price, volume, spread, activity, liquidity, and base candidate rank.", "Ranking", "core"),
        ("news-events", "News observations", "Point-in-time company-news events and scored downstream signals.", "Event intelligence", "watchlist"),
        ("sec-events", "SEC observations", "Point-in-time filing events and derived issuer signals.", "Event intelligence", "watchlist"),
        ("membership-history", "Watchlist membership history", "Append-only add, remove, expiry, and override events with causal reasons.", "Audit and history", "watchlist"),
    ]
    for capability_id, name, description, category, tier in operational:
        if capability_id in seen:
            continue
        required = capability_id in {"instrument-identity", "market-quality", "liquidity-rank", "membership-history"}
        calculation_rows.append({
            "capability_id": capability_id,
            "name": name,
            "description": description,
            "category": category,
            "provider": "QMD",
            "output_type": "system",
            "capability_type": "event" if capability_id in {"news-events", "sec-events"} else "system",
            "priority": "p0" if required else "p2",
            "availability": "implemented",
            "inputs": ["QMD services"],
            "fields": [capability_id],
            "calculation": description,
            "timeframes": [],
            "selected_timeframes": [],
            "enabled": True,
            "configurable": not required,
            "system_required": required,
            "tier": tier,
        })
    calculation_rows = [
        _normalize_discovery_capability_contract(row) for row in calculation_rows
    ]
    symbols = sorted({
        str(row.get("ticker") or "").upper()
        for row in runtime_assignments
        if str(row.get("ticker") or "").strip()
    })
    merged_rule_sets: list[dict[str, Any]] = []
    rule_set_ids: set[str] = set()
    for rule_set in [*(rule_sets or []), *_default_watchlist_rule_sets()]:
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        if not rule_set_id or rule_set_id in rule_set_ids:
            continue
        rule_set_ids.add(rule_set_id)
        merged_rule_sets.append(deepcopy(rule_set))
    field_catalog = _market_discovery_field_catalog(calculation_rows)
    return {
        "security_universe": {
            "universe_id": "qmd-security-universe",
            "name": "QMD Security Universe",
            "description": "The broad authoritative instrument set eligible for the Core Scan.",
            "enabled": True,
            "configurable": False,
        },
        "core_scan": {
            "scan_id": "qmd-core-scan",
            "name": "Core Scan",
            "description": "Required low-cost calculations evaluated across the complete QMD Security Universe.",
            "refresh_interval_ms": 1000,
            "published": True,
            "calculations": calculation_rows,
        },
        "classifications": _market_discovery_classifications(),
        "field_catalog": field_catalog,
        "column_catalog": _watchlist_column_catalog(field_catalog),
        "rule_sets": merged_rule_sets,
        "watchlists": _default_watchlist_templates(symbols, calculation_rows),
    }


def _default_profile_composition() -> dict[str, Any]:
    return {
        "watchlist_id": "core-candidates",
        "portfolio_policy_id": "default",
        "oms_profile_id": "adaptive-regular",
        "account_keys": ["replay"],
        "allowed_environments": ["replay", "backtest", "backtest_debug"],
        "action_authority": _default_action_authority(),
    }


def _default_draft() -> dict[str, Any]:
    definition = get_strategy_definition(STRATEGY_ID, STRATEGY_REVISION)
    parameters = deepcopy(definition.get("config", {}).get("parameters") or default_long_momentum_parameters())
    system_profiles = [
        _strategy_profile(
            "long-momentum-balanced",
            "Long Momentum · Balanced",
            "Balanced Long Momentum template with breakout confirmation, protection, and re-entry.",
            parameters,
            origin="system",
        ),
    ]
    system_profiles[0]["protected"] = True
    profile_templates = [
        _strategy_profile(
            "long-momentum-template",
            "Long Momentum · Balanced",
            "Balanced Long Momentum template with breakout confirmation, protection, and re-entry.",
            parameters,
            origin="system",
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
    discovery = _default_market_discovery(
        runtime_assignments,
        list(system_profiles[0].get("rule_set_catalog") or []),
    )
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
    default_account_keys = [
        str(binding["account_key"])
        for binding in bindings
        if bool(binding.get("enabled", True)) and "replay" in set(binding.get("modes") or [])
    ] or [str(bindings[0]["account_key"])]
    for profile in [*system_profiles, *profile_templates]:
        profile["composition"]["portfolio_policy_id"] = policy["policy_id"]
        profile["composition"]["account_keys"] = default_account_keys
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
        "market_discovery": discovery,
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


def _assert_published_profiles_unchanged(
    current: dict[str, Any], candidate: dict[str, Any]
) -> None:
    current_profiles = {
        str(row.get("profile_id")): row
        for row in dict(current.get("strategy") or {}).get("profiles") or []
        if str(row.get("publication_status") or "draft") == "published"
    }
    candidate_profiles = {
        str(row.get("profile_id")): row
        for row in dict(candidate.get("strategy") or {}).get("profiles") or []
    }
    for profile_id, published in current_profiles.items():
        proposed = candidate_profiles.get(profile_id)
        if proposed is None:
            raise ValueError(
                "Published strategies are immutable and cannot be deleted; clone one to create a new draft"
            )
        if _canonical_profile_content(proposed) != _canonical_profile_content(published):
            raise ValueError(
                f"Published Strategy {published.get('name')} is immutable; clone it to create a modified draft"
            )


def _canonical_profile_content(profile: dict[str, Any]) -> str:
    return json.dumps(profile, separators=(",", ":"), sort_keys=True)


def _validate_market_discovery(section: dict[str, Any]) -> None:
    universe = dict(section.get("security_universe") or {})
    if not str(universe.get("universe_id") or ""):
        raise ValueError("Market Discovery requires one QMD Security Universe")
    core_scan = dict(section.get("core_scan") or {})
    if not str(core_scan.get("scan_id") or ""):
        raise ValueError("Market Discovery requires one Core Scan")
    calculations = list(core_scan.get("calculations") or [])
    calculation_ids = _unique_ids(calculations, "capability_id", "QMD capability")
    required_calculation_ids = {
        str(row.get("capability_id") or "")
        for row in _default_market_discovery([], []).get("core_scan", {}).get("calculations", [])
        if bool(row.get("system_required"))
    }
    missing_required = required_calculation_ids - calculation_ids
    if missing_required:
        raise ValueError(
            "Market Discovery is missing required QMD capabilities: "
            + ", ".join(sorted(missing_required))
        )
    rule_sets = list(section.get("rule_sets") or [])
    rule_set_ids = _unique_ids(
        rule_sets,
        "rule_set_id",
        "Watchlist rule set",
    )
    for rule_set in rule_sets:
        _validate_rule_set_definition(rule_set, f"Watchlist rule set {rule_set.get('name')}")
    field_catalog = list(section.get("field_catalog") or [])
    field_source_ids = _unique_ids(
        field_catalog,
        "source_id",
        "Market Discovery field",
    )
    if not field_source_ids:
        raise ValueError("Market Discovery requires a registered field catalog")
    field_by_source = {
        str(row.get("source_id") or ""): row for row in field_catalog
    }
    for rule_set in rule_sets:
        if str(rule_set.get("scope") or "strategy") != "watchlist":
            continue
        for condition in rule_set.get("conditions") or []:
            source_id = str(condition.get("left_source_id") or "")
            field = field_by_source.get(source_id)
            if field is None:
                raise ValueError(
                    f"Watchlist rule set {rule_set.get('name')} references unknown field {source_id}"
                )
            comparator = str(condition.get("comparator") or "")
            if not bool(field.get("filterable")) or comparator not in set(
                field.get("filter_operators") or []
            ):
                raise ValueError(
                    f"Watchlist rule set {rule_set.get('name')} cannot use {comparator} on {source_id}"
                )
            right_source_id = str(condition.get("right_source_id") or "")
            if right_source_id and right_source_id not in field_by_source:
                raise ValueError(
                    f"Watchlist rule set {rule_set.get('name')} references unknown comparison field {right_source_id}"
                )
    for calculation in calculations:
        execution_scope = str(calculation.get("execution_scope") or "")
        if execution_scope not in DISCOVERY_EXECUTION_SCOPES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown execution scope {execution_scope}"
            )
        allowed_scopes = {
            str(value) for value in calculation.get("allowed_scopes") or [] if str(value)
        }
        if execution_scope not in allowed_scopes:
            raise ValueError(
                f"QMD capability {calculation.get('name')} does not allow its default execution scope"
            )
        if allowed_scopes - DISCOVERY_EXECUTION_SCOPES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown allowed execution scopes"
            )
        configuration_policy = str(calculation.get("configuration_policy") or "")
        if configuration_policy not in DISCOVERY_CONFIGURATION_POLICIES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown configuration policy"
            )
        if execution_scope == "universal_ingest" and (
            configuration_policy != "locked"
            or not bool(calculation.get("system_required"))
            or not bool(calculation.get("enabled"))
        ):
            raise ValueError(
                f"Universal Ingest capability {calculation.get('name')} must remain enabled and locked"
            )
        if bool(calculation.get("system_required")) and not bool(calculation.get("enabled")):
            raise ValueError(
                f"Required QMD capability {calculation.get('name')} cannot be disabled"
            )
        supported_timeframes = {
            str(value) for value in calculation.get("timeframes") or [] if str(value)
        }
        selected_timeframes = [
            str(value)
            for value in calculation.get("selected_timeframes") or []
            if str(value)
        ]
        unknown_timeframes = set(selected_timeframes) - supported_timeframes
        if unknown_timeframes:
            raise ValueError(
                f"QMD capability {calculation.get('name')} selects unsupported calculation cadences: "
                + ", ".join(sorted(unknown_timeframes))
            )
        if bool(calculation.get("enabled")) and supported_timeframes and not selected_timeframes:
            raise ValueError(
                f"Enabled QMD capability {calculation.get('name')} requires at least one calculation cadence"
            )
    watchlists = list(section.get("watchlists") or [])
    if not watchlists:
        raise ValueError("Market Discovery requires at least one Watchlist")
    _unique_ids(watchlists, "watchlist_id", "Watchlist")
    column_catalog = list(section.get("column_catalog") or [])
    column_ids = _unique_ids(column_catalog, "column_id", "Watchlist column")
    if not column_ids:
        raise ValueError("Market Discovery requires a Watchlist column catalog")
    for column in column_catalog:
        source_id = str(column.get("source_id") or "")
        field = field_by_source.get(source_id)
        if field is None or str(field.get("column_id") or "") != str(
            column.get("column_id") or ""
        ):
            raise ValueError(
                f"Watchlist column {column.get('column_id')} is not generated from the field registry"
            )
    _unique_ids(list(section.get("classifications") or []), "classification_id", "Market classification")
    for watchlist in watchlists:
        availability = str(watchlist.get("availability") or "available")
        if availability not in {"available", "integration_pending"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown availability state")
        if availability == "integration_pending" and bool(watchlist.get("enabled")):
            raise ValueError(f"Watchlist {watchlist.get('name')} cannot be enabled until its upstream integration is available")
        if str(watchlist.get("source_scan_id") or "") != str(core_scan.get("scan_id")):
            raise ValueError(f"Watchlist {watchlist.get('name')} references an unknown Core Scan")
        if int(watchlist.get("maximum_size") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} maximum size must be positive")
        if int(watchlist.get("refresh_interval_ms") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} refresh interval must be positive")
        expiry = str(watchlist.get("membership_expiry") or "end_of_trading_day")
        if expiry not in {"end_of_trading_day", "time_to_live", "never"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown membership expiry policy")
        if int(watchlist.get("membership_ttl_ms") or 0) < 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} membership TTL cannot be negative")
        if expiry == "time_to_live" and int(watchlist.get("membership_ttl_ms") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} membership TTL must be positive")
        unknown = set(watchlist.get("calculations") or []) - calculation_ids
        if unknown:
            raise ValueError(f"Watchlist {watchlist.get('name')} references unknown QMD capabilities")
        if str(watchlist.get("ranking_field") or "") not in calculation_ids:
            raise ValueError(f"Watchlist {watchlist.get('name')} references an unknown ranking field")
        if str(watchlist.get("ranking_direction") or "descending") not in {"ascending", "descending"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown ranking direction")
        if str(watchlist.get("inclusion_operator") or "all") not in {"all", "any"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has unsupported inclusion logic")
        unknown_columns = set(watchlist.get("columns") or []) - column_ids
        if unknown_columns:
            raise ValueError(f"Watchlist {watchlist.get('name')} references unknown display columns")
        unknown_rules = (
            set(watchlist.get("inclusion_rule_sets") or [])
            | set(watchlist.get("exclusion_rule_sets") or [])
        ) - rule_set_ids
        if unknown_rules:
            raise ValueError(f"Watchlist {watchlist.get('name')} references unknown rule sets")


def _compile_profile_run_plan(candidate: dict[str, Any], profile: dict[str, Any]) -> None:
    """Compile one user-authored Strategy into backend-only Run Plan contracts."""

    composition = {**_default_profile_composition(), **dict(profile.get("composition") or {})}
    discovery = dict(candidate.get("market_discovery") or {})
    watchlist = next(
        (
            row
            for row in discovery.get("watchlists") or []
            if str(row.get("watchlist_id")) == str(composition.get("watchlist_id"))
        ),
        None,
    )
    if watchlist is None:
        raise ValueError(f"Strategy {profile.get('name')} references an unknown Watchlist")
    profile_id = str(profile["profile_id"])
    existing_plans = list(dict(candidate.get("run_plans") or {}).get("plans") or [])
    existing_for_profile = next(
        (row for row in existing_plans if str(row.get("profile_id")) == profile_id),
        None,
    )
    run_plan_id = str(
        dict(existing_for_profile or {}).get("run_plan_id") or f"strategy-{profile_id}"
    )
    universe_id = f"watchlist-{watchlist['watchlist_id']}"
    symbols = sorted(
        set(str(value).strip().upper() for value in watchlist.get("manual_inclusions") or [] if str(value).strip())
        - set(str(value).strip().upper() for value in watchlist.get("manual_exclusions") or [] if str(value).strip())
    )
    runtime_assignments = [
        deepcopy(row)
        for plan in existing_plans
        for row in plan.get("runtime_assignments") or []
        if str(row.get("ticker") or "").upper() in set(symbols)
    ]
    universe = {
        "universe_id": universe_id,
        "name": str(watchlist.get("name") or "Strategy watchlist"),
        "description": str(watchlist.get("description") or "QMD-resolved Watchlist snapshot."),
        "source": "watchlist",
        "symbols": symbols,
        "scanner_view_id": str(watchlist.get("watchlist_id") or ""),
        "watchlist_snapshot": deepcopy(watchlist),
        "enabled": bool(watchlist.get("enabled", True)),
    }
    account_keys = [str(value) for value in composition.get("account_keys") or []]
    mandates = list(dict(candidate["portfolio"]).get("mandates") or [])
    selected_mandates = [row for row in mandates if str(row.get("account_key")) in account_keys]
    if not selected_mandates:
        selected_mandates = mandates
    for mandate in selected_mandates:
        mandate["run_plan_id"] = run_plan_id
    candidate["portfolio"]["mandates"] = selected_mandates
    candidate["run_plans"] = {
        "universes": [universe],
        "plans": [{
            "run_plan_id": run_plan_id,
            "name": str(profile.get("name") or "Published Strategy"),
            "description": "Compiled runtime contract for an immutable published Strategy.",
            "profile_id": profile_id,
            "oms_profile_id": str(composition.get("oms_profile_id") or ""),
            "universe_id": universe_id,
            "book_id": "default",
            "action_authority": deepcopy(composition.get("action_authority") or _default_action_authority()),
            "campaign_lifecycle": _default_campaign_policy(),
            "safety_supervisor": _default_safety_supervisor(),
            "mandate_ids": [str(row.get("mandate_id")) for row in selected_mandates],
            "enabled": True,
            "allowed_environments": list(composition.get("allowed_environments") or []),
            "runtime_assignments": runtime_assignments,
            "observation_dependencies": _compiled_observation_dependencies(
                profile,
                list(discovery.get("core_scan", {}).get("calculations") or []),
            ),
            "compiled": True,
        }],
    }


def _compiled_observation_dependencies(
    profile: dict[str, Any],
    capability_catalog_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    definition = get_strategy_definition(
        str(profile.get("definition_id") or ""),
        int(profile.get("definition_revision") or 0) or None,
    )
    taxonomy = StrategyTaxonomy.from_payload(
        definition.get("taxonomy") or dict(definition.get("config") or {}).get("taxonomy")
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for input_kind, refs in (("indicator", taxonomy.indicators), ("signal", taxonomy.signals)):
        for ref in refs:
            capability_key = ref.capability_key or ref.key
            producer = ref.producer or "strategy_payload"
            key = (producer, capability_key)
            row = grouped.setdefault(key, {
                "producer": producer,
                "capability_key": capability_key,
                "input_kinds": set(),
                "input_keys": set(),
                "timeframes": set(),
                "required": False,
            })
            row["input_kinds"].add(input_kind)
            row["input_keys"].add(ref.key)
            if ref.timeframe:
                row["timeframes"].add(ref.timeframe.lower())
            row["required"] = bool(row["required"] or ref.required)
    compiled: list[dict[str, Any]] = []
    for _, row in sorted(grouped.items()):
        compiled.append({
            **row,
            "input_kinds": sorted(row["input_kinds"]),
            "input_keys": sorted(row["input_keys"]),
            "timeframes": sorted(row["timeframes"]),
        })
    return _with_qmd_dependency_metadata(compiled, capability_catalog_rows)


def _with_qmd_dependency_metadata(
    dependencies: list[dict[str, Any]],
    capability_catalog_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    capability_metadata = {
        str(row.get("capability_key") or ""): row
        for row in capability_catalog_rows or []
        if str(row.get("capability_key") or "")
    }
    result: list[dict[str, Any]] = []
    for dependency in dependencies:
        output = deepcopy(dependency)
        if str(output.get("producer") or "") == "qmd":
            metadata = capability_metadata.get(str(output.get("capability_key") or ""))
            warm_up_bars = metadata.get("warm_up_bars") if metadata else None
            output["warm_up"] = {
                "bars": int(warm_up_bars) if warm_up_bars is not None else None,
                "status": (
                    "required"
                    if warm_up_bars is not None and int(warm_up_bars) > 0
                    else "not_required"
                    if metadata is not None
                    else "catalog_unavailable"
                ),
            }
            output["capability_revision"] = (
                int(metadata.get("implementation_version") or 1)
                if metadata is not None
                else None
            )
        result.append(output)
    return result


def _validate_draft(draft: dict[str, Any], *, require_runtime_ready: bool = True) -> None:
    missing = CONFIGURATION_SECTIONS - set(draft)
    if missing:
        raise ValueError(f"Trading configuration is missing sections: {', '.join(sorted(missing))}")
    _validate_market_discovery(dict(draft["market_discovery"]))
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
        rule_set_catalog = list(profile.get("rule_set_catalog") or [])
        _validate_strategy_lifecycle(lifecycle, rule_set_catalog)
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
        if modes.intersection({"paper", "live"}):
            if str(row.get("source_account_id") or "").strip():
                raise ValueError(
                    f"Account {row.get('account_key')} must resolve its broker account id "
                    "server-side instead of storing source_account_id"
                )
            reference = str(row.get("source_account_env") or "").strip()
            if not reference:
                raise ValueError(
                    f"Account {row.get('account_key')} requires a server-side broker "
                    "account environment key for Paper or Live"
                )
            if require_runtime_ready and not _resolved_source_account_id(row):
                raise ValueError(
                    f"Account {row.get('account_key')} requires an exact broker account id "
                    f"resolved from {reference} for Paper or Live"
                )
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
    watchlist_ids = {
        str(row.get("watchlist_id") or "")
        for row in dict(draft["market_discovery"]).get("watchlists") or []
    }
    for profile in profiles:
        composition = {**_default_profile_composition(), **dict(profile.get("composition") or {})}
        if str(composition.get("watchlist_id") or "") not in watchlist_ids:
            raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown Watchlist")
        if str(composition.get("oms_profile_id") or "") not in oms_ids:
            raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown OMS profile")
        unknown_accounts = set(composition.get("account_keys") or []) - account_keys
        if unknown_accounts:
            raise ValueError(f"Strategy Profile {profile.get('name')} references unknown accounts")
        environments = set(composition.get("allowed_environments") or [])
        if not environments or not environments <= SUPPORTED_MODES:
            raise ValueError(f"Strategy Profile {profile.get('name')} has unsupported environments")
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
        if require_runtime_ready and source == "scanner_view":
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
            if str(universe.get("source") or "") == "watchlist":
                missing_identity = []
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
            for action in ("initial_entry", "add", "reentry")
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
    source_schema_version = int(raw.get("schema_version") or 0)
    if (
        isinstance(raw.get("run_plans"), dict)
        or isinstance(raw.get("assignments"), dict)
    ) and isinstance(raw.get("strategy"), dict):
        result = deepcopy(raw)
        defaults = _default_draft()
        result["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        result["market_discovery"] = deepcopy(
            result.get("market_discovery") or defaults["market_discovery"]
        )
        result["market_discovery"]["classifications"] = deepcopy(
            defaults["market_discovery"].get("classifications") or []
        )
        result["market_discovery"]["field_catalog"] = deepcopy(
            defaults["market_discovery"].get("field_catalog") or []
        )
        result["market_discovery"]["column_catalog"] = deepcopy(
            defaults["market_discovery"].get("column_catalog") or []
        )
        current_rule_sets = {
            str(row.get("rule_set_id") or ""): deepcopy(row)
            for row in result["market_discovery"].get("rule_sets") or []
        }
        result["market_discovery"]["rule_sets"] = [
            {**default_rule_set, **current_rule_sets.pop(str(default_rule_set.get("rule_set_id") or ""), {})}
            for default_rule_set in defaults["market_discovery"].get("rule_sets") or []
        ] + list(current_rule_sets.values())
        default_calculations = list(defaults["market_discovery"]["core_scan"]["calculations"])
        current_calculations = {
            str(row.get("capability_id") or ""): deepcopy(row)
            for row in dict(result["market_discovery"].get("core_scan") or {}).get("calculations") or []
        }
        merged_calculations: list[dict[str, Any]] = []
        for default_calculation in default_calculations:
            capability_id = str(default_calculation.get("capability_id") or "")
            calculation = {**default_calculation, **current_calculations.pop(capability_id, {})}
            calculation.update({
                "name": str(default_calculation.get("name") or capability_id),
                "description": str(default_calculation.get("description") or ""),
                "category": str(default_calculation.get("category") or ""),
                "provider": str(default_calculation.get("provider") or "QMD"),
                "output_type": str(default_calculation.get("output_type") or "number"),
                "capability_type": str(default_calculation.get("capability_type") or "indicator"),
                "priority": str(default_calculation.get("priority") or "p2"),
                "availability": str(default_calculation.get("availability") or "implemented"),
                "inputs": list(default_calculation.get("inputs") or []),
                "fields": list(default_calculation.get("fields") or []),
                "calculation": str(default_calculation.get("calculation") or default_calculation.get("description") or ""),
                "timeframes": list(default_calculation.get("timeframes") or []),
                "configurable": bool(default_calculation.get("configurable")),
                "system_required": bool(default_calculation.get("system_required")),
                "tier": str(default_calculation.get("tier") or "core"),
                "execution_scope": str(default_calculation.get("execution_scope") or "core_scan"),
                "allowed_scopes": list(default_calculation.get("allowed_scopes") or []),
                "configuration_policy": str(default_calculation.get("configuration_policy") or "generated"),
                "implementation_status": str(default_calculation.get("implementation_status") or default_calculation.get("availability") or "implemented"),
                "operational_status": str(default_calculation.get("operational_status") or "unknown"),
                "coverage_status": str(default_calculation.get("coverage_status") or "unknown"),
                "cost_class": str(default_calculation.get("cost_class") or "unknown"),
                "stateful": bool(default_calculation.get("stateful")),
            })
            supported_timeframes = list(calculation["timeframes"])
            selected_timeframes = [
                str(value)
                for value in calculation.get("selected_timeframes") or supported_timeframes
                if str(value) in supported_timeframes
            ]
            calculation["selected_timeframes"] = selected_timeframes or supported_timeframes
            if calculation["system_required"]:
                calculation["enabled"] = True
            merged_calculations.append(calculation)
        for calculation in current_calculations.values():
            supported_timeframes = [
                str(value) for value in calculation.get("timeframes") or [] if str(value)
            ]
            selected_timeframes = [
                str(value)
                for value in calculation.get("selected_timeframes") or supported_timeframes
                if str(value) in supported_timeframes
            ]
            calculation["selected_timeframes"] = selected_timeframes or supported_timeframes
            merged_calculations.append(calculation)
        result["market_discovery"]["core_scan"]["calculations"] = merged_calculations
        discovery_calculation_ids = {
            str(row.get("capability_id") or "")
            for row in dict(result["market_discovery"].get("core_scan") or {}).get("calculations") or []
        }
        default_watchlists = list(defaults["market_discovery"].get("watchlists") or [])
        current_watchlists = {
            str(row.get("watchlist_id") or ""): deepcopy(row)
            for row in result["market_discovery"].get("watchlists") or []
        }
        merged_watchlists: list[dict[str, Any]] = []
        for default_watchlist in default_watchlists:
            watchlist_id = str(default_watchlist.get("watchlist_id") or "")
            current_watchlist = current_watchlists.pop(watchlist_id, {})
            merged = {**default_watchlist, **current_watchlist}
            if bool(default_watchlist.get("template")) and str(default_watchlist.get("origin")) == "system":
                previous_availability = str(current_watchlist.get("availability") or "")
                merged["availability"] = default_watchlist.get("availability", "available")
                merged["availability_detail"] = default_watchlist.get("availability_detail", "")
                if previous_availability == "integration_pending" and merged["availability"] == "available":
                    merged["enabled"] = bool(default_watchlist.get("enabled", True))
            merged_watchlists.append(merged)
        merged_watchlists.extend(current_watchlists.values())
        result["market_discovery"]["watchlists"] = merged_watchlists
        column_ids = {
            str(row.get("column_id") or "")
            for row in result["market_discovery"].get("column_catalog") or []
        }
        for watchlist in result["market_discovery"].get("watchlists") or []:
            watchlist.setdefault("membership_expiry", "end_of_trading_day")
            watchlist.setdefault("inclusion_operator", "all")
            watchlist.setdefault("ranking_direction", "descending")
            watchlist.setdefault("origin", "user")
            watchlist.setdefault("template", False)
            watchlist.setdefault("availability", "available")
            watchlist.setdefault("availability_detail", "")
            watchlist["columns"] = [
                str(column_id)
                for column_id in watchlist.get("columns") or []
                if str(column_id) in column_ids
            ] or [
                str(row.get("column_id") or "")
                for row in result["market_discovery"].get("column_catalog") or []
                if bool(row.get("default_visible"))
            ]
            if str(watchlist.get("ranking_field") or "") not in discovery_calculation_ids:
                watchlist["ranking_field"] = "liquidity-rank"
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
            profile.setdefault(
                "publication_status",
                "template" if str(profile.get("origin") or "") == "system" else "draft",
            )
            profile.setdefault("derived_from_profile_id", "")
            profile["composition"] = {
                **_default_profile_composition(),
                **dict(profile.get("composition") or {}),
            }
            if str(profile.get("publication_status")) == "published":
                profile["editable"] = False
            elif str(profile.get("origin") or "") == "system":
                profile["editable"] = False
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
            lifecycle = _migrate_lifecycle_v14(lifecycle, parameters)
            lifecycle.pop("re_evaluation", None)
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
            _migrate_profile_rule_catalog(profile)
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
        for template in result["strategy"]["profile_templates"]:
            template["publication_status"] = "template"
            template["editable"] = False
            template.setdefault("derived_from_profile_id", "")
            template["composition"] = {
                **_default_profile_composition(),
                **dict(template.get("composition") or {}),
            }
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
        profiles_by_id = {
            str(profile.get("profile_id") or ""): profile
            for profile in result["strategy"].get("profiles") or []
        }
        for run_plan in result["run_plans"].get("plans") or []:
            run_plan.setdefault("universe_id", fallback_universe)
            run_plan.setdefault("book_id", "default")
            calculations = list(
                dict(result.get("market_discovery") or {})
                .get("core_scan", {})
                .get("calculations")
                or []
            )
            if "observation_dependencies" not in run_plan:
                profile = profiles_by_id.get(str(run_plan.get("profile_id") or ""))
                run_plan["observation_dependencies"] = (
                    _compiled_observation_dependencies(profile, calculations)
                    if profile
                    else []
                )
            else:
                run_plan["observation_dependencies"] = _with_qmd_dependency_metadata(
                    list(run_plan.get("observation_dependencies") or []),
                    calculations,
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
            if source_schema_version < 19:
                _migrate_server_side_broker_binding(binding)
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
        _migrate_server_side_broker_binding(binding)
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


def _migrate_server_side_broker_binding(binding: dict[str, Any]) -> None:
    modes = set(binding.get("modes") or [])
    if not modes.intersection({"paper", "live"}):
        return
    if not str(binding.get("source_account_env") or "").strip():
        if modes == {"paper"} or str(binding.get("account_class") or "") == "paper":
            binding["source_account_env"] = "IBKR_PAPER_ACCOUNT_ID"
        elif modes == {"live"}:
            binding["source_account_env"] = "IBKR_CASH_ACCOUNT_ID"
    binding["source_account_id"] = ""


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
    profile = {
        "profile_id": profile_id,
        "revision": 1,
        "name": name,
        "description": description,
        "definition_id": STRATEGY_ID,
        "definition_revision": STRATEGY_REVISION,
        "origin": origin,
        "editable": origin == "user",
        "protected": protected,
        "enabled": True,
        "publication_status": "template" if origin == "system" else "draft",
        "derived_from_profile_id": "",
        "composition": _default_profile_composition(),
        "lifecycle": _default_strategy_lifecycle(parameters),
        "parameters": _parameters_without_lifecycle(parameters),
        "capabilities": capabilities,
    }
    _migrate_profile_rule_catalog(profile)
    return profile


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


def _validate_strategy_lifecycle(
    lifecycle: dict[str, Any],
    raw_rule_set_catalog: list[dict[str, Any]],
) -> None:
    required = {"phase_modes", "trading_behavior", "initial_entry", "reentry", "exit"}
    missing = required - set(lifecycle)
    if missing:
        raise ValueError(
            f"Strategy lifecycle is missing: {', '.join(sorted(missing))}"
        )
    phase_modes = dict(lifecycle["phase_modes"])
    required_phases = {"initial_entry", "manage", "reentry", "exit"}
    if set(phase_modes) != required_phases or any(
        str(phase_modes.get(phase_name) or "") not in {"automatic", "manual"}
        for phase_name in required_phases
    ):
        raise ValueError(
            "Strategy phase modes must define initial_entry, manage, reentry, and exit as automatic or manual"
        )
    behavior = dict(lifecycle["trading_behavior"])
    if str(behavior.get("side") or "") not in {"long", "short"}:
        raise ValueError("Each Strategy Profile must use exactly one side: long or short")
    sessions = set(behavior.get("eligible_sessions") or [])
    if not sessions or not sessions <= {"premarket", "regular", "after_hours"}:
        raise ValueError("Strategy eligible sessions are unsupported")
    rule_set_ids = _unique_ids(
        raw_rule_set_catalog,
        "rule_set_id",
        "Strategy rule set",
    )
    rule_set_catalog = {
        str(rule_set["rule_set_id"]): dict(rule_set)
        for rule_set in raw_rule_set_catalog
    }
    for rule_set in raw_rule_set_catalog:
        _validate_rule_set_definition(dict(rule_set), f"Rule set {rule_set.get('name')}")
    initial_entry = dict(lifecycle["initial_entry"])
    runtime_rules = {
        "trigger": _materialize_rule_stage(dict(initial_entry.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(initial_entry.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(initial_entry.get("blockers") or {}), rule_set_catalog),
    }
    for stage_name in ("opportunity", "confirmation", "blockers"):
        _validate_rule_stage(
            dict(initial_entry.get(stage_name) or {}),
            f"Initial entry {stage_name}",
            rule_set_ids,
        )
    parameters = default_long_momentum_parameters()
    parameters["entry_rules"] = runtime_rules
    resolve_long_momentum_parameters(parameters)
    _validate_capital_request(dict(initial_entry.get("capital_request") or {}), "Initial entry")
    _validate_order_intent(dict(initial_entry.get("order_intent") or {}), "Initial entry")
    add_steps = list(initial_entry.get("add_steps") or [])
    _unique_ids(add_steps, "step_id", "Initial-entry add step")
    for step in add_steps:
        _validate_rule_stage(dict(step.get("rules") or {}), f"Add step {step.get('name')}", rule_set_ids)
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
        "trigger": _materialize_rule_stage(dict(reentry_rules.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(reentry_rules.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(reentry_rules.get("blockers") or {}), rule_set_catalog),
    }
    for stage_name in ("opportunity", "confirmation", "blockers"):
        _validate_rule_stage(
            dict(reentry_rules.get(stage_name) or {}),
            f"Reentry {stage_name}",
            rule_set_ids,
        )
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
        _validate_rule_stage(dict(route.get("rules") or {}), f"Exit rule set {route.get('name')}", rule_set_ids)
        _validate_order_intent(dict(route.get("order_intent") or {}), f"Exit rule set {route.get('name')}")
        position_fraction = float(route.get("position_fraction") or 0)
        if not 0 < position_fraction <= 1:
            raise ValueError(f"Exit rule set {route.get('name')} position fraction must be between zero and one")


def _validate_rule_set_definition(rule_set: dict[str, Any], label: str) -> None:
    operator = str(rule_set.get("operator") or "")
    if operator not in {"all", "any", "score"}:
        raise ValueError(f"{label} has unsupported condition logic")
    if operator == "score" and not 0 < float(rule_set.get("required_score") or 0) <= 1:
        raise ValueError(f"{label} required score must be between zero and one")
    if not list(rule_set.get("conditions") or []):
        raise ValueError(f"{label} requires at least one condition")


def _validate_rule_expression(
    expression: dict[str, Any],
    label: str,
    rule_set_ids: set[str],
) -> None:
    kind = str(expression.get("kind") or "")
    if kind == "rule_set":
        rule_set_id = str(expression.get("rule_set_id") or "")
        if rule_set_id not in rule_set_ids:
            raise ValueError(f"{label} references unknown rule set {rule_set_id or '<empty>'}")
        return
    if kind != "operator" or str(expression.get("operator") or "") not in {"and", "or"}:
        raise ValueError(f"{label} has unsupported expression logic")
    children = list(expression.get("children") or [])
    if not children:
        raise ValueError(f"{label} requires at least one rule set")
    for child in children:
        _validate_rule_expression(dict(child), label, rule_set_ids)


def _validate_rule_stage(
    stage: dict[str, Any],
    label: str,
    rule_set_ids: set[str],
) -> None:
    _validate_rule_expression(dict(stage.get("expression") or {}), label, rule_set_ids)


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
    descriptions = {
        "passive": "Posts at the near-side quote without crossing the spread, prioritizing price quality over fill certainty.",
        "midpoint": "Posts at the current bid-ask midpoint, seeking spread improvement while accepting that the order may not fill.",
        "adaptive_patient": "Starts at the near-side quote and advances only to the midpoint after repeated attempts, favoring price quality over speed.",
        "adaptive_regular": "Moves progressively from the near-side quote toward executable liquidity, balancing price improvement with fill probability.",
        "adaptive_urgent": "Quotes at the executable touch immediately, prioritizing a timely fill while remaining inside the approved price envelope.",
        "adaptive_very_urgent": "Starts at the executable touch and may move through it by bounded ticks on reprices, maximizing fill urgency within hard limits.",
        "immediate_with_limit": "Seeks an immediate fill at executable liquidity but never crosses the configured buy ceiling or sell floor.",
        "ibkr_native_adaptive": "Uses urgent touch pricing without OMS repricing; the current runtime does not delegate this policy to a broker-native adaptive algorithm.",
        "cancel_if_not_filled": "Moves from passive toward executable pricing while time remains, then cancels the unfilled remainder at the deadline.",
    }
    return [
        {
            "policy_id": name,
            "revision": 1,
            "name": name,
            "description": descriptions[name],
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
    rule_set_catalog = {
        str(rule_set.get("rule_set_id") or ""): dict(rule_set)
        for rule_set in profile.get("rule_set_catalog") or []
    }
    initial_entry = dict(lifecycle.get("initial_entry") or {})
    parameters["entry_rules"] = {
        "trigger": _materialize_rule_stage(dict(initial_entry.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(initial_entry.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(initial_entry.get("blockers") or {}), rule_set_catalog),
    }
    behavior = deepcopy(dict(lifecycle.get("trading_behavior") or {}))
    phase_modes = deepcopy(dict(lifecycle.get("phase_modes") or {}))
    reentry = deepcopy(dict(lifecycle.get("reentry") or {}))
    reentry_rules = dict(reentry.pop("rules", {}) or {})
    reentry["enabled"] = str(phase_modes.get("reentry") or "automatic") == "automatic"
    parameters["reentry"] = reentry
    exit_rule_sets = list(
        dict(lifecycle.get("exit") or {}).get("rule_sets") or []
    )
    parameters["strategy_behavior"] = behavior
    parameters["phase_policy"] = {
        "initial_entry": {
            "mode": str(phase_modes.get("initial_entry") or "automatic"),
            "capital_request": deepcopy(dict(initial_entry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(initial_entry.get("order_intent") or {})),
            "add_steps": deepcopy(list(initial_entry.get("add_steps") or [])),
        },
        "manage": {
            "mode": str(phase_modes.get("manage") or "automatic"),
        },
        "reentry": {
            "mode": str(phase_modes.get("reentry") or "automatic"),
            "rules": {
                "trigger": _materialize_rule_stage(dict(reentry_rules.get("opportunity") or {}), rule_set_catalog),
                "confirmation": _materialize_rule_stage(dict(reentry_rules.get("confirmation") or {}), rule_set_catalog),
                "veto": _materialize_rule_stage(dict(reentry_rules.get("blockers") or {}), rule_set_catalog),
            },
            "capital_request": deepcopy(dict(reentry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(reentry.get("order_intent") or {})),
        },
        "exit": {"mode": str(phase_modes.get("exit") or "automatic"), "rule_sets": [
            {
                **deepcopy(route),
                "rules": _materialize_rule_stage(dict(route.get("rules") or {}), rule_set_catalog),
            }
            for route in exit_rule_sets
        ]},
    }
    parameters["phase_policy"]["initial_entry"]["add_steps"] = [
        {
            **deepcopy(step),
            "rules": _materialize_rule_stage(dict(step.get("rules") or {}), rule_set_catalog),
        }
        for step in initial_entry.get("add_steps") or []
    ]
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
