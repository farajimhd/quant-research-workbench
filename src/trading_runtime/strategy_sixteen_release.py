"""Pure approval-manifest validation for the Backtest-only sixteenth number.

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

from .strategy_fifteen_release import (
    ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY,
    ENTRY_PRICE_POLICY, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY,
    RECENT_BOS_POLICY, RULES as PARENT_RULES,
)

PARENT_REVISION_ID = "strategy-one-15:9612bd76-0faa-4b40-a13a-54515c2fb53f"
PARENT_PAYLOAD_HASH = "fdc313a3e446d7d8f7e9b038da573c367ccfc5e5cc8d56fd94a919f842c2ccd2"
from .strategy_fifteen_release import MOMENTUM_POLICY

SESSION_EXIT_AUTHORITY_POLICY = {
    "contract": "strategy-sixteen-shared-numbered-session-reason-authority-v1",
    "reason_authority": "numbered_session_exit_reason(strategy_number)",
    "consumers": "session_exit_factory_runtime_admission_and_memory_journal",
    "number_binding": "exact_installed_integer_strategy_number",
    "session_timing": "unchanged_inherited_session_policy",
    "failure_exit": "unchanged_inherited_unbounded_followthrough_policy",
}

BEHAVIOR = (
    "Strategy 16 inherits exact pinned Strategy 15 with all entry, sizing, costs, "
    "protection and exit rules unchanged, including rising completed 1s/10s "
    "momentum, recent supported BOS, unbounded completed 5s half-original-risk "
    "failure exit, fixed target, original ask cap, no adds and no 30s trailing. "
    "Sole operational correction: the session exit factory, runtime admission "
    "and memory journal validate the same shared numbered session reason "
    "authority, bound to the exact installed strategy number. Preserve extended "
    "session activation, cutoff, liquidation priority and later certified fills. "
    "Backtest only; live and public interrupted resume remain closed."
)
RULES = (*PARENT_RULES, "strategy-sixteen-shared-numbered-session-reason-authority-v1")

from .strategy_fifteen_release import MOMENTUM_INPUTS


def release_contract() -> NumberedStrategyRelease:
    installed = numbered_fixed_strategy(16)
    values = dict(number=16, executor_strategy_id=installed.strategy_id,
                  executor_revision=16, evaluation_interval=installed.execution_interval,
                  input_contracts=(*REQUIRED_INPUTS, *MOMENTUM_INPUTS), rule_set_contracts=RULES,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest="")
    return NumberedStrategyRelease(**values, approved_digest=draft.digest())


def session_policy() -> dict[str, Any]:
    return session_policy_payload()


def verify_strategy_sixteen_manifest(strategy: Mapping[str, Any]) -> dict[str, Any]:
    installed = numbered_fixed_strategy(16)
    if (strategy.get("strategy_number") != 16 or type(strategy.get("strategy_number")) is not int
            or strategy.get("revision") != 16 or type(strategy.get("revision")) is not int
            or strategy.get("strategy_id") != installed.strategy_id
            or strategy.get("execution_interval") != installed.execution_interval):
        raise ValueError("Strategy 16 installed execution identity differs")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 16 lacks its approved numbered release manifest")
    required = {"contract", "approved_digest", "approved_code_commit", "approved_code_fingerprint",
                "approval_reference", "publication_mode", "session_policy", "source_revision_id",
                "source_payload_hash", "manifest_hash", "activation_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy", "followthrough_policy", "entry_scope_policy", "recent_bos_policy", "momentum_policy", "session_exit_authority_policy"}
    if set(manifest) != required:
        raise ValueError("Strategy 16 manifest shape differs")
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
            or manifest["session_exit_authority_policy"] != SESSION_EXIT_AUTHORITY_POLICY
            or manifest["publication_mode"] != "backtest_only"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest["approved_code_commit"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["approved_code_fingerprint"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_payload_hash"]))
            or manifest["source_revision_id"] != PARENT_REVISION_ID
            or manifest["source_payload_hash"] != PARENT_PAYLOAD_HASH
            or not isinstance(manifest["approval_reference"], str)
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Strategy 16 manifest differs from its installed sealed contract")
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != "manifest_hash"}).encode()).hexdigest()
    if manifest["manifest_hash"] != seal:
        raise ValueError("Strategy 16 manifest approval/code seal differs")
    verify_installed_strategy_sixteen_release(manifest)
    return manifest


def verify_installed_strategy_sixteen_release(manifest: Mapping[str, Any]) -> NumberedStrategyRelease:
    """Preflight binds published approval to the immutable installed registry."""
    from .strategy_registry import fixed_strategy_executor, numbered_strategy
    release = numbered_strategy(16)
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    if (release.approved_digest != manifest.get("approved_digest")
            or release.canonical_payload() != manifest.get("contract")):
        raise ValueError("Published Strategy 16 differs from its installed numbered registry seal")
    return release


def derive_strategy_sixteen_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict[str, Any]:
    """Preserve inherited fields; expose every behavioral change in the tree."""
    if (source.strategy_number != 15 or source.revision()["revision_id"] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError("Strategy 16 must derive from exact pinned certified Strategy 15")
    from .strategy_fifteen_release import verify_strategy_fifteen_manifest
    verify_strategy_fifteen_manifest(source.payload["strategy"])
    payload = deepcopy(source.payload)
    if payload.get("assignments"):
        raise ValueError("Strategy 16 compiler cannot inherit mutable assignments")
    release = release_contract()
    manifest = {"contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": approved_code_commit,
                "approved_code_fingerprint": approved_code_fingerprint,
                "approval_reference": approval_reference, "publication_mode": "backtest_only",
                "session_policy": session_policy(), "activation_policy": deepcopy(ACTIVATION_POLICY), "add_policy": deepcopy(ADD_POLICY), "trailing_policy": deepcopy(TRAILING_POLICY), "target_policy": deepcopy(TARGET_POLICY), "entry_price_policy": deepcopy(ENTRY_PRICE_POLICY), "followthrough_policy": deepcopy(FOLLOWTHROUGH_POLICY), "entry_scope_policy": deepcopy(ENTRY_SCOPE_POLICY), "recent_bos_policy": deepcopy(RECENT_BOS_POLICY), "momentum_policy": deepcopy(MOMENTUM_POLICY), "session_exit_authority_policy": deepcopy(SESSION_EXIT_AUTHORITY_POLICY), "source_revision_id": source.revision()["revision_id"],
                "source_payload_hash": source.payload_hash}
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload["strategy"].update(strategy_number=16, revision=16, name="Early Squeeze Strategy 16",
                               profile_id="strategy-one-16", profile_revision=16,
                               numbered_release=manifest)
    verify_strategy_sixteen_manifest(payload["strategy"])
    profile = payload["strategy_profile"]
    profile.update(profile_id="strategy-one-16", revision=16, definition_revision=16,
                   name="Early Squeeze Strategy 16", description=release.behavior_specification)
    profile.setdefault("lifecycle", {}).setdefault("trading_behavior", {})["eligible_sessions"] = ["premarket", "afterhours"]
    payload["run_plan"].update(name="Strategy 16 Backtest", description="Sealed extended-session Strategy 16",
                               profile_id="strategy-one-16")
    nodes = encode_nodes(payload)
    return {"source_candidate_id": f"strategy-sixteen-from:{source.revision()['revision_id']}",
            "source_candidate_hash": source.payload_hash,
            "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "payload": payload}
