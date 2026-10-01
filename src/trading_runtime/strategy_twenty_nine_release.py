"""Pure approval-manifest validation for the Backtest-only twenty-ninth number.

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

from .strategy_twenty_eight_release import (
    ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY,
    ENTRY_PRICE_POLICY, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY,
    RECENT_BOS_POLICY, RULES as PARENT_RULES,
)

PARENT_REVISION_ID = "strategy-one-28:c3d72733-06ae-45f8-9c33-5cc0e3c1f9c9"
PARENT_PAYLOAD_HASH = "818200d70de2a99de30b5a6c48c0ef775699b3bd4fd714c7cee8dfb625510a0e"
from .strategy_twenty_eight_release import MOMENTUM_POLICY

from .strategy_strong_ten_second_momentum import (
    POLICY_ID, strong_ten_second_momentum_policy_payload,
)
from .strategy_twenty_eight_release import MOMENTUM_INPUTS

STRONG_TEN_SECOND_MOMENTUM_POLICY = strong_ten_second_momentum_policy_payload()

from .strategy_initial_strong_momentum import POLICY_ID as INITIAL_POLICY_ID, initial_strong_momentum_policy_payload
INITIAL_STRONG_MOMENTUM_POLICY = initial_strong_momentum_policy_payload()

from .strategy_initial_ten_percent import (
    POLICY_ID as FIRST_SETUP_GROWTH_POLICY_ID,
    first_setup_ten_percent_policy_payload,
)
FIRST_SETUP_GROWTH_POLICY = first_setup_ten_percent_policy_payload()

from .strategy_initial_price_break import (
    POLICY_ID as FIRST_PRICE_POLICY_ID, initial_price_break_policy_payload,
)
FIRST_PRICE_POLICY = {**initial_price_break_policy_payload(),
    "first_momentum": "strict_10pct_premarket_and_afterhours_original_first_setup",
    "current_momentum": "unchanged_parent25_strict_10pct",
    "afterhours_policy": "unchanged_parent25"}

from .strategy_persistent_risk_failure import POLICY_ID, persistent_risk_policy_payload
PREMARKET_FAILURE_POLICY = persistent_risk_policy_payload()

BEHAVIOR = (
    "Strategy 29 inherits exact pinned Strategy 28. Preserve the inclusive "
    "first-held minute quarter-original-risk PM and half-original-risk AH "
    "failure rule. Later held boundaries remain eligible for half original "
    "risk plus negative completed 5s MACD, valid completed close and a fresh "
    "bid at or below the same threshold. Time alone never exits. Original "
    "proposal ask and stop remain authoritative, with no duplicate pending "
    "exit, synthetic evidence or new indicator calculations. Entries, "
    "sizing, costs, targets, protection, V7 prior-day seed and RTH warming "
    "are inherited. Backtest only; live and public resume remain closed."
)

RULES = (*PARENT_RULES, POLICY_ID)



def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(29)
    values = dict(number=29, executor_strategy_id=installed.strategy_id,
                  executor_revision=29, evaluation_interval=installed.execution_interval,
                  input_contracts=(*REQUIRED_INPUTS, *MOMENTUM_INPUTS), rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_twenty_nine_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(29)
    if (strategy.get("strategy_number") != 29 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 29 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 29 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 29 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy", "momentum_policy", "strong_ten_second_momentum_policy", "initial_strong_momentum_policy", "first_setup_growth_policy", "first_price_break_policy", "premarket_failure_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 29 manifest shape differs")
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
        raise ValueError("Strategy 29 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 29 manifest approval/code seal differs")
    verify_installed_strategy_twenty_nine_release(manifest)
    return manifest


def verify_installed_strategy_twenty_nine_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(29)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 29 differs from its installed numbered registry seal")
    return release


def derive_strategy_twenty_nine_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 28 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 29 must derive from exact pinned certified Strategy 28")
    from .strategy_twenty_eight_release import verify_strategy_twenty_eight_manifest
    verify_strategy_twenty_eight_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 29 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "momentum_policy": deepcopy(MOMENTUM_POLICY), "strong_ten_second_momentum_policy": deepcopy(STRONG_TEN_SECOND_MOMENTUM_POLICY), "initial_strong_momentum_policy": deepcopy(INITIAL_STRONG_MOMENTUM_POLICY), "first_setup_growth_policy": deepcopy(FIRST_SETUP_GROWTH_POLICY), "first_price_break_policy": deepcopy(FIRST_PRICE_POLICY), "premarket_failure_policy": deepcopy(PREMARKET_FAILURE_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=29, revision=29, name="Early Squeeze Strategy 29",
                               profile_id="strategy-one-29", profile_revision=29,
                               numbered_release=manifest)
    verify_strategy_twenty_nine_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-29", revision=29, definition_revision=29,
                   name="Early Squeeze Strategy 29", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 29 Backtest", description="Sealed extended-session Strategy 29",
                               profile_id="strategy-one-29")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-twenty-nine-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
