"""Pure approval-manifest validation for the Backtest-only thirteenth number.

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

from .strategy_twelve_release import (
    ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY,
    ENTRY_PRICE_POLICY, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY,
    RECENT_BOS_POLICY, RULES as PARENT_RULES,
)
from .strategy_rising_momentum_entry import MOMENTUM_RESOLUTIONS_MS

PARENT_REVISION_ID = "strategy-one-12:322bd3bf-bfd5-4c47-be38-fb77c9f15444"
PARENT_PAYLOAD_HASH = "ad3a9bae8615d43c24d6de4bb8d68869c7aa02cb1512f1c541ddab0586c1fb87"
MOMENTUM_POLICY = {
    "contract": "strategy-thirteen-rising-completed-momentum-v1",
    "resolutions_ms": list(MOMENTUM_RESOLUTIONS_MS),
    "histogram": "producer_macd_line_minus_producer_macd_signal",
    "eligibility": "current_histogram > adjacent_prior_histogram_on_either_resolution",
    "current_source": "exact_latest_completed_bucket_at_proposal_boundary",
    "prior_source": "immediately_preceding_bucket_same_resolution_no_carry_forward",
    "source_authority": "certified_build_and_technical_attempt_and_market_plan",
    "missing_or_null": "reject_branch",
    "forming_stale_nonadjacent_or_invented": "fail_closed_integrity_error",
    "scope": "new_entries_and_reentries_only",
    "persistence": "two_normalized_source_observation_rows_per_entry",
}
BEHAVIOR = (
    "Strategy 13 inherits exact pinned Strategy 12. Sole change: entry and reentry "
    "require strictly rising MACD histogram on either completed 1s or 10s source, "
    "compared with its immediately adjacent predecessor at proposal time. "
    "Missing/null observations reject their branch; forming/stale/nonadjacent or "
    "invented evidence fails closed. Preserve recent supported BOS through 30000ms, "
    "all completed entry gates, sizing/costs, original ask cap, persistent acquisition, "
    "no adds, fixed target, no completed-30s trailing, structural stop ratchets, "
    "extended-session activation/cutoff/liquidation and certified LGHL exclusion. "
    "Retain failure exit within inclusive first-held 60000ms, session exit priority, "
    "later certified liquidity fills, and prior-day V7 seed plus regular-session warm-up. "
    "Exact normalized momentum source witnesses precede Portfolio admission and cold "
    "recovery reruns the same rule. Backtest only at completed100ms; live/public resume closed."
)
RULES = (*PARENT_RULES, "strategy-thirteen-rising-completed-momentum-v1")
MOMENTUM_INPUTS = ("arte.indicators_v1@1s:adjacent-completed-macd",
                   "arte.indicators_v1@10s:adjacent-completed-macd")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(13)
    values = dict(number=13, executor_strategy_id=installed.strategy_id,
                  executor_revision=13, evaluation_interval=installed.execution_interval,
                  input_contracts=(*REQUIRED_INPUTS, *MOMENTUM_INPUTS), rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_thirteen_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(13)
    if (strategy.get("strategy_number") != 13 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 13 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 13 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 13 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy", "momentum_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 13 manifest shape differs")
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
        raise ValueError("Strategy 13 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 13 manifest approval/code seal differs")
    verify_installed_strategy_thirteen_release(manifest)
    return manifest


def verify_installed_strategy_thirteen_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(13)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 13 differs from its installed numbered registry seal")
    return release


def derive_strategy_thirteen_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 12 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 13 must derive from exact pinned certified Strategy 12")
    from .strategy_twelve_release import verify_strategy_twelve_manifest
    verify_strategy_twelve_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 13 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "momentum_policy": deepcopy(MOMENTUM_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=13, revision=13, name="Early Squeeze Strategy 13",
                               profile_id="strategy-one-13", profile_revision=13,
                               numbered_release=manifest)
    verify_strategy_thirteen_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-13", revision=13, definition_revision=13,
                   name="Early Squeeze Strategy 13", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 13 Backtest", description="Sealed extended-session Strategy 13",
                               profile_id="strategy-one-13")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-thirteen-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
