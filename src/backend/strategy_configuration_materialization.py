"""Bind verified selection references to the existing app configuration shape.

Nested dictionaries below are transient app objects, never persisted JSON.
No defaults, sizing overrides, publication or strategy registration occur here.
"""
from dataclasses import asdict
from hashlib import sha256

from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.strategy_configuration_selection import (
    StrategyConfigurationSelection, restore_configuration_selection,
)
from src.trading_runtime.columnar_strategy_release import ProgramRelease
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.journal_contract import canonical_json


def materialize_program_configuration(selection, release, policies):
    """Pure reference binding after the owning readers certify each authority."""
    if type(selection) is not StrategyConfigurationSelection or type(release) is not ProgramRelease:
        raise ValueError("Configuration binding requires verified selection and programme types")
    selection = restore_configuration_selection(selection.rows)
    header = selection.header
    if (release.header["release_id"] != header["program_release_id"]
            or release.header["content_hash"] != header["program_release_hash"]
            or release.header["strategy_id"] != header["strategy_id"]
            or release.header["strategy_number"] != header["strategy_revision"]):
        raise ValueError("Configuration programme reference differs from its sealed release")
    accounts = selection.rows[schema.ACCOUNT.name]
    hashes = {row["policy_hash"] for row in accounts}
    if set(policies) != hashes:
        raise ValueError("Configuration policy references are missing or unexpected")
    for digest, policy in policies.items():
        if type(policy) is not PortfolioPolicy or _policy_rows(policy)[0] != digest:
            raise ValueError("Configuration policy differs from its immutable catalogue hash")
    if len({policy.policy_id for policy in policies.values()}) != len(policies):
        raise ValueError("Configuration contains ambiguous policy identifiers")
    bindings = []
    for row in accounts:
        binding = {key: value for key, value in row.items()
                   if key not in {"configuration_id", "ordinal", "policy_hash"}}
        binding["enabled"] = bool(binding["enabled"])
        binding["portfolio_policy_id"] = policies[row["policy_hash"]].policy_id
        binding["modes"] = [mode["mode"] for mode in selection.rows[schema.MODE.name]
                            if mode["account_key"] == row["account_key"]]
        bindings.append(binding)
    mandates = []
    for row in selection.rows[schema.MANDATE.name]:
        mandate = {key: value for key, value in row.items() if key not in {"configuration_id", "ordinal"}}
        for flag in ("enabled", "allow_replacement"):
            mandate[flag] = bool(mandate[flag])
        mandates.append(mandate)
    groups = []
    for row in selection.rows[schema.GROUP.name]:
        group = {key: value for key, value in row.items() if key not in {"configuration_id", "ordinal"}}
        group["account_keys"] = [member["account_key"] for member in selection.rows[schema.GROUP_MEMBER.name]
                                 if member["group_id"] == row["group_id"]]
        groups.append(group)
    payload = {
        "strategy": {"strategy_id": header["strategy_id"], "revision": header["strategy_revision"],
                     "program_release_id": header["program_release_id"],
                     "program_release_hash": header["program_release_hash"], "parameters": {}},
        "run_plan": {"run_plan_id": header["run_plan_id"], "safety_supervisor": {
            "enabled_by_environment": {row["mode"]: bool(row["enabled"])
                for row in selection.rows[schema.SUPERVISOR.name]}}},
        "accounts": {"bindings": bindings},
        "portfolio": {"policies": [asdict(policies[digest]) for digest in sorted(hashes)],
                      "mandates": mandates, "groups": groups},
        "oms": {"limit_offset_bps": header["oms_limit_offset_bps"]},
    }
    return {
        "revision_id": "strategy-configuration:" + header["configuration_id"],
        "revision": header["strategy_revision"], "label": header["label"],
        "run_plan_id": header["run_plan_id"], "selection_hash": header["content_hash"],
        "content_hash": sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
        "payload": payload,
    }
