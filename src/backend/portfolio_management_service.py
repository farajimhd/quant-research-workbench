from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.backend.canonical_trading_service import portfolio_exposure, portfolio_metrics
from src.backend.real_live_trading_service import configured_real_live_accounts
from src.backend.trading_runtime_service import trading_journal
from src.backend.trading_configuration_service import approved_configuration
from src.trading_runtime.portfolio import (
    PortfolioControlMode,
    narrow_policy_for_account_class,
    portfolio_policy_from_payload,
)
from src.trading_runtime.portfolio_config import (
    configured_portfolio_policy_catalog,
    configured_portfolio_profiles,
    portfolio_configuration_payload,
)


def portfolio_management_snapshot(canonical_state: dict[str, Any]) -> dict[str, Any]:
    approved = approved_configuration()
    configuration = dict(approved.get("payload") or {}) if approved else None
    profiles, groups = configured_portfolio_profiles(
        configured_real_live_accounts(), configuration=configuration
    )
    policy_catalog = configured_portfolio_policy_catalog(configuration=configuration)
    selected_ids = {str(row.get("account_id") or "") for row in canonical_state.get("accounts") or []}
    profiles = tuple(profile for profile in profiles if profile.account_id in selected_ids)
    journal = trading_journal()
    persisted = journal.portfolio_states()
    managed_states = journal.order_management_states()
    portfolio_records = journal.portfolio_management_records(limit=5_000)
    order_records = journal.order_management_records(limit=5_000)
    risk_records = [
        row
        for row in order_records
        if row.entity_type == "continuous_risk_state"
    ]
    account_rows: list[dict[str, Any]] = []
    for profile in profiles:
        account_values = [
            row for row in canonical_state.get("account_values") or []
            if str(row.get("account_id") or "") == profile.account_id
        ]
        ledger = [
            row for row in canonical_state.get("ledger") or []
            if str(row.get("account_id") or "") == profile.account_id
        ]
        positions = [
            row for row in canonical_state.get("positions") or []
            if str(row.get("account_id") or "") == profile.account_id
        ]
        orders = [
            row for row in canonical_state.get("orders") or []
            if str(row.get("account_id") or "") == profile.account_id
        ]
        state = persisted.get(profile.account_id) or {}
        account_managed_states = [
            row for row in managed_states if row["account_id"] == profile.account_id
        ]
        latest_risk = next(
            (
                row.payload
                for row in reversed(risk_records)
                if row.account_id == profile.account_id
            ),
            {},
        )
        metrics = portfolio_metrics(account_values, ledger, positions)
        risk_metrics = latest_risk.get("metrics") if isinstance(latest_risk, dict) else {}
        if isinstance(risk_metrics, dict):
            metrics["daily_loss"] = float(risk_metrics.get("daily_loss") or 0)
            metrics["drawdown"] = float(risk_metrics.get("drawdown") or 0)
        exposure = portfolio_exposure(positions)
        selected_policy = state.get("selected_policy") or {}
        selected_identity = str(selected_policy.get("identity") or "")
        policy = (
            portfolio_policy_from_payload(selected_policy)
            if selected_policy
            else policy_catalog.get(selected_identity, profile.policy)
        )
        policy = narrow_policy_for_account_class(policy, profile.account_class)
        available_policies = {
            candidate.identity: narrow_policy_for_account_class(candidate, profile.account_class)
            for candidate in (profile.policy, *policy_catalog.values())
        }
        gross = float(exposure.get("gross_value") or 0)
        net = float(exposure.get("net_value") or 0)
        net_liquidation = float(metrics.get("net_liquidation") or 0)
        eligible_equity = net_liquidation * policy.eligible_equity_fraction
        reservations = list(state.get("reservations") or [])
        active_reservations = [
            row for row in reservations
            if str(row.get("status") or "") not in {"released", "filled", "cancelled", "rejected", "policy_blocked"}
        ]
        reserved_notional = sum(float(row.get("reserved_notional") or 0) for row in active_reservations)
        reserved_risk = sum(float(row.get("reserved_planned_risk") or 0) for row in active_reservations)
        observed_at = _latest_timestamp(account_values + ledger + positions + orders)
        sync_state = (
            "entries_blocked"
            if canonical_state.get("stale") or not canonical_state.get("complete")
            else "synchronized"
        )
        control = str(state.get("control_mode") or PortfolioControlMode.ENABLED)
        account_rows.append(
            {
                "account_key": profile.account_key,
                "account_id": profile.account_id,
                "account_class": profile.account_class,
                "mode": profile.mode,
                "session_key": profile.session_key,
                "base_currency": profile.base_currency,
                "enabled": profile.enabled,
                "sync_state": sync_state,
                "control_mode": control,
                "observed_at": observed_at,
                "stale_reason": canonical_state.get("stale_reason") or "",
                "policy": {**asdict(policy), "identity": policy.identity},
                "available_policies": [
                    {**asdict(candidate), "identity": candidate.identity}
                    for candidate in available_policies.values()
                ],
                "run_plan_allocations": dict(profile.strategy_allocations),
                "strategy_allocations": _strategy_allocation_projection(
                    profile.strategy_allocations,
                    configuration,
                ),
                "disabled_strategy_allocations": sorted(
                    str(item) for item in state.get("disabled_strategy_allocations") or ()
                ),
                "metrics": {
                    **metrics,
                    **exposure,
                    "eligible_equity": eligible_equity,
                    "reserved_notional": reserved_notional,
                    "reserved_planned_risk": reserved_risk,
                    "gross_headroom": max(0.0, policy.maximum_gross_exposure - gross - reserved_notional),
                    "net_long_headroom": max(0.0, policy.maximum_net_long_exposure - max(0.0, net) - reserved_notional),
                    "net_short_headroom": max(0.0, policy.maximum_net_short_exposure - max(0.0, -net) - reserved_notional),
                    "planned_risk_headroom": max(
                        0.0,
                        eligible_equity * policy.maximum_open_risk_fraction - reserved_risk,
                    ),
                },
                "position_count": len([row for row in positions if float(row.get("quantity") or 0) != 0]),
                "working_order_count": len([row for row in orders if not row.get("terminal")]),
                "reservations": active_reservations,
                "allocations": list(state.get("allocations") or []),
                "reconciliation": list(state.get("reconciliation") or []),
                "continuous_risk": latest_risk,
                "managed_order_groups": account_managed_states[-100:],
                "pending_operational_commands": list(
                    state.get("pending_operational_commands") or ()
                ),
            }
        )
    decisions = [
        {
            "event_time": row.event_time,
            "account_id": row.account_id,
            "entity_type": row.entity_type,
            **row.payload,
        }
        for row in portfolio_records[-250:]
    ]
    return {
        "schema_version": 1,
        "as_of": canonical_state.get("as_of"),
        "complete": bool(canonical_state.get("complete")),
        "stale": bool(canonical_state.get("stale")),
        "stale_reason": str(canonical_state.get("stale_reason") or ""),
        "accounts": account_rows,
        "groups": _group_rows(groups, account_rows),
        "recent_decisions": decisions,
        "operational_metrics": _operational_metrics(
            account_rows,
            managed_states,
            portfolio_records,
            order_records,
        ),
        "configuration": portfolio_configuration_payload(profiles, groups),
        "configuration_authority": {
            "source": "approved_release" if approved else "legacy_environment",
            "revision_id": str(approved.get("revision_id") or "") if approved else "",
            "revision": int(approved.get("revision") or 0) if approved else 0,
        },
    }


def _operational_metrics(
    account_rows: list[dict[str, Any]],
    managed_states: list[dict[str, Any]],
    portfolio_records: list[Any],
    order_records: list[Any],
) -> dict[str, Any]:
    disposition_counts: dict[str, int] = {}
    reservation_event_counts: dict[str, int] = {}
    for record in portfolio_records:
        if record.entity_type == "portfolio_decision":
            status = str(record.payload.get("status") or "unknown")
            disposition_counts[status] = disposition_counts.get(status, 0) + 1
        if record.entity_type == "portfolio_reservation":
            event = str(record.payload.get("event") or "unknown")
            reservation_event_counts[event] = reservation_event_counts.get(event, 0) + 1

    state_counts: dict[str, int] = {}
    unprotected_quantity = 0.0
    for row in managed_states:
        state = dict(row.get("state") or {})
        status = str(state.get("state") or "unknown")
        state_counts[status] = state_counts.get(status, 0) + 1
        unprotected_quantity += max(
            0.0,
            float(state.get("protection_required_quantity") or 0)
            - float(state.get("protection_coverage_quantity") or 0),
        )
    reconciliation_records = [
        record
        for record in order_records
        if "reconciliation" in str(record.payload.get("event") or record.entity_type).lower()
    ]
    reconciliation_failures = [
        record
        for record in reconciliation_records
        if any(
            token in str(record.payload.get("event") or "").lower()
            for token in ("missing", "failed", "unknown", "mismatch")
        )
    ]
    active_reservations = [
        reservation
        for account in account_rows
        for reservation in account.get("reservations") or []
    ]
    reconciliation_issues = [
        issue
        for account in account_rows
        for issue in account.get("reconciliation") or []
    ]
    return {
        "schema_version": 1,
        "journal_window_limit": 5_000,
        "portfolio": {
            "decision_count": sum(disposition_counts.values()),
            "disposition_counts": disposition_counts,
            "reservation_event_counts": reservation_event_counts,
            "active_reservation_count": len(active_reservations),
            "active_reserved_notional": sum(
                float(row.get("reserved_notional") or 0) for row in active_reservations
            ),
            "active_reserved_planned_risk": sum(
                float(row.get("reserved_planned_risk") or 0) for row in active_reservations
            ),
            "reconciliation_issue_count": len(reconciliation_issues),
            "pending_command_count": sum(
                len(account.get("pending_operational_commands") or []) for account in account_rows
            ),
            "journal_window_truncated": len(portfolio_records) >= 5_000,
        },
        "oms": {
            "managed_group_count": len(managed_states),
            "state_counts": state_counts,
            "outcome_unknown_count": state_counts.get("outcome_unknown", 0),
            "unprotected_quantity": unprotected_quantity,
            "reconciliation_event_count": len(reconciliation_records),
            "reconciliation_failure_count": len(reconciliation_failures),
            "last_reconciliation_at": (
                reconciliation_records[-1].event_time.isoformat()
                if reconciliation_records
                else None
            ),
            "journal_window_truncated": len(order_records) >= 5_000,
        },
    }


def _strategy_allocation_projection(
    run_plan_allocations: dict[str, float] | Any,
    configuration: dict[str, Any] | None,
) -> dict[str, float]:
    """Present Strategy totals without erasing distinct Run Plan authority."""
    allocations = {
        str(key): float(value)
        for key, value in dict(run_plan_allocations or {}).items()
    }
    if not configuration:
        return allocations
    plan_section = dict(
        configuration.get("run_plans") or configuration.get("assignments") or {}
    )
    plans = {
        str(row.get("run_plan_id") or row.get("deployment_id") or ""): row
        for row in (plan_section.get("plans") or plan_section.get("deployments") or [])
    }
    profiles = {
        str(row.get("profile_id") or ""): row
        for row in dict(configuration.get("strategy") or {}).get("profiles") or []
    }
    projected: dict[str, float] = {}
    for allocation_id, fraction in allocations.items():
        plan = dict(plans.get(allocation_id) or {})
        profile_id = str(
            plan.get("strategy_profile_id") or plan.get("profile_id") or ""
        )
        profile = dict(profiles.get(profile_id) or {})
        strategy_id = str(
            profile.get("definition_id")
            or profile.get("strategy_id")
            or plan.get("strategy_id")
            or allocation_id
        )
        projected[strategy_id] = projected.get(strategy_id, 0.0) + fraction
    return projected


def portfolio_management_command(
    account_key: str,
    command: str,
    *,
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = approved_configuration()
    configuration = dict(approved.get("payload") or {}) if approved else None
    profiles, _ = configured_portfolio_profiles(
        configured_real_live_accounts(), configuration=configuration
    )
    profile = next((row for row in profiles if row.account_key == account_key), None)
    if profile is None:
        raise KeyError(account_key)
    normalized = command.strip().lower()
    detail = dict(detail or {})
    controls = {
        "pause_entries": PortfolioControlMode.ENTRIES_PAUSED,
        "reduce_only": PortfolioControlMode.REDUCE_ONLY,
        "disable": PortfolioControlMode.DISABLED,
        "kill_entries": PortfolioControlMode.REDUCE_ONLY,
        "emergency_flatten": PortfolioControlMode.REDUCE_ONLY,
    }
    if normalized == "reconcile":
        return {
            "account_key": account_key,
            "account_id": profile.account_id,
            "command": normalized,
            "refresh_required": True,
        }
    journal = trading_journal()
    state = journal.portfolio_states().get(profile.account_id) or {"account_key": account_key}
    event_payload: dict[str, Any]
    response: dict[str, Any]
    if normalized == "select_policy":
        identity = str(detail.get("policy_identity") or "").strip()
        catalog = configured_portfolio_policy_catalog(configuration=configuration)
        catalog[profile.policy.identity] = profile.policy
        policy = catalog.get(identity)
        if policy is None:
            raise ValueError(f"Unknown configured portfolio policy: {identity}")
        policy = narrow_policy_for_account_class(policy, profile.account_class)
        state["selected_policy"] = {**asdict(policy), "identity": policy.identity}
        state["control_mode"] = PortfolioControlMode.ENTRIES_PAUSED.value
        event_payload = {
            "event": "portfolio_policy_selected",
            "account_key": account_key,
            "policy": state["selected_policy"],
            "entries_paused": True,
            "reason": reason,
        }
        response = {
            "account_key": account_key,
            "account_id": profile.account_id,
            "command": normalized,
            "control_mode": PortfolioControlMode.ENTRIES_PAUSED.value,
            "policy": state["selected_policy"],
        }
    elif normalized in {"disable_strategy", "enable_strategy"}:
        strategy_id = str(detail.get("strategy_id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id is required")
        disabled = {
            str(item) for item in state.get("disabled_strategy_allocations") or ()
        }
        enabled = normalized == "enable_strategy"
        if enabled:
            disabled.discard(strategy_id)
        else:
            disabled.add(strategy_id)
        state["disabled_strategy_allocations"] = sorted(disabled)
        event_payload = {
            "event": "strategy_allocation_control_changed",
            "account_key": account_key,
            "strategy_id": strategy_id,
            "enabled": enabled,
            "reason": reason,
        }
        response = {
            "account_key": account_key,
            "account_id": profile.account_id,
            "command": normalized,
            "strategy_id": strategy_id,
            "enabled": enabled,
            "disabled_strategy_allocations": state["disabled_strategy_allocations"],
        }
    elif normalized in {"resume_entries", "kill_entries", "emergency_flatten"}:
        if normalized != "resume_entries":
            state["control_mode"] = PortfolioControlMode.REDUCE_ONLY.value
        pending = list(state.get("pending_operational_commands") or ())
        command_id = f"{normalized}:{datetime.now(timezone.utc).isoformat()}"
        pending.append(
            {
                "command_id": command_id,
                "command": normalized,
                "reason": reason,
                "detail": detail,
                "status": "pending",
            }
        )
        state["pending_operational_commands"] = pending[-100:]
        event_payload = {
            "event": "portfolio_operational_command_requested",
            "account_key": account_key,
            "command_id": command_id,
            "command": normalized,
            "reason": reason,
        }
        response = {
            "account_key": account_key,
            "account_id": profile.account_id,
            "command": normalized,
            "command_id": command_id,
            "control_mode": str(
                state.get("control_mode") or PortfolioControlMode.ENTRIES_PAUSED.value
            ),
            "execution_required": True,
        }
    elif normalized not in controls:
        raise ValueError(f"Unsupported portfolio command: {command}")
    else:
        if not profile.enabled and controls[normalized] != PortfolioControlMode.DISABLED:
            raise ValueError("A disabled account profile cannot be enabled operationally")
        state["control_mode"] = controls[normalized].value
        event_payload = {
            "event": "portfolio_control_changed",
            "account_key": account_key,
            "control_mode": controls[normalized].value,
            "reason": reason,
        }
        response = {
            "account_key": account_key,
            "account_id": profile.account_id,
            "command": normalized,
            "control_mode": controls[normalized].value,
        }
    state["control_reason"] = reason
    state["control_updated_at"] = datetime.now(timezone.utc)
    journal.save_portfolio_state(profile.account_id, state)
    journal.append(
        run_id="portfolio-management",
        category="portfolio_management",
        entity_type="portfolio_control",
        entity_id=account_key,
        account_id=profile.account_id,
        payload=event_payload,
    )
    return response


def _latest_timestamp(rows: list[dict[str, Any]]) -> str:
    candidates = [
        str(row.get("source_event_time") or row.get("received_at") or "")
        for row in rows
        if row.get("source_event_time") or row.get("received_at")
    ]
    return max(candidates) if candidates else ""


def _group_rows(groups: tuple[Any, ...], accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {row["account_key"]: row for row in accounts}
    result: list[dict[str, Any]] = []
    for group in groups:
        selected = [by_key[key] for key in group.account_keys if key in by_key]
        gross = sum(float(row["metrics"].get("gross_value") or 0) for row in selected)
        result.append(
            {
                **asdict(group),
                "gross_exposure": gross,
                "gross_headroom": max(0.0, group.maximum_gross_exposure - gross),
                "sync_state": (
                    "synchronized"
                    if selected and all(row["sync_state"] == "synchronized" for row in selected)
                    else "entries_blocked"
                ),
            }
        )
    return result
