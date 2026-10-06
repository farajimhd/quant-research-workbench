"""Prepared full-configuration policy deltas; no registration or native admission.

Unlike inherit-only capabilities@1, this separate @2 schema permits only the
two named generic deltas. A prepared digest is not installed source authority.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import re

from . import declared_native_fixed_capabilities as inherited
from .drawdown_measure_policy import DrawdownMeasurePolicy
from .entry_spread_risk import EntrySpreadRiskPolicy
from .strategy_registry import NumberedStrategyRelease
from .strategy_one_configuration_tree import encode_nodes, decode_nodes, node_hash

INPUT_CONTRACT = "declared-native-fixed-candidate-input@2"
RULE_CONTRACT = "declared-native-fixed-candidate-rule@2"
QUOTE_SOURCE_CONTRACT = "declared-entry-spread-risk-quote-source@2"
RECOVERY_CONTRACT = "entry-spread-risk-intent-recovery@2"
SPREAD_RULE = "entry-spread-at-most-one-quarter-original-risk@1"
QUOTE_SOURCE_PAYLOAD = {
    "contract": QUOTE_SOURCE_CONTRACT, "predicate": "source_qualified_uuid_tuple",
    "attempt_output": "distinct_liquidity_attempt_id", "clock": "exact_completed_100ms_proposal",
    "coverage": "exact_parent_eligible_keys", "missing_coverage": "fail_certification",
}


@dataclass(frozen=True)
class NativeFixedPolicyDelta:
    drawdown: DrawdownMeasurePolicy | None = None
    spread: EntrySpreadRiskPolicy | None = None

    def __post_init__(self):
        if self.drawdown is not None:
            if type(self.drawdown) is not DrawdownMeasurePolicy:
                raise ValueError("Delta requires exact typed drawdown policy")
            self.drawdown.__post_init__()
        if self.spread is not None:
            if type(self.spread) is not EntrySpreadRiskPolicy:
                raise ValueError("Delta requires exact typed spread policy")
            self.spread.__post_init__()
            if (self.spread.policy_id != SPREAD_RULE
                    or self.spread.maximum_spread_original_risk != (1, 4)):
                raise ValueError("Only declared quarter-original-risk delta is supported")
        if self.drawdown is None and self.spread is None:
            raise ValueError("An empty delta must use inherit-only@1")

    def payload(self):
        return {"drawdown": None if self.drawdown is None else self.drawdown.payload(),
                "spread": None if self.spread is None else self.spread.payload(),
                "quote_source": None if self.spread is None else deepcopy(QUOTE_SOURCE_PAYLOAD),
                "intent_recovery": None if self.spread is None else RECOVERY_CONTRACT}


def parse_policy_delta(value):
    inherited._keys(value, {"drawdown", "spread", "quote_source", "intent_recovery"}, "policy delta")
    # Equality alone would accept bool/int and float/int aliases; canonical JSON
    # with strict built-in validation preserves the actual scalar types.
    inherited._json(value)
    drawdown = None if value["drawdown"] is None else DrawdownMeasurePolicy()
    spread = None if value["spread"] is None else EntrySpreadRiskPolicy(SPREAD_RULE, (1, 4))
    result = NativeFixedPolicyDelta(drawdown, spread)
    if inherited._json(result.payload()) != inherited._json(value):
        raise ValueError("Delta differs from supported complete policy payload")
    return result


@dataclass(frozen=True)
class CandidateLabels:
    name: str
    profile_id: str
    run_name: str
    run_description: str

    def __post_init__(self):
        for value in (self.name, self.profile_id, self.run_name, self.run_description):
            if type(value) is not str or value != value.strip() or not 1 <= len(value) <= 512:
                raise ValueError("Candidate labels require bounded exact strings")

    def payload(self):
        return dict(name=self.name, profile_id=self.profile_id, run_name=self.run_name,
                    run_description=self.run_description)


@dataclass(frozen=True)
class NativeFixedCandidateSpec:
    base: inherited.DeclaredNativeFixedCapabilities
    delta: NativeFixedPolicyDelta
    labels: CandidateLabels

    def __post_init__(self):
        if (type(self.base) is not inherited.DeclaredNativeFixedCapabilities
                or type(self.delta) is not NativeFixedPolicyDelta or type(self.labels) is not CandidateLabels):
            raise ValueError("Candidate needs exact typed base, delta and labels")
        self.base.__post_init__()
        self.delta.__post_init__()
        self.labels.__post_init__()
        optional = self.base.payload()["inherited"]["optional_policies"]
        if ((self.delta.drawdown and optional["drawdown_measure_policy"] is not None)
                or (self.delta.spread and optional["entry_spread_risk_policy"] is not None)):
            raise ValueError("Delta cannot replace or redundantly select inherited policy")

    def payload(self):
        return {"schema_version": 2, "base": self.base.payload(),
                "delta": self.delta.payload(), "labels": self.labels.payload()}

    def release(self):
        base = self.base.payload()["inherited"]
        inputs = list(base["input_contracts"]) + [INPUT_CONTRACT]
        rules = list(base["rule_set_contracts"]) + [RULE_CONTRACT]
        if self.delta.drawdown:
            inputs.append(self.delta.drawdown.policy_id)
            rules.append(self.delta.drawdown.policy_id)
        if self.delta.spread:
            inputs.extend((QUOTE_SOURCE_CONTRACT, "typed-entry-spread-risk-v4@1", RECOVERY_CONTRACT))
            rules.append(self.delta.spread.policy_id)
        if len(set(inputs)) != len(inputs) or len(set(rules)) != len(rules):
            raise ValueError("Candidate declarations overlap inherited authority")
        identity = self.base.identity
        behavior = ("Prepared-only exact parent inheritance; declared deltas: "
                    + ", ".join(policy.policy_id for policy in (self.delta.drawdown, self.delta.spread)
                                if policy is not None)
                    + ". Full manifest pins policy, clock, coverage and recovery semantics. "
                      "All other parent economics/entries/exits are inherited. "
                      "No installed/native/financial acceptance.")
        release = NumberedStrategyRelease(identity.strategy_number, identity.strategy_id,
            identity.revision, identity.execution_interval, tuple(inputs), tuple(rules), behavior, "")
        release = replace(release, approved_digest=release.digest())
        release.verify()
        return release


def parse_candidate_spec(value):
    """Structural parsing only; parent authority comes from independent readback."""
    inherited._keys(value, {"schema_version", "base", "delta", "labels"}, "candidate spec")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("Unsupported candidate schema")
    inherited._keys(value["labels"], {"name", "profile_id", "run_name", "run_description"}, "labels")
    result = NativeFixedCandidateSpec(inherited.parse_native_fixed_capabilities(value["base"]),
                                     parse_policy_delta(value["delta"]), CandidateLabels(**value["labels"]))
    result.release()
    return result


def _verified_parent(client, spec):
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    parent = certify_numbered_configuration(client, spec.base.parent.strategy_number)
    expected = inherited.capability_declaration_for_parent(spec.base.identity, parent)
    inherited.parse_native_fixed_capabilities(spec.base.payload(), expected_parent=expected)
    certificate = certify_numbered_fixed_v4_projection(parent.strategy_number)
    inherited._digest(certificate, "parent source certificate")
    return parent, certificate


def _compile(parent, spec, approval):
    inherited._keys(approval, {"approved_code_commit", "approved_code_fingerprint", "approval_reference"}, "approval")
    if type(approval["approved_code_commit"]) is not str or re.fullmatch("[0-9a-f]{40}", approval["approved_code_commit"]) is None:
        raise ValueError("Candidate source commit is invalid")
    inherited._digest(approval["approved_code_fingerprint"], "source fingerprint")
    ref = approval["approval_reference"]
    if type(ref) is not str or ref != ref.strip() or not 1 <= len(ref) <= 512:
        raise ValueError("Candidate approval reference is invalid")
    payload = deepcopy(parent.payload)
    if payload.get("assignments"):
        raise ValueError("Candidate cannot inherit mutable assignments")
    release = spec.release()
    policies = deepcopy(spec.base.payload()["inherited"]["policies"])
    if spec.delta.drawdown:
        policies["drawdown_measure_policy"] = spec.delta.drawdown.payload()
    if spec.delta.spread:
        policies["entry_spread_risk_policy"] = spec.delta.spread.payload()
        policies["entry_spread_risk_quote_source"] = deepcopy(QUOTE_SOURCE_PAYLOAD)
    manifest = dict(policies, contract=release.canonical_payload(), approved_digest=release.approved_digest,
        **approval, publication_mode="backtest_only", source_revision_id=spec.base.parent.revision_id,
        source_payload_hash=spec.base.parent.payload_hash, native_fixed_candidate=spec.payload())
    manifest["manifest_hash"] = sha256(inherited._json(manifest).encode()).hexdigest()
    identity, labels = spec.base.identity, spec.labels
    payload["strategy"].update(strategy_number=identity.strategy_number, strategy_id=identity.strategy_id,
        revision=identity.revision, execution_interval=identity.execution_interval, name=labels.name,
        profile_id=labels.profile_id, profile_revision=identity.revision, numbered_release=manifest)
    payload["strategy_profile"].update(profile_id=labels.profile_id, revision=identity.revision,
        definition_revision=identity.revision, name=labels.name, description=release.behavior_specification)
    payload["run_plan"].update(name=labels.run_name, description=labels.run_description, profile_id=labels.profile_id)
    nodes = encode_nodes(payload)
    return {"source_candidate_id": "declared-native-fixed-candidate-from:" + spec.base.parent.revision_id,
            "source_candidate_hash": spec.base.parent.payload_hash,
            "payload_hash": sha256(inherited._json(payload).encode()).hexdigest(),
            "node_hash": node_hash(nodes), "node_count": len(nodes), "nodes": list(nodes), "payload": payload}


def prepare_candidate_configuration(client, spec, *, approval):
    """Independently read parent and compile source-only complete configuration."""
    if type(spec) is not NativeFixedCandidateSpec:
        raise ValueError("Preparation requires exact typed candidate spec")
    spec = parse_candidate_spec(spec.payload())
    parent, _ = _verified_parent(client, spec)
    return _compile(parent, spec, approval)


def verify_prepared_candidate_configuration(client, spec, envelope, *, approval):
    """Reject caller resealing, foreign nodes and every undeclared config delta."""
    expected = prepare_candidate_configuration(client, spec, approval=approval)
    inherited._keys(envelope, set(expected), "prepared envelope")
    inherited._json(envelope)
    if (type(envelope["node_count"]) is not int or type(envelope["nodes"]) is not list
            or len(envelope["nodes"]) != envelope["node_count"]
            or node_hash(envelope["nodes"]) != envelope["node_hash"]
            or inherited._json(decode_nodes(envelope["nodes"])) != inherited._json(envelope["payload"])
            or sha256(inherited._json(envelope["payload"]).encode()).hexdigest() != envelope["payload_hash"]
            or inherited._json(envelope) != inherited._json(expected)):
        raise ValueError("Prepared candidate differs from exact full parent inheritance")
    return spec.release()
