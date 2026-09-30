"""Pure approval-manifest validation for the Backtest-only tenth number.

The normalized configuration tree owns publication. This module supplies
deterministic validation, not a second configuration store or registration.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import re
from typing import Any, Mapping

from .journal_contract import canonical_json
from .numbered_fixed_strategy import entry_price_policy_payload, target_policy_payload, trailing_policy_payload, add_policy_payload, activation_policy_payload, followthrough_policy_payload, session_policy_payload, numbered_fixed_strategy
from .strategy_one_contract import REQUIRED_INPUTS
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_registry import NumberedStrategyRelease

ACTIVATION_POLICY = activation_policy_payload()
ADD_POLICY = add_policy_payload()
TRAILING_POLICY = trailing_policy_payload()
TARGET_POLICY = target_policy_payload()

ENTRY_PRICE_POLICY = entry_price_policy_payload()

PARENT_REVISION_ID = "strategy-one-9:a9249a01-2049-4745-a61c-8e245a9be127"
PARENT_PAYLOAD_HASH = "76965e35cd33a9bfdd1f7d6ad1ad5b41efd18084d03d946c4524a0e273715293"
FOLLOWTHROUGH_POLICY = followthrough_policy_payload()
ENTRY_SCOPE_POLICY = {
    "contract": "strategy-one-empty-exclusion-entry-scope-v1",
    "parent_authority": "fully_certified_original_candidate_activation_hod_entry_products",
    "excluded_population": "certified_zero_candidate_tickers_only",
    "projection": "exact_retained_candidate_activation_hod_inputs",
    "scope_token": "original_entry_token_and_current_candidate_activation_hod_tokens_and_exclusions",
    "nonempty_exclusion": "fail_closed_requires_separately_certified_entry_products",
    "source_mutation": "none_read_only_certification",
}

BEHAVIOR = (
    "Strategy 10 inherits the exact pinned Strategy 9 trading behavior. Sole change: "
    "read-only entry certification verifies original candidate, activation, HOD and "
    "entry products before exact projection of zero-candidate ticker exclusions. "
    "Nonempty exclusions require separately certified entry products. Retain the "
    "scope token binding original authority and explicit exclusions. Preserve "
    "the completed post-first-held 5s failure exit: close and fresh bid at/below "
    "(original ask + initial stop)/2, MACD line below signal, quote age 0..1000000us, "
    "positive holdings and no pending exit. Session liquidation takes priority; "
    "fills use later certified liquidity. Missing inputs skip current proposal. "
    "Original ask cap, persistent acquisition, sizing/costs, fixed target, no adds, "
    "no 30s trailing, structural ratchets and session rules remain inherited. "
    "Backtest only at completed 100ms; live and public resume remain closed."
)
RULES = ("strategy-one-completed-entry-add-protection-v1",
         "strategy-two-extended-session-policy-v1",
         "strategy-three-current-session-activation-v1", "strategy-four-no-add-v1",
         "strategy-five-no-completed-30s-low-trailing-v1", "strategy-six-initial-target-only-v1",
         "strategy-eight-reference-ask-entry-cap-v1", "strategy-nine-followthrough-failure-v1", "strategy-ten-empty-exclusion-entry-scope-v1")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(10)
    values = dict(number=10, executor_strategy_id=installed.strategy_id,
                  executor_revision=10, evaluation_interval=installed.execution_interval,
                  input_contracts=REQUIRED_INPUTS, rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_ten_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(10)
    if (strategy.get("strategy_number") != 10 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 10 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 10 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 10 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 10 manifest shape differs")
    release = release_contract()
    release.verify()
    if (manifest["contract"] != release.canonical_payload()
            or manifest["approved_digest"] != release.approved_digest
            or manifest["session_policy"] != session_policy()
            or manifest["activation_policy"] != ACTIVATION_POLICY
            or manifest["add_policy"] != ADD_POLICY
            or manifest["trailing_policy"] != TRAILING_POLICY
            or manifest["target_policy"] != TARGET_POLICY
            or manifest["entry_price_policy"] != ENTRY_PRICE_POLICY
            or manifest["followthrough_policy"] != FOLLOWTHROUGH_POLICY
            or manifest["entry_scope_policy"] != ENTRY_SCOPE_POLICY
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 10 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 10 manifest approval/code seal differs")
    verify_installed_strategy_ten_release(manifest)
    return manifest


def verify_installed_strategy_ten_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(10)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 10 differs from its installed numbered registry seal")
    return release


def derive_strategy_ten_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 9 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 10 must derive from exact pinned certified Strategy 9")
    from .strategy_nine_release import verify_strategy_nine_manifest
    verify_strategy_nine_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 10 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=10, revision=10, name="Early Squeeze Strategy 10",
                               profile_id="strategy-one-10", profile_revision=10,
                               numbered_release=manifest)
    verify_strategy_ten_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-10", revision=10, definition_revision=10,
                   name="Early Squeeze Strategy 10", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 10 Backtest", description="Sealed extended-session Strategy 10",
                               profile_id="strategy-one-10")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-ten-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
