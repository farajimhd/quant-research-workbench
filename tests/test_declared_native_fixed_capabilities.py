"""Unregistered own fixtures, real typed parent readback; no financial acceptance.

Only ancestor50 acquisition and future installed lookups are test seams.
Parent57/59 node/hash decoding, manifest and exact derivation remain real.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from test_strategy_fifty_two_release import source_fixture
from test_strategy_thirty_three_configuration import APPROVAL
from test_strategy_two_configuration import Reader
from src.backend import backtest_strategy_one_configuration as reader_module
from src.trading_runtime import declared_native_fixed_capabilities as module
from src.trading_runtime import strategy_registry as registry
from src.trading_runtime import strategy_fifty_seven_release, strategy_fifty_nine_release
from src.trading_runtime.journal_contract import canonical_json


@pytest.fixture(params=(57, 59))
def prepared(request, monkeypatch):
    number = request.param
    parent_module = strategy_fifty_seven_release if number == 57 else strategy_fifty_nine_release
    derive = (parent_module.derive_strategy_fifty_seven_configuration if number == 57
              else parent_module.derive_strategy_fifty_nine_configuration)
    ancestor = source_fixture()
    envelope = derive(ancestor, **APPROVAL)
    client = Reader()
    client.payloads[number] = deepcopy(envelope["payload"])
    client.sources[number] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
    actual_reader = reader_module.certify_numbered_configuration
    def read(reader, selected=1):
        if selected == 50:
            assert reader is client
            return ancestor
        return actual_reader(reader, selected)
    monkeypatch.setattr(reader_module, "certify_numbered_configuration", read)
    parent = actual_reader(client, number)
    own_number = 9001 if number == 57 else 9017  # Clearly unregistered fixtures.
    identity = module.NativeFixedIdentity(own_number, parent.payload["strategy"]["strategy_id"], own_number, "100ms")
    spec = module.capability_declaration_for_parent(identity, parent)
    inherited = spec.payload()["inherited"]
    values = dict(number=own_number, executor_strategy_id=identity.strategy_id,
                  executor_revision=own_number, evaluation_interval="100ms",
                  input_contracts=tuple(inherited["input_contracts"]) + (module.INPUT_CONTRACT,),
                  rule_set_contracts=tuple(inherited["rule_set_contracts"]) + (module.RULE_CONTRACT,),
                  behavior_specification="Unregistered inherit-only foundation fixture")
    draft = registry.NumberedStrategyRelease(**values, approved_digest="")
    release = replace(draft, approved_digest=draft.digest())
    registration = registry.FixedStrategyExecutorRegistration(
        identity.strategy_id, own_number, "100ms", lambda: spec,
        lambda assignments: pytest.fail("Foundation must never build an actor"))
    actual_release = registry.numbered_strategy
    actual_executor = registry.fixed_strategy_executor
    monkeypatch.setattr(registry, "numbered_strategy", lambda selected: release if selected == own_number else actual_release(selected))
    monkeypatch.setattr(registry, "fixed_strategy_executor", lambda strategy_id, revision: registration
                        if (strategy_id, revision) == (identity.strategy_id, own_number)
                        else actual_executor(strategy_id, revision))
    manifest = {**deepcopy(inherited["policies"]),
                "contract": release.canonical_payload(), "approved_digest": release.approved_digest,
                "approved_code_commit": "b" * 40, "approved_code_fingerprint": "c" * 64,
                "approval_reference": "unregistered-source-fixture-only", "publication_mode": "backtest_only",
                "source_revision_id": spec.parent.revision_id, "source_payload_hash": spec.parent.payload_hash,
                "native_fixed_capabilities": spec.payload()}
    reseal(manifest)
    strategy = {"strategy_number": own_number, "strategy_id": identity.strategy_id,
                "revision": own_number, "execution_interval": "100ms", "numbered_release": manifest}
    client.queries.clear()
    return spec, strategy, client, parent, release, registration


def reseal(manifest):
    manifest["manifest_hash"] = sha256(canonical_json({key: value for key, value in manifest.items()
                                                     if key != "manifest_hash"}).encode()).hexdigest()


def test_pure_roundtrip_preserves_every_parent_policy_source_and_economic_reference(prepared):
    spec, _, _, parent, _, _ = prepared
    payload = spec.payload()
    assert module.parse_native_fixed_capabilities(payload, expected_parent=spec) == spec
    assert payload["parent"]["payload_hash"] == parent.payload_hash
    assert payload["inherited"]["configuration_payload_hash"] == parent.payload_hash
    assert payload["inherited"]["policies"]["session_policy"] == parent.payload["strategy"]["numbered_release"]["session_policy"]
    assert payload["inherited"]["policies"]["early_original_risk_failure_policy"] == parent.payload["strategy"]["numbered_release"]["early_original_risk_failure_policy"]
    assert payload["inherited"]["flags"]["allows_adds"] is False
    assert payload["inherited"]["optional_policies"]["drawdown_measure_policy"] is None
    with pytest.raises(FrozenInstanceError):
        spec.inherited_json = "foreign"
    payload["inherited"]["flags"]["allows_adds"] = True
    assert spec.payload()["inherited"]["flags"]["allows_adds"] is False


def test_actual_factory_reads_typed_parent_and_preserves_detached_context(prepared):
    spec, strategy, client, parent, _, _ = prepared
    result = module.installed_declared_native_fixed_capabilities(strategy, configuration_client=client)
    assert type(result) is module.InstalledNativeFixedCapabilityAuthority
    assert result.capabilities == spec
    assert result.capabilities.identity.strategy_number != parent.strategy_number
    assert result.parent_configuration() == parent.payload
    assert len(result.parent_source_certificate) == 64
    assert len(client.queries) == 2 and all(query.startswith("SELECT ") for query in client.queries)
    copy = result.parent_configuration()
    copy["strategy"]["parameters"]["execution"]["tick_size"] = 999
    assert result.parent_configuration() == parent.payload


@pytest.mark.parametrize("mutation", (
    "missing", "unknown", "schema-bool", "schema-future", "number-bool", "revision-float",
    "strategy-id-bool", "interval-foreign", "parent-hash", "parent-revision", "parent-number-bool",
    "parent-token", "duplicate-input", "duplicate-rule", "foreign-input", "unknown-policy",
    "changed-policy", "missing-flag", "numeric-flag", "recovery-foreign", "source-clock",
    "configuration-hash", "reserved-policy", "nonfinite", "foreign-object",
))
def test_strict_parser_rejects_missing_unknown_foreign_types_and_inherited_drift(prepared, mutation):
    spec = prepared[0]
    value = spec.payload()
    inherited = value["inherited"]
    if mutation == "missing": del value["parent"]
    elif mutation == "unknown": value["private_override"] = {}
    elif mutation == "schema-bool": value["schema_version"] = True
    elif mutation == "schema-future": value["schema_version"] = 2
    elif mutation == "number-bool": value["identity"]["strategy_number"] = True
    elif mutation == "revision-float": value["identity"]["revision"] = 9001.0
    elif mutation == "strategy-id-bool": value["identity"]["strategy_id"] = True
    elif mutation == "interval-foreign": value["identity"]["execution_interval"] = "events"
    elif mutation == "parent-hash": value["parent"]["payload_hash"] = "f" * 64
    elif mutation == "parent-revision": value["parent"]["revision_id"] = "foreign"
    elif mutation == "parent-number-bool": value["parent"]["strategy_number"] = True
    elif mutation == "parent-token": value["parent"]["configuration_token"] = "f" * 64
    elif mutation == "duplicate-input": inherited["input_contracts"].append(inherited["input_contracts"][0])
    elif mutation == "duplicate-rule": inherited["rule_set_contracts"].append(inherited["rule_set_contracts"][0])
    elif mutation == "foreign-input": inherited["input_contracts"].append("private-producer@999")
    elif mutation == "unknown-policy": inherited["policies"]["private-exit"] = {}
    elif mutation == "changed-policy": inherited["policies"]["session_policy"]["timezone"] = "UTC"
    elif mutation == "missing-flag": del inherited["flags"]["allows_adds"]
    elif mutation == "numeric-flag": inherited["flags"]["allows_adds"] = 0
    elif mutation == "recovery-foreign": inherited["source_binding"]["intent_recovery_contract"] = "private-recovery@1"
    elif mutation == "source-clock": inherited["source_binding"]["execution_interval"] = "200ms"
    elif mutation == "configuration-hash": inherited["configuration_payload_hash"] = "f" * 64
    elif mutation == "reserved-policy": inherited["policies"]["native_fixed_capabilities"] = {}
    elif mutation == "nonfinite": inherited["policies"]["private"] = float("nan")
    else: inherited["policies"]["private"] = object()
    with pytest.raises(ValueError):
        module.parse_native_fixed_capabilities(value, expected_parent=spec)


@pytest.mark.parametrize("mutation", ("identity", "identity-int", "payload", "inherited-policy", "manifest-seal", "missing-manifest", "foreign-parent", "foreign-token"))
def test_factory_rejects_resealed_caller_drift_before_any_parent_read(prepared, mutation):
    _, strategy, client, _, _, _ = prepared
    value = deepcopy(strategy)
    manifest = value["numbered_release"]
    if mutation == "identity": value["strategy_id"] = False
    elif mutation == "identity-int": value["strategy_id"] = 123
    elif mutation == "payload": manifest["native_fixed_capabilities"]["inherited"]["flags"]["allows_adds"] = True
    elif mutation == "inherited-policy": manifest["session_policy"]["timezone"] = "UTC"
    elif mutation == "missing-manifest": del manifest["approval_reference"]
    elif mutation == "foreign-parent": manifest["source_payload_hash"] = "f" * 64
    elif mutation == "foreign-token": manifest["native_fixed_capabilities"]["parent"]["configuration_token"] = "f" * 64
    if mutation != "manifest-seal": reseal(manifest)
    else: manifest["manifest_hash"] = "f" * 64
    with pytest.raises(ValueError):
        module.installed_declared_native_fixed_capabilities(value, configuration_client=client)
    assert not client.queries


@pytest.mark.parametrize("contracts", ("missing-input", "missing-rule", "foreign-input", "foreign-rule", "duplicate-input", "duplicate-rule", "removed-parent-rule"))
def test_factory_rejects_resealed_release_declaration_drift(prepared, monkeypatch, contracts):
    spec, strategy, client, _, release, _ = prepared
    inputs, rules = release.input_contracts, release.rule_set_contracts
    if contracts == "missing-input": inputs = inputs[:-1]
    elif contracts == "missing-rule": rules = rules[:-1]
    elif contracts == "foreign-input": inputs = inputs[:-1] + ("declared-native-fixed-capabilities-input@99",)
    elif contracts == "foreign-rule": rules = rules[:-1] + ("declared-native-fixed-capabilities-rule@99",)
    elif contracts == "duplicate-input": inputs += (inputs[-1],)
    elif contracts == "duplicate-rule": rules += (rules[-1],)
    else: rules = rules[1:]
    draft = replace(release, input_contracts=inputs, rule_set_contracts=rules, approved_digest="")
    changed = replace(draft, approved_digest=draft.digest())
    monkeypatch.setattr(registry, "numbered_strategy", lambda number: changed)
    with pytest.raises(ValueError):
        module.installed_declared_native_fixed_capabilities(strategy, configuration_client=client)
    assert not client.queries


def test_factory_independently_rejects_typed_parent_economic_mutation(prepared):
    _, strategy, client, parent, _, _ = prepared
    client.payloads[parent.strategy_number]["strategy"]["parameters"]["execution"]["tick_size"] = .02
    # Reader regenerates valid node/hash rows; exact parent derivation must fail.
    with pytest.raises(RuntimeError, match="certified inheritance"):
        module.installed_declared_native_fixed_capabilities(strategy, configuration_client=client)
    assert len(client.queries) == 2


def test_missing_reader_cannot_activate_and_legacy_claim_is_rejected(prepared):
    _, strategy, _, parent, _, _ = prepared
    with pytest.raises(ValueError, match="read authority"):
        module.installed_declared_native_fixed_capabilities(strategy)
    assert module.installed_declared_native_fixed_capabilities(parent.payload["strategy"]) is None
    legacy = deepcopy(parent.payload["strategy"])
    legacy["numbered_release"]["native_fixed_capabilities"] = strategy["numbered_release"]["native_fixed_capabilities"]
    with pytest.raises(ValueError, match="Legacy release"):
        module.installed_declared_native_fixed_capabilities(legacy)


def test_parent_preparation_rejects_arbitrary_certified_claim(prepared):
    spec, _, _, parent, _, _ = prepared
    for changed in (replace(parent, payload_hash="f" * 64), replace(parent, node_hash="f" * 64)):
        with pytest.raises(ValueError, match="normalized seal"):
            module.capability_declaration_for_parent(spec.identity, changed)


def test_pure_schema_rejects_internally_inconsistent_parent_hash_without_authority_claim(prepared):
    value = prepared[0].payload()
    value["inherited"]["configuration_payload_hash"] = "f" * 64
    with pytest.raises(ValueError, match="hashes disagree"):
        module.parse_native_fixed_capabilities(value)


@pytest.mark.parametrize("claims", (False, True))
def test_original_unnumbered_release_is_outside_restricted_factory_surface(claims):
    from test_strategy_two_configuration import ONE
    baseline = deepcopy(ONE["strategy"])
    if claims:
        baseline["numbered_release"] = {"native_fixed_capabilities": {}}
    # No number-specific fallback exists: absence of installed numbered
    # authority rejects, preserving the baseline's separate existing route.
    with pytest.raises(ValueError, match="not published"):
        module.installed_declared_native_fixed_capabilities(baseline)
