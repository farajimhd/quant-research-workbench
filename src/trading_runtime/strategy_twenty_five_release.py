"""Pure approval-manifest validation for the Backtest-only twenty-fiveth number.

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

from .strategy_twenty_four_release import (
    ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY,
    ENTRY_PRICE_POLICY, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY,
    RECENT_BOS_POLICY, RULES as PARENT_RULES,
)

PARENT_REVISION_ID = "strategy-one-24:c78b3815-0d1a-4e52-bb4e-084695fc1df8"
PARENT_PAYLOAD_HASH = "d5f8acab96b83a1ebc88fa5a34defbd14c190d02b64540ad01af6ef43d28382c"
from .strategy_twenty_four_release import MOMENTUM_POLICY

from .strategy_strong_ten_second_momentum import (
    POLICY_ID, strong_ten_second_momentum_policy_payload,
)
from .strategy_twenty_four_release import MOMENTUM_INPUTS

STRONG_TEN_SECOND_MOMENTUM_POLICY = strong_ten_second_momentum_policy_payload()

from .strategy_initial_strong_momentum import POLICY_ID as INITIAL_POLICY_ID, initial_strong_momentum_policy_payload
INITIAL_STRONG_MOMENTUM_POLICY = initial_strong_momentum_policy_payload()

from .strategy_initial_momentum_growth import (
    POLICY_ID as FIRST_SETUP_GROWTH_POLICY_ID,
    first_setup_momentum_growth_policy_payload,
)
FIRST_SETUP_GROWTH_POLICY = first_setup_momentum_growth_policy_payload()

from .strategy_initial_price_break import (
    POLICY_ID as FIRST_PRICE_POLICY_ID, initial_price_break_policy_payload,
)
FIRST_PRICE_POLICY = initial_price_break_policy_payload()

from .strategy_premarket_quarter_risk_failure import POLICY_ID, quarter_risk_policy_payload
PREMARKET_FAILURE_POLICY = quarter_risk_policy_payload()

BEHAVIOR = (
    "Strategy 25 inherits exact pinned Strategy 24. Sole behavior change: during "
    "the inclusive first-held 60000ms window, premarket failure requires losing "
    "one quarter rather than one half of original stop distance, together with "
    "negative completed 5s MACD and a fresh bid below the same threshold. "
    "The 5s bucket must be wholly after first held quantity. After-hours failure, "
    "all entries, first-price anchors, sizing, costs, protection, targets, V7 "
    "prior-day seeds and RTH warming remain inherited. Backtest only; live and "
    "public interrupted resume remain closed."
)

RULES = (*PARENT_RULES, POLICY_ID)


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(25)
    values = dict(number=25, executor_strategy_id=installed.strategy_id,
                  executor_revision=25, evaluation_interval=installed.execution_interval,
                  input_contracts=(*REQUIRED_INPUTS, *MOMENTUM_INPUTS), rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_twenty_five_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(25)
    if (strategy.get("strategy_number") != 25 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 25 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 25 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 25 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy", "momentum_policy", "strong_ten_second_momentum_policy", "initial_strong_momentum_policy", "first_setup_growth_policy", "first_price_break_policy", "premarket_failure_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 25 manifest shape differs")
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
            or manifest["strong_ten_second_momentum_policy"] != STRONG_TEN_SECOND_MOMENTUM_POLICY
            or manifest["initial_strong_momentum_policy"] != INITIAL_STRONG_MOMENTUM_POLICY
            or manifest["first_setup_growth_policy"] != FIRST_SETUP_GROWTH_POLICY
            or manifest["first_price_break_policy"] != FIRST_PRICE_POLICY
            or manifest["premarket_failure_policy"] != PREMARKET_FAILURE_POLICY
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 25 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 25 manifest approval/code seal differs")
    verify_installed_strategy_twenty_five_release(manifest)
    return manifest


def verify_installed_strategy_twenty_five_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(25)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 25 differs from its installed numbered registry seal")
    return release


def derive_strategy_twenty_five_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 24 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 25 must derive from exact pinned certified Strategy 24")
    from .strategy_twenty_four_release import verify_strategy_twenty_four_manifest
    verify_strategy_twenty_four_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 25 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "momentum_policy": deepcopy(MOMENTUM_POLICY), "strong_ten_second_momentum_policy": deepcopy(STRONG_TEN_SECOND_MOMENTUM_POLICY), "initial_strong_momentum_policy": deepcopy(INITIAL_STRONG_MOMENTUM_POLICY), "first_setup_growth_policy": deepcopy(FIRST_SETUP_GROWTH_POLICY), "first_price_break_policy": deepcopy(FIRST_PRICE_POLICY), "premarket_failure_policy": deepcopy(PREMARKET_FAILURE_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=25, revision=25, name="Early Squeeze Strategy 25",
                               profile_id="strategy-one-25", profile_revision=25,
                               numbered_release=manifest)
    verify_strategy_twenty_five_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-25", revision=25, definition_revision=25,
                   name="Early Squeeze Strategy 25", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 25 Backtest", description="Sealed extended-session Strategy 25",
                               profile_id="strategy-one-25")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-twenty-five-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
