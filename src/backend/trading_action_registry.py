from __future__ import annotations

from copy import deepcopy
from typing import Any


ACTION_DEFINITION_SCHEMA_VERSION = 1
ACTION_POLICY_SCHEMA_VERSION = 1


def trading_action_definitions() -> list[dict[str, Any]]:
    """System-owned broker-neutral actions shared by Strategy and Canvas."""

    return deepcopy([
        _action("position.enter_long", "Enter long", "Open a long position after Portfolio admission.", "intent", "enter_long", "enter"),
        _action("position.enter_short", "Enter short", "Open a short position after borrow and Portfolio admission.", "intent", "enter_short", "enter"),
        _action("position.add_long", "Add long", "Increase an existing long position within its approved mandate.", "intent", "add_long", "add"),
        _action("position.add_short", "Add short", "Increase an existing short position within its approved mandate.", "intent", "add_short", "add"),
        _action("position.reduce_long", "Reduce long", "Release part of an existing long position.", "intent", "reduce_long", "reduce"),
        _action("position.reduce_short", "Reduce short", "Release part of an existing short position.", "intent", "reduce_short", "reduce"),
        _action("position.exit_long", "Exit long", "Close the reconciled long position.", "intent", "exit", "exit"),
        _action("position.exit_short", "Cover short", "Close the reconciled short position.", "intent", "cover", "exit"),
        _action("campaign.request_entry", "Request entry", "Ask an armed campaign to evaluate entry at the next causal observation.", "campaign_command", "request_entry", "enter", sizing_modes=()),
        _action("campaign.force_entry", "Force and attach", "Request immediate entry while retaining Portfolio, risk, and OMS validation.", "campaign_command", "force_entry", "enter", sizing_modes=()),
        _action("campaign.pause", "Pause campaign", "Pause discretionary Strategy actions without disabling protection.", "campaign_command", "pause", "control", sizing_modes=()),
        _action("campaign.resume", "Resume campaign", "Resume an operator-paused campaign.", "campaign_command", "resume", "control", sizing_modes=()),
        _action("campaign.disable_after_exit", "Disable after exit", "Prevent re-entry after the current position becomes flat.", "campaign_command", "disable_after_exit", "control", sizing_modes=()),
    ])


def default_action_policies() -> list[dict[str, Any]]:
    """Reusable policy definitions replacing the former Strategy capabilities."""

    return deepcopy([
        {
            "policy_id": "profit-pocket",
            "revision": ACTION_POLICY_SCHEMA_VERSION,
            "name": "Profit pocket",
            "description": "Reduce a winning position when the installed Strategy's registered profit-pocket mechanism passes.",
            "action_id": "position.reduce_long",
            "category": "position_management",
            "authority": "automatic",
            "enabled": True,
            "atomic": True,
            "editable": False,
            "origin": "system",
            "trigger": {
                "type": "strategy_mechanism",
                "mechanism_id": "profit_pocket",
                "rule_set_ids": [],
                "summary": "The installed executor evaluates favorable move and momentum-slowdown evidence causally.",
            },
            "quantity": {"mode": "position_fraction", "value": 0.5, "minimum_remaining_quantity": 1.0},
            "maximum_uses": 0,
            "settings": {
                "trigger": "acceleration_slowdown",
                "minimum_gain_pct": 0.75,
                "volatility_multiple": 1.0,
                "acceleration_slowdown_threshold": 0.15,
            },
        },
        {
            "policy_id": "confirmed-pullback-add",
            "revision": ACTION_POLICY_SCHEMA_VERSION,
            "name": "Confirmed pullback add",
            "description": "Request additional exposure only when the registered bullish-continuation Rule Set passes.",
            "action_id": "position.add_long",
            "category": "position_management",
            "authority": "confirm",
            "enabled": True,
            "atomic": True,
            "editable": False,
            "origin": "system",
            "trigger": {
                "type": "rule_sets",
                "operator": "all",
                "rule_set_ids": ["add-confirmed-position-add-bullish-structure-add"],
                "summary": "Bullish structure continuation after a pullback.",
            },
            "quantity": {"mode": "initial_allocation_fraction", "value": 0.5},
            "maximum_uses": 2,
            "settings": {},
        },
    ])


def action_policy_rule_set_ids(policy: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in dict(policy.get("trigger") or {}).get("rule_set_ids") or []
        if str(value)
    }


def resolve_trading_action(
    *, action_id: str = "", runtime_action: str = ""
) -> dict[str, Any]:
    """Resolve and cross-check a semantic action at an API/runtime boundary."""

    definitions = trading_action_definitions()
    by_id = {str(row["action_id"]): row for row in definitions}
    by_runtime = {str(row["runtime_action"]): row for row in definitions}
    resolved = by_id.get(str(action_id)) if action_id else by_runtime.get(str(runtime_action))
    if resolved is None or str(resolved.get("kind") or "") != "intent":
        raise ValueError("Trade proposal references an unknown Trading Action")
    if runtime_action and str(resolved.get("runtime_action") or "") != str(runtime_action):
        raise ValueError("Trade proposal action does not match its Trading Action")
    return deepcopy(resolved)


def validate_trading_actions(section: dict[str, Any], rule_set_ids: set[str]) -> None:
    definitions = list(section.get("definitions") or [])
    policies = list(section.get("policies") or [])
    action_ids = _unique(definitions, "action_id", "Trading Action")
    actions_by_id = {str(row["action_id"]): row for row in definitions}
    _unique(policies, "policy_id", "Action Policy")
    for definition in definitions:
        if int(definition.get("revision") or 0) < 1:
            raise ValueError("Trading Action revisions must be positive")
        if str(definition.get("kind") or "") not in {"intent", "campaign_command"}:
            raise ValueError(f"Trading Action {definition.get('action_id')} has an unsupported kind")
    for policy in policies:
        policy_id = str(policy.get("policy_id") or "")
        if str(policy.get("action_id") or "") not in action_ids:
            raise ValueError(f"Action Policy {policy_id} references an unknown Trading Action")
        if str(policy.get("authority") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Action Policy {policy_id} has unsupported authority")
        trigger = dict(policy.get("trigger") or {})
        trigger_type = str(trigger.get("type") or "")
        if trigger_type not in {"rule_sets", "strategy_mechanism"}:
            raise ValueError(f"Action Policy {policy_id} has an unsupported trigger")
        missing = action_policy_rule_set_ids(policy) - rule_set_ids
        if missing:
            raise ValueError(
                f"Action Policy {policy_id} references unknown Rule Sets: {', '.join(sorted(missing))}"
            )
        if trigger_type == "rule_sets" and not action_policy_rule_set_ids(policy):
            raise ValueError(f"Action Policy {policy_id} requires at least one Rule Set")
        action = actions_by_id[str(policy.get("action_id") or "")]
        quantity = dict(policy.get("quantity") or {})
        quantity_mode = str(quantity.get("mode") or "")
        if quantity_mode not in set(action.get("sizing_modes") or []):
            raise ValueError(
                f"Action Policy {policy_id} uses a quantity mode unsupported by its Trading Action"
            )
        if float(quantity.get("value") or 0) <= 0:
            raise ValueError(f"Action Policy {policy_id} quantity must be positive")
        if int(policy.get("maximum_uses") or 0) < 0:
            raise ValueError(f"Action Policy {policy_id} maximum uses cannot be negative")


def _action(
    action_id: str,
    name: str,
    description: str,
    kind: str,
    runtime_action: str,
    category: str,
    *,
    sizing_modes: tuple[str, ...] = (
        "fixed_quantity",
        "mandate_fraction",
        "risk_fraction",
        "position_fraction",
        "initial_allocation_fraction",
    ),
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "revision": ACTION_DEFINITION_SCHEMA_VERSION,
        "name": name,
        "description": description,
        "kind": kind,
        "runtime_action": runtime_action,
        "category": category,
        "sizing_modes": list(sizing_modes),
        "atomic": True,
        "editable": False,
        "origin": "system",
    }


def _unique(rows: list[dict[str, Any]], key: str, label: str) -> set[str]:
    values = [str(row.get(key) or "") for row in rows]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"{label} ids must be present and unique")
    return set(values)
