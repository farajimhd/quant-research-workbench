"""Pure approval-manifest validation for the Backtest-only twelfth number.

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

PARENT_REVISION_ID = "strategy-one-11:895d462c-df55-41df-95a4-b066c14cc6e9"
PARENT_PAYLOAD_HASH = "6af195caed51284948e8809e53d0b679ab79b8064ff1fa736f3733d05a150dc2"
from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS
FOLLOWTHROUGH_POLICY = {
    **followthrough_policy_payload(),
    "early_failure_window_ms": EARLY_FAILURE_WINDOW_MS,
    "eligibility": "boundary_ms - first_held_boundary_ms <= early_failure_window_ms",
    "age_origin": "first_completed_broker_bucket_containing_positive_held_quantity",
    "window_endpoint": "inclusive",
    "expired_window": "no_failure_exit_proposal_keep_existing_protection",
}
ENTRY_SCOPE_POLICY = {
    "contract": "strategy-one-empty-exclusion-entry-scope-v1",
    "parent_authority": "fully_certified_original_candidate_activation_hod_entry_products",
    "excluded_population": "certified_zero_candidate_tickers_only",
    "projection": "exact_retained_candidate_activation_hod_inputs",
    "scope_token": "original_entry_token_and_current_candidate_activation_hod_tokens_and_exclusions",
    "nonempty_exclusion": "fail_closed_requires_separately_certified_entry_products",
    "source_mutation": "none_read_only_certification",
}

from .strategy_recent_bos_entry import MAX_BOS_ENTRY_AGE_MS

RECENT_BOS_POLICY = {
    "contract": "strategy-twelve-recent-bos-entry-v1",
    "maximum_bos_age_ms": MAX_BOS_ENTRY_AGE_MS,
    "eligibility": "0 <= boundary_ms - bos_break_boundary_ms <= maximum_bos_age_ms",
    "window_endpoint": "inclusive",
    "age_origin": "certified_completed_bos_break_boundary",
    "applies_to": "new_entries_and_reentries",
    "missing_bos": "reject_current_proposal",
    "malformed_or_future_bos": "fail_closed_integrity_error",
    "expired_bos": "reject_current_proposal",
    "position_management": "unchanged_no_age_based_exit",
}

BEHAVIOR = (
    "Strategy 12 inherits exact pinned Strategy 11. Sole change: entries and "
    "reentries require completed BOS age from 0 through 30000ms inclusive, "
    "measured from certified BOS break boundary to current proposal boundary. "
    "Missing or expired BOS rejects current proposal; malformed or future BOS fails "
    "closed as an integrity error. No age-based "
    "exit. Preserve completed input gates, sizing/costs, original ask cap, persistent "
    "acquisition, no adds, fixed target, no 30s trailing, structural ratchets, "
    "session activation/cutoff/liquidation and certified zero-candidate exclusion. "
    "Retain original follow-through failure conditions within inclusive 60000ms "
    "of first held boundary, normalized witness and original reason identity. "
    "Session liquidation has priority; execution requires later certified liquidity. "
    "Backtest only at completed 100ms; live and public resume closed."
)
RULES = ("strategy-one-completed-entry-add-protection-v1",
         "strategy-two-extended-session-policy-v1",
         "strategy-three-current-session-activation-v1", "strategy-four-no-add-v1",
         "strategy-five-no-completed-30s-low-trailing-v1", "strategy-six-initial-target-only-v1",
         "strategy-eight-reference-ask-entry-cap-v1", "strategy-nine-followthrough-failure-v1", "strategy-ten-empty-exclusion-entry-scope-v1", "strategy-eleven-early-followthrough-failure-v1", "strategy-twelve-recent-bos-entry-v1")


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(12)
    values = dict(number=12, executor_strategy_id=installed.strategy_id,
                  executor_revision=12, evaluation_interval=installed.execution_interval,
                  input_contracts=REQUIRED_INPUTS, rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_twelve_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(12)
    if (strategy.get("strategy_number") != 12 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 12 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 12 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 12 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 12 manifest shape differs")
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
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 12 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 12 manifest approval/code seal differs")
    verify_installed_strategy_twelve_release(manifest)
    return manifest


def verify_installed_strategy_twelve_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(12)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 12 differs from its installed numbered registry seal")
    return release


def derive_strategy_twelve_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 11 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 12 must derive from exact pinned certified Strategy 11")
    from .strategy_eleven_release import verify_strategy_eleven_manifest
    verify_strategy_eleven_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 12 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=12, revision=12, name="Early Squeeze Strategy 12",
                               profile_id="strategy-one-12", profile_revision=12,
                               numbered_release=manifest)
    verify_strategy_twelve_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-12", revision=12, definition_revision=12,
                   name="Early Squeeze Strategy 12", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 12 Backtest", description="Sealed extended-session Strategy 12",
                               profile_id="strategy-one-12")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-twelve-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
