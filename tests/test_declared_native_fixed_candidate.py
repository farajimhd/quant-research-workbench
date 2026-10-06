"""Unregistered prepared candidates; real normalized parent and source checks."""
from copy import deepcopy
from dataclasses import replace, FrozenInstanceError
from hashlib import sha256

import pytest

from test_declared_native_fixed_capabilities import prepared
from src.trading_runtime import declared_native_fixed_candidate as module
from src.trading_runtime import declared_native_fixed_capabilities as original
from src.trading_runtime.drawdown_measure_policy import DrawdownMeasurePolicy
from src.trading_runtime.entry_spread_risk import EntrySpreadRiskPolicy
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash

APPROVAL = dict(approved_code_commit="b" * 40, approved_code_fingerprint="c" * 64,
                approval_reference="unregistered-prepared-source-only")


def candidate(prepared, spread=False):
    return module.NativeFixedCandidateSpec(prepared[0], module.NativeFixedPolicyDelta(
        DrawdownMeasurePolicy(), EntrySpreadRiskPolicy(module.SPREAD_RULE, (1, 4)) if spread else None),
        module.CandidateLabels("Unregistered candidate", "unregistered-fixture", "Prepared research", "Source only"))


def test_complete_configuration_roundtrip_and_original_inherit_only_unchanged(prepared):
    spec = candidate(prepared)
    parent, client = prepared[3], prepared[2]
    assert original.parse_native_fixed_capabilities(spec.base.payload(), expected_parent=spec.base) == spec.base
    assert module.parse_candidate_spec(spec.payload()) == spec
    envelope = module.prepare_candidate_configuration(client, spec, approval=APPROVAL)
    release = module.verify_prepared_candidate_configuration(client, spec, envelope, approval=APPROVAL)
    assert release.number == spec.base.identity.strategy_number
    assert release.number != parent.strategy_number
    assert release.input_contracts[-1] == DrawdownMeasurePolicy().policy_id
    assert release.rule_set_contracts[-1] == DrawdownMeasurePolicy().policy_id
    # Restoring only allowed identity/manifest/display changes gives exact parent.
    restored = deepcopy(envelope["payload"])
    for section, fields in {
        "strategy": ("strategy_number", "strategy_id", "revision", "execution_interval", "name", "profile_id", "profile_revision", "numbered_release"),
        "strategy_profile": ("profile_id", "revision", "definition_revision", "name", "description"),
        "run_plan": ("name", "description", "profile_id"),
    }.items():
        for field in fields:
            if field in parent.payload[section]: restored[section][field] = deepcopy(parent.payload[section][field])
            else: restored[section].pop(field, None)
    assert restored == parent.payload
    if parent.strategy_number == 59:
        assert envelope["payload"]["strategy"]["numbered_release"]["entry_momentum_growth_policy"] == parent.payload["strategy"]["numbered_release"]["entry_momentum_growth_policy"]
    assert client.queries and all(query.startswith("SELECT ") for query in client.queries)
    with pytest.raises(FrozenInstanceError): spec.delta = None
    copy = spec.payload()
    copy["base"]["inherited"]["flags"]["allows_adds"] = True
    assert spec.payload()["base"]["inherited"]["flags"]["allows_adds"] is False


def test_frozen_combined_delta_preserves_momentum_and_technical_source_binding(prepared):
    if prepared[3].strategy_number == 57:
        with pytest.raises(ValueError, match="replace or redundantly"):
            candidate(prepared, True)
        return
    spec = candidate(prepared, True)
    envelope = module.prepare_candidate_configuration(prepared[2], spec, approval=APPROVAL)
    release = module.verify_prepared_candidate_configuration(prepared[2], spec, envelope, approval=APPROVAL)
    manifest = envelope["payload"]["strategy"]["numbered_release"]
    assert manifest["entry_momentum_growth_policy"] == prepared[3].payload["strategy"]["numbered_release"]["entry_momentum_growth_policy"]
    assert manifest["entry_spread_risk_policy"] == EntrySpreadRiskPolicy(module.SPREAD_RULE, (1, 4)).payload()
    assert manifest["entry_spread_risk_quote_source"] == module.QUOTE_SOURCE_PAYLOAD
    assert module.RECOVERY_CONTRACT in release.input_contracts
    assert module.QUOTE_SOURCE_CONTRACT in release.input_contracts
    assert module.SPREAD_RULE in release.rule_set_contracts


@pytest.mark.parametrize("mutation", ("missing", "unknown", "version-bool", "version-foreign", "fraction-bool", "fraction-float", "half-risk", "foreign-rule", "missing-source", "foreign-recovery", "drawdown-foreign", "drawdown-scale", "duplicate-input"))
def test_strict_complete_delta_schema(prepared, mutation):
    spec = candidate(prepared)
    value = spec.payload()
    if mutation == "missing": del value["delta"]["drawdown"]
    elif mutation == "unknown": value["delta"]["private_override"] = {}
    elif mutation == "version-bool": value["schema_version"] = True
    elif mutation == "version-foreign": value["schema_version"] = 3
    elif mutation == "drawdown-foreign": value["delta"]["drawdown"]["policy_id"] = "foreign@2"
    elif mutation == "drawdown-scale": value["delta"]["drawdown"]["typed_scale"] = 17
    elif mutation == "duplicate-input": value["base"]["inherited"]["input_contracts"].append(value["base"]["inherited"]["input_contracts"][0])
    else:
        value["delta"] = module.NativeFixedPolicyDelta(DrawdownMeasurePolicy(), EntrySpreadRiskPolicy(module.SPREAD_RULE, (1, 4))).payload()
        if mutation == "fraction-bool": value["delta"]["spread"]["maximum_spread_original_risk"][0] = True
        elif mutation == "fraction-float": value["delta"]["spread"]["maximum_spread_original_risk"][0] = 1.0
        elif mutation == "half-risk": value["delta"]["spread"]["maximum_spread_original_risk"][1] = 2
        elif mutation == "foreign-rule": value["delta"]["spread"]["policy_id"] = "foreign@1"
        elif mutation == "missing-source": value["delta"]["quote_source"] = None
        elif mutation == "foreign-recovery": value["delta"]["intent_recovery"] = "foreign@2"
    with pytest.raises(ValueError): module.parse_candidate_spec(value)


def reseal(envelope):
    manifest = envelope["payload"]["strategy"]["numbered_release"]
    manifest["manifest_hash"] = sha256(original._json({k: v for k, v in manifest.items() if k != "manifest_hash"}).encode()).hexdigest()
    envelope["nodes"] = list(encode_nodes(envelope["payload"]))
    envelope["node_count"] = len(envelope["nodes"])
    envelope["node_hash"] = node_hash(envelope["nodes"])
    envelope["payload_hash"] = sha256(original._json(envelope["payload"]).encode()).hexdigest()


@pytest.mark.parametrize("mutation", ("economics", "run-state", "fees", "policy", "missing-node", "foreign-node", "count-bool", "lineage", "approval", "unknown-envelope"))
def test_actual_prepared_verifier_rejects_caller_resealed_full_configuration(prepared, mutation):
    spec = candidate(prepared)
    envelope = module.prepare_candidate_configuration(prepared[2], spec, approval=APPROVAL)
    if mutation == "economics": envelope["payload"]["strategy"]["parameters"]["execution"]["tick_size"] = 999
    elif mutation == "run-state": envelope["payload"]["run_plan"]["private_state"] = "foreign"
    elif mutation == "fees": envelope["payload"]["private_cost_override"] = 0
    elif mutation == "policy": envelope["payload"]["strategy"]["numbered_release"]["drawdown_measure_policy"]["typed_scale"] = 17
    elif mutation == "lineage": envelope["source_candidate_hash"] = "f" * 64
    elif mutation == "approval": envelope["payload"]["strategy"]["numbered_release"]["approved_code_fingerprint"] = "f" * 64
    elif mutation == "unknown-envelope": envelope["private"] = True
    reseal(envelope)
    if mutation == "missing-node": envelope["nodes"].pop()
    elif mutation == "foreign-node": envelope["nodes"][0]["value_kind"] = "unknown"
    elif mutation == "count-bool": envelope["node_count"] = True
    with pytest.raises(ValueError):
        module.verify_prepared_candidate_configuration(prepared[2], spec, envelope, approval=APPROVAL)


def test_exact_parent_pin_and_parent_economics_are_independently_verified(prepared):
    spec = candidate(prepared)
    bad_parent = replace(spec.base.parent, configuration_token="f" * 64)
    foreign = replace(spec, base=replace(spec.base, parent=bad_parent))
    with pytest.raises(ValueError): module.prepare_candidate_configuration(prepared[2], foreign, approval=APPROVAL)
    prepared[2].payloads[prepared[3].strategy_number]["strategy"]["parameters"]["execution"]["tick_size"] = 999
    with pytest.raises(RuntimeError, match="certified inheritance"):
        module.prepare_candidate_configuration(prepared[2], spec, approval=APPROVAL)


@pytest.mark.parametrize("value", (None, {}, True, "foreign"))
def test_no_freeflag_policy_activation(value):
    with pytest.raises(ValueError): module.NativeFixedPolicyDelta(value, None)
