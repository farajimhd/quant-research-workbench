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
    profiles_for_runtime,
)


def configured_portfolio_profiles(
    accounts: Iterable[Any],
    *,
    raw_config: str | None = None,
) -> tuple[tuple[PortfolioAccountProfile, ...], tuple[PortfolioGroupPolicy, ...]]:
    """Bind stable configured account keys to externally supplied broker ids.

    ``accounts`` may contain dataclasses or dictionaries exposing account_key,
    account_id, account_class, and trading_mode. Broker ids are never expected
    inside the durable portfolio policy JSON.
    """

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


def configured_portfolio_policy_catalog(
    *,
    raw_config: str | None = None,
) -> dict[str, PortfolioPolicy]:
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
        return profiles_for_runtime(requested, mode=mode), ()
    available_ids = {str(row["account_id"]) for row in selected}
    missing = set(requested) - available_ids
    if missing:
        raise ValueError(
            "Configured runtime account ids are not bound in IBKR_ACCOUNTS_JSON "
            f"or IBKR_ACCOUNT_*_ID: {', '.join(sorted(missing))}"
        )
    return configured_portfolio_profiles(selected)


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
        for tuple_field in ("allowed_security_types", "allowed_currencies", "restricted_symbols"):
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
