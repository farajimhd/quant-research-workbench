"""Pure approval-manifest validation for the Backtest-only second number.

The normalized configuration tree owns publication. This module supplies
deterministic validation, not a second configuration store or registration.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import re
from typing import Any, Mapping

from .journal_contract import canonical_json
from .numbered_fixed_strategy import session_policy_payload, numbered_fixed_strategy
from .strategy_one_contract import REQUIRED_INPUTS
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_registry import NumberedStrategyRelease

BEHAVIOR = (
    "Strategy 2 inherits Strategy 1 completed-bar entry, reentry, two resistance adds, "
    "sizing and ratcheting stop/target precedence. Backtest only, completed 100ms. "
    "America/New_York extended sessions only: premarket acquisitions before 09:25, "
    "cancel pending acquisitions at 09:25, flatten attempts from 09:29; after-hours "
    "acquisitions before 19:50, cancel pending acquisitions at 19:50, flatten "
    "attempts from 19:55. Fills require later certified liquidity; residual at "
    "session end fails. No regular-session entry/add, synthetic fill or live admission."
)
RULES = ("strategy-one-completed-entry-add-protection-v1", "strategy-two-extended-session-policy-v1")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(2)
    values = dict(number=2, executor_strategy_id=installed.strategy_id,
                  executor_revision=2, evaluation_interval=installed.execution_interval,
                  input_contracts=REQUIRED_INPUTS, rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_two_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(2)
    if (strategy.get("strategy_number") != 2 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 2 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 2 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 2 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash"}
    if set(manifest) != required:
        raise ValueError("Strategy 2 manifest shape differs")
    release = release_contract()
    release.verify()
    if (manifest["contract"] != release.canonical_payload()
            or manifest["approved_digest"] != release.approved_digest
            or manifest["session_policy"] != session_policy()
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or not re.fullmatch(r"strategy-one-1:[0-9a-fA-F-]{36}", str(manifest["source_revision_id"]))
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 2 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 2 manifest approval/code seal differs")
    verify_installed_strategy_two_release(manifest)
    return manifest


def verify_installed_strategy_two_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(2)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 2 differs from its installed numbered registry seal")
    return release


def derive_strategy_two_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if source.strategy_number != 1:
        raise ValueError("Strategy 2 must derive from certified Strategy 1")
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 2 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=2, revision=2, name="Early Squeeze Strategy 2",
                               profile_id="strategy-one-2", profile_revision=2,
                               numbered_release=manifest)
    verify_strategy_two_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-2", revision=2, definition_revision=2,
                   name="Early Squeeze Strategy 2", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 2 Backtest", description="Sealed extended-session Strategy 2",
                               profile_id="strategy-one-2")
    # Number 2 is research Backtest-only even if its inherited account supports live.
    for binding in payload.get("accounts", {}).get("bindings", []):
        binding["modes"] = ["backtest"] if "backtest" in binding.get("modes", []) else []
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-two-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
