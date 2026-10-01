"""Pure approval-manifest validation for the Backtest-only fifteenth number.

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

from .strategy_fourteen_release import (
    ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY,
    ENTRY_PRICE_POLICY, ENTRY_SCOPE_POLICY,
    RECENT_BOS_POLICY, RULES as PARENT_RULES,
)

PARENT_REVISION_ID = "strategy-one-14:5166c8f6-6c35-4e35-bd3e-4fe67091bce3"
PARENT_PAYLOAD_HASH = "725b5e6b99e4406c353c6dc8020902dfe9fc1859f292b17678c0a2618f23d988"
from .strategy_fourteen_release import MOMENTUM_POLICY
from .numbered_fixed_strategy import followthrough_policy_payload

FOLLOWTHROUGH_POLICY = followthrough_policy_payload()

BEHAVIOR = (
    "Strategy 15 inherits exact pinned Strategy 14, including rising completed "
    "1s/10s momentum entry, recent supported BOS, protection, sizing, costs, "
    "extended sessions, LGHL exclusion and prior-day V7/RTH warming. Sole "
    "behavioral change: the existing completed 5s half-original-stop-distance "
    "failure exit with negative MACD histogram and fresh qualifying bid remains "
    "eligible throughout the holding, removing the 60-second eligibility limit. "
    "Original proposal ask and initial stop remain fixed; the first eligible "
    "bucket must be wholly after the first held boundary. Backtest only; live "
    "and public interrupted resume remain closed."
)
# The removed eligibility window is no longer an active rule contract.
RULES = (*(rule for rule in PARENT_RULES
           if rule != "strategy-eleven-early-followthrough-failure-v1"),
         "strategy-fifteen-unlimited-followthrough-failure-v1")

MOMENTUM_INPUTS = ("arte.indicators_v1@1s:adjacent-completed-macd",
                   "arte.indicators_v1@10s:adjacent-completed-macd")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(15)
    values = dict(number=15, executor_strategy_id=installed.strategy_id,
                  executor_revision=15, evaluation_interval=installed.execution_interval,
                  input_contracts=(*REQUIRED_INPUTS, *MOMENTUM_INPUTS), rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_fifteen_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(15)
    if (strategy.get("strategy_number") != 15 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 15 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 15 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 15 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy", "momentum_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 15 manifest shape differs")
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
            or manifest["recent_bos_policy"] != RECENT_BOS_POLICY
            or manifest["momentum_policy"] != MOMENTUM_POLICY
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 15 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 15 manifest approval/code seal differs")
    verify_installed_strategy_fifteen_release(manifest)
    return manifest


def verify_installed_strategy_fifteen_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(15)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 15 differs from its installed numbered registry seal")
    return release


def derive_strategy_fifteen_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 14 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 15 must derive from exact pinned certified Strategy 14")
    from .strategy_fourteen_release import verify_strategy_fourteen_manifest
    verify_strategy_fourteen_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 15 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "momentum_policy": deepcopy(MOMENTUM_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=15, revision=15, name="Early Squeeze Strategy 15",
                               profile_id="strategy-one-15", profile_revision=15,
                               numbered_release=manifest)
    verify_strategy_fifteen_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-15", revision=15, definition_revision=15,
                   name="Early Squeeze Strategy 15", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 15 Backtest", description="Sealed extended-session Strategy 15",
                               profile_id="strategy-one-15")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-fifteen-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
