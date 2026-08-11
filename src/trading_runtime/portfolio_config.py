from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, fields
from typing import Any, Iterable, Mapping

from src.trading_runtime.portfolio import (
    PortfolioAccountProfile,
    PortfolioGroupPolicy,
    PortfolioPolicy,
    default_policy_for_account,
    narrow_policy_for_account_class,
    portfolio_policy_from_payload,
    profiles_for_runtime,
)


def configured_portfolio_profiles(
    accounts: Iterable[Any],
    *,
    raw_config: str | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[tuple[PortfolioAccountProfile, ...], tuple[PortfolioGroupPolicy, ...]]:
    """Bind stable configured account keys to externally supplied broker ids.

    ``accounts`` may contain dataclasses or dictionaries exposing account_key,
    account_id, account_class, and trading_mode. Broker ids are never expected
    inside the durable portfolio policy JSON.
    """

    if configuration is not None:
        return portfolio_profiles_from_configuration(accounts, configuration)
    config_text = raw_config if raw_config is not None else os.environ.get("PORTFOLIO_MANAGEMENT_JSON", "")
    config = json.loads(config_text) if config_text.strip() else {}
    if not isinstance(config, dict):
        raise ValueError("PORTFOLIO_MANAGEMENT_JSON must be an object")
    policies = _policies(config.get("policies") or {})
    account_config = config.get("accounts") or {}
    if not isinstance(account_config, dict):
        raise ValueError("Portfolio accounts configuration must be an object keyed by account_key")
    profiles: list[PortfolioAccountProfile] = []
    for source in accounts:
        raw = source if isinstance(source, Mapping) else asdict(source)
        account_key = str(raw.get("account_key") or raw.get("key") or "").strip()
        account_id = str(raw.get("account_id") or raw.get("id") or "").strip()
        if not account_key or not account_id:
            continue
        account_class = str(raw.get("account_class") or raw.get("type") or account_key).strip().lower()
        mode = str(raw.get("trading_mode") or raw.get("mode") or "live").strip().lower()
        override = account_config.get(account_key) or {}
        if not isinstance(override, dict):
            raise ValueError(f"Portfolio account {account_key} must be an object")
        policy_reference = str(override.get("policy") or "").strip()
        base_policy = policies.get(policy_reference) if policy_reference else default_policy_for_account(account_class)
        if base_policy is None:
            raise ValueError(f"Unknown portfolio policy for {account_key}: {policy_reference}")
        policy = narrow_policy_for_account_class(base_policy, account_class)
        allocations = override.get("strategy_allocations") or {}
        if not isinstance(allocations, dict):
            raise ValueError(f"strategy_allocations for {account_key} must be an object")
        profiles.append(
            PortfolioAccountProfile(
                account_key=account_key,
                account_id=account_id,
                mode="paper" if mode == "paper" else "live",
                account_class=account_class,
                policy=policy,
                session_key=str(override.get("session_key") or f"ibkr-{mode}"),
                enabled=bool(override.get("enabled", True)),
                base_currency=str(override.get("base_currency") or raw.get("base_currency") or "USD").upper(),
                strategy_allocations={str(key): float(value) for key, value in allocations.items()},
            )
        )
    groups = _groups(config.get("groups") or {}, {profile.account_key for profile in profiles})
    return tuple(profiles), groups


def portfolio_profiles_from_configuration(
    accounts: Iterable[Any],
    configuration: Mapping[str, Any],
) -> tuple[tuple[PortfolioAccountProfile, ...], tuple[PortfolioGroupPolicy, ...]]:
    """Bind an approved application release to externally discovered IBKR ids."""

    portfolio = dict(configuration.get("portfolio") or {})
    account_section = dict(configuration.get("accounts") or {})
    policies = {
        str(row.get("policy_id") or ""): portfolio_policy_from_payload(row)
        for row in portfolio.get("policies") or []
    }
    policies.update({policy.identity: policy for policy in policies.values()})
    discovered: dict[str, Mapping[str, Any]] = {}
    for source in accounts:
        raw = source if isinstance(source, Mapping) else asdict(source)
        key = str(raw.get("account_key") or raw.get("key") or "").strip()
        if key:
            discovered[key] = raw
    run_plan_section = dict(
        configuration.get("run_plans") or configuration.get("assignments") or {}
    )
    run_plans = {
        str(row.get("run_plan_id") or row.get("deployment_id") or ""): row
        for row in (run_plan_section.get("plans") or run_plan_section.get("deployments") or [])
    }
    mandates_by_account: dict[str, list[dict[str, Any]]] = {}
    for mandate in portfolio.get("mandates") or []:
        if bool(mandate.get("enabled", True)):
            mandates_by_account.setdefault(str(mandate.get("account_key") or ""), []).append(dict(mandate))
    profiles: list[PortfolioAccountProfile] = []
    configured_keys: set[str] = set()
    for binding in account_section.get("bindings") or []:
        modes = {str(value) for value in binding.get("modes") or []}
        if not modes.intersection({"paper", "live"}) or not bool(binding.get("enabled", True)):
            continue
        account_key = str(binding.get("account_key") or "").strip()
        source = discovered.get(account_key)
        if source is None:
            continue
        configured_keys.add(account_key)
        account_id = str(source.get("account_id") or source.get("id") or "").strip()
        expected_id = str(binding.get("source_account_id") or "").strip()
        if expected_id and expected_id != account_id:
            raise ValueError(
                f"Approved account {account_key} expects broker account {expected_id} but discovery returned {account_id}"
            )
        discovered_mode = str(source.get("trading_mode") or source.get("mode") or "live").lower()
        mode = "paper" if discovered_mode == "paper" else "live"
        if mode not in modes:
            continue
        account_class = str(source.get("account_class") or binding.get("account_class") or account_key).lower()
        configured_class = str(binding.get("account_class") or account_class).lower()
        if configured_class not in {account_class, "paper"}:
            raise ValueError(
                f"Approved account {account_key} class {configured_class} does not match discovered class {account_class}"
            )
        policy_reference = str(binding.get("portfolio_policy_id") or "")
        policy = policies.get(policy_reference)
        if policy is None:
            raise ValueError(f"Approved account {account_key} references unknown policy {policy_reference}")
        allocations: dict[str, float] = {}
        strategy_mandates: dict[str, dict[str, Any]] = {}
        for mandate in mandates_by_account.get(account_key, []):
            run_plan_id = str(
                mandate.get("run_plan_id") or mandate.get("deployment_id") or ""
            )
            run_plan = run_plans.get(run_plan_id) or {}
            if (
                not bool(run_plan.get("enabled", True))
                or mode
                not in {
                    str(value)
                    for value in (
                        run_plan.get("allowed_environments")
                        or run_plan.get("modes")
                        or []
                    )
                }
            ):
                continue
            if run_plan_id:
                allocations[run_plan_id] = float(mandate.get("maximum_cash_fraction") or 0)
                strategy_mandates[run_plan_id] = mandate
        profiles.append(PortfolioAccountProfile(
            account_key=account_key,
            account_id=account_id,
            mode=mode,
            account_class=account_class,
            policy=narrow_policy_for_account_class(policy, account_class),
            session_key=str(binding.get("session_key") or f"ibkr-{mode}"),
            enabled=True,
            base_currency=str(binding.get("base_currency") or "USD").upper(),
            strategy_allocations=allocations,
            strategy_mandates=strategy_mandates,
        ))
    groups = tuple(
        PortfolioGroupPolicy(
            group_id=str(row.get("group_id") or ""),
            account_keys=tuple(str(value) for value in row.get("account_keys") or ()),
            maximum_gross_exposure=float(row.get("maximum_gross_exposure") or 0),
            maximum_ticker_exposure=float(row.get("maximum_ticker_exposure") or 0),
        )
        for row in portfolio.get("groups") or []
        if set(str(value) for value in row.get("account_keys") or ()) <= configured_keys
    )
    return tuple(profiles), groups


def configured_portfolio_policy_catalog(
    *,
    raw_config: str | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, PortfolioPolicy]:
    if configuration is not None:
        rows = list(dict(configuration.get("portfolio") or {}).get("policies") or [])
        policies = [portfolio_policy_from_payload(dict(row)) for row in rows]
        return {policy.identity: policy for policy in policies}
    config_text = raw_config if raw_config is not None else os.environ.get("PORTFOLIO_MANAGEMENT_JSON", "")
    config = json.loads(config_text) if config_text.strip() else {}
    if not isinstance(config, dict):
        raise ValueError("PORTFOLIO_MANAGEMENT_JSON must be an object")
    catalog = _policies(config.get("policies") or {})
    return {policy.identity: policy for policy in catalog.values()}


def configured_portfolio_profiles_for_runtime(
    account_ids: Iterable[str],
    *,
    mode: str,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[tuple[PortfolioAccountProfile, ...], tuple[PortfolioGroupPolicy, ...]]:
    """Resolve the runtime's live account bindings from the same durable config.

    When explicit IBKR bindings exist, all accounts in the selected live/paper
    session are included so aggregate group limits observe the whole configured
    group, even if only a subset receives strategy intents in this run.
    """

    requested = tuple(str(account_id) for account_id in account_ids)
    if mode not in {"live", "paper"}:
        return profiles_for_runtime(requested, mode=mode), ()
    descriptors = _configured_ibkr_account_descriptors()
    selected = [
        row for row in descriptors
        if str(row["trading_mode"]) == mode and str(row["account_id"])
    ]
    if not selected:
        if configuration is not None:
            raise ValueError(
                f"Approved {mode} configuration requires externally discovered IBKR account bindings"
            )
        return profiles_for_runtime(requested, mode=mode), ()
    available_ids = {str(row["account_id"]) for row in selected}
    missing = set(requested) - available_ids
    if missing:
        raise ValueError(
            "Configured runtime account ids are not bound in IBKR_ACCOUNTS_JSON "
            f"or IBKR_ACCOUNT_*_ID: {', '.join(sorted(missing))}"
        )
    return configured_portfolio_profiles(selected, configuration=configuration)


def portfolio_configuration_payload(
    profiles: Iterable[PortfolioAccountProfile],
    groups: Iterable[PortfolioGroupPolicy],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "accounts": [
            {
                "account_key": profile.account_key,
                "account_class": profile.account_class,
                "mode": profile.mode,
                "session_key": profile.session_key,
                "enabled": profile.enabled,
                "base_currency": profile.base_currency,
                "policy": {**asdict(profile.policy), "identity": profile.policy.identity},
                "strategy_allocations": dict(profile.strategy_allocations),
            }
            for profile in profiles
        ],
        "groups": [asdict(group) for group in groups],
    }


def _policies(raw: Any) -> dict[str, PortfolioPolicy]:
    if not isinstance(raw, dict):
        raise ValueError("Portfolio policies configuration must be an object")
    valid = {item.name for item in fields(PortfolioPolicy)}
    result: dict[str, PortfolioPolicy] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            raise ValueError(f"Portfolio policy {key} must be an object")
        unknown = set(payload) - valid
        if unknown:
            raise ValueError(f"Unknown fields in portfolio policy {key}: {', '.join(sorted(unknown))}")
        normalized = dict(payload)
        normalized.setdefault("policy_id", str(key))
        for tuple_field in (
            "allowed_security_types",
            "allowed_currencies",
            "restricted_symbols",
            "allowed_execution_policies",
            "allowed_protection_profiles",
        ):
            if tuple_field in normalized:
                normalized[tuple_field] = tuple(str(item) for item in normalized[tuple_field])
        policy = PortfolioPolicy(**normalized)
        result[str(key)] = policy
        result[policy.identity] = policy
    return result


def _groups(raw: Any, account_keys: set[str]) -> tuple[PortfolioGroupPolicy, ...]:
    if not isinstance(raw, dict):
        raise ValueError("Portfolio groups configuration must be an object")
    groups: list[PortfolioGroupPolicy] = []
    for group_id, payload in raw.items():
        if not isinstance(payload, dict):
            raise ValueError(f"Portfolio group {group_id} must be an object")
        keys = tuple(str(item) for item in payload.get("accounts") or ())
        unknown = set(keys) - account_keys
        if unknown:
            raise ValueError(f"Portfolio group {group_id} references unknown accounts: {', '.join(sorted(unknown))}")
        groups.append(
            PortfolioGroupPolicy(
                group_id=str(group_id),
                account_keys=keys,
                maximum_gross_exposure=float(payload.get("maximum_gross_exposure") or 0),
                maximum_ticker_exposure=float(payload.get("maximum_ticker_exposure") or 0),
            )
        )
    return tuple(groups)


def _configured_ibkr_account_descriptors() -> list[dict[str, str]]:
    raw = os.environ.get("IBKR_ACCOUNTS_JSON", "").strip()
    descriptors: list[dict[str, str]] = []
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            items = parsed.items()
        elif isinstance(parsed, list):
            items = enumerate(parsed)
        else:
            raise ValueError("IBKR_ACCOUNTS_JSON must be a list or object")
        for fallback_key, value in items:
            if not isinstance(value, dict):
                continue
            key = str(value.get("key") or value.get("account_key") or fallback_key).strip()
            account_class = str(value.get("account_class") or value.get("type") or key).strip().lower()
            account_id = str(value.get("account_id") or value.get("id") or value.get("account") or "").strip()
            configured_mode = str(
                value.get("trading_mode") or value.get("mode") or
                ("paper" if account_class == "paper" else "live")
            ).strip().lower()
            descriptors.append(
                {
                    "account_key": key,
                    "account_class": account_class,
                    "account_id": account_id,
                    "trading_mode": "paper" if configured_mode == "paper" else "live",
                }
            )
        return descriptors
    for name, account_id in os.environ.items():
        match = re.fullmatch(r"IBKR_ACCOUNT_([A-Z0-9_]+)_ID", name)
        if not match or not account_id.strip():
            continue
        token = match.group(1)
        key = token.lower().replace("_", "-")
        account_class = os.environ.get(f"IBKR_ACCOUNT_{token}_CLASS", key).strip().lower()
        configured_mode = os.environ.get(
            f"IBKR_ACCOUNT_{token}_MODE",
            "paper" if "paper" in key else "live",
        ).strip().lower()
        descriptors.append(
            {
                "account_key": key,
                "account_class": account_class,
                "account_id": account_id.strip(),
                "trading_mode": "paper" if configured_mode == "paper" else "live",
            }
        )
    return descriptors
