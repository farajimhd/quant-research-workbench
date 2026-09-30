"""Pure approval-manifest validation for the Backtest-only ninth number.

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

PARENT_REVISION_ID = "strategy-one-8:bb1f983a-869f-4b70-b3f3-44c9032bded8"
PARENT_PAYLOAD_HASH = "d642e40bcee00bdb047d15376aea5d70796d58b49b4f153b97ca0d5549202421"
FOLLOWTHROUGH_POLICY = followthrough_policy_payload()

BEHAVIOR = (
    "Strategy 9 inherits the exact pinned Strategy 8 release with one early failure exit. "
    "At an exact completed 5s boundary, a whole bucket strictly after the first held "
    "bucket must close at or below (original proposal ask + initial stop)/2, with "
    "completed 5s MACD line below signal and a bid observed 0..1000000us ago still "
    "at or below that threshold. Require positive holdings and no pending exit; "
    "missing or stale inputs skip this proposal without delayed retry. Session "
    "liquidation takes priority. Current liquidity is consumed before management; "
    "exit fills require later real certified liquidity. Preserve original ask cap, "
    "persistent complete-remainder acquisition, sizing/costs, fixed target, no adds, "
    "no subsequent 30s-low trailing, structural ratchets and extended-session rules. "
    "Backtest only, completed 100ms; live admission and public resume remain closed."
)
RULES = ("strategy-one-completed-entry-add-protection-v1",
         "strategy-two-extended-session-policy-v1",
         "strategy-three-current-session-activation-v1", "strategy-four-no-add-v1",
         "strategy-five-no-completed-30s-low-trailing-v1", "strategy-six-initial-target-only-v1",
         "strategy-eight-reference-ask-entry-cap-v1", "strategy-nine-followthrough-failure-v1")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(9)
    values = dict(number=9, executor_strategy_id=installed.strategy_id,
                  executor_revision=9, evaluation_interval=installed.execution_interval,
                  input_contracts=REQUIRED_INPUTS, rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_nine_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(9)
    if (strategy.get("strategy_number") != 9 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 9 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 9 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 9 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 9 manifest shape differs")
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
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 9 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 9 manifest approval/code seal differs")
    verify_installed_strategy_nine_release(manifest)
    return manifest


def verify_installed_strategy_nine_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(9)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 9 differs from its installed numbered registry seal")
    return release


def derive_strategy_nine_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 8 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 9 must derive from exact pinned certified Strategy 8")
    from .strategy_eight_release import verify_strategy_eight_manifest
    verify_strategy_eight_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 9 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=9, revision=9, name="Early Squeeze Strategy 9",
                               profile_id="strategy-one-9", profile_revision=9,
                               numbered_release=manifest)
    verify_strategy_nine_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-9", revision=9, definition_revision=9,
                   name="Early Squeeze Strategy 9", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 9 Backtest", description="Sealed extended-session Strategy 9",
                               profile_id="strategy-one-9")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-nine-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
