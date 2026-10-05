"""Prepared Strategy52 preserves its exact parent except declared AH extension."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_fifty_release as parent
from src.trading_runtime import strategy_fifty_two_release as child
from src.trading_runtime.journal_contract import canonical_json
from test_strategy_fifty_release import source_fixture as strategy42_fixture
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    result = parent.derive_strategy_fifty_configuration(strategy42_fixture(), **APPROVAL)
    result['payload']['strategy']['numbered_release']['approved_code_fingerprint'] = child.PARENT_CODE_FINGERPRINT
    reseal(result['payload']['strategy']['numbered_release'])
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def reseal(manifest):
    manifest['manifest_hash'] = sha256(canonical_json(
        {k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()


def test_prepared_release_preserves_all_parent_behavior_and_inputs():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_fifty_two_configuration(source, **APPROVAL)
    assert source.payload == before
    previous, current = parent.release_contract(), child.release_contract()
    current.verify()
    assert current.number == current.executor_revision == 52
    assert current.input_contracts == previous.input_contracts
    assert current.evaluation_interval == previous.evaluation_interval
    assert current.rule_set_contracts == previous.rule_set_contracts + (child.ARMED_PROFIT_FLOOR_POLICY.policy_id,)
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.HALF_RISK_LIQUIDITY_POLICY == parent.HALF_RISK_LIQUIDITY_POLICY
    assert child.EARLY_FAILURE_POLICY.premarket_fraction is None
    assert child.EARLY_FAILURE_POLICY.afterhours_fraction == (1, 4)
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][name] == before[name]
    identities = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identities:
        assert result['payload']['strategy'][name] == before['strategy'][name]
    for name, value in before['strategy']['numbered_release'].items():
        if name in child.INHERITED_POLICIES or name == 'half_risk_liquidity_policy':
            assert result['payload']['strategy']['numbered_release'][name] == value
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert result['source_candidate_id'] == 'strategy-fifty-two-from:' + child.PARENT_REVISION_ID
    assert child.verify_prepared_strategy_fifty_two_manifest(result['payload']['strategy'])


def test_prepared_verifier_roundtrips_normalized_json_without_registry_admission(monkeypatch):
    from src.trading_runtime import strategy_registry
    result = child.derive_strategy_fifty_two_configuration(source_fixture(), **APPROVAL)
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda *args: pytest.fail('prepared verifier consulted registry'))
    decoded = json.loads(canonical_json(result['payload']['strategy']))
    assert child.verify_prepared_strategy_fifty_two_manifest(decoded)


@pytest.mark.parametrize('field,value', [
    ('payload_hash', 'a' * 64), ('attempt_id', '00000000-0000-0000-0000-000000000001'),
])
def test_rejects_foreign_exact_parent(field, value):
    with pytest.raises(ValueError, match='exact pinned certified Strategy50'):
        child.derive_strategy_fifty_two_configuration(replace(source_fixture(), **{field: value}), **APPROVAL)


@pytest.mark.parametrize('policy', [
    'armed_profit_floor_policy', 'early_original_risk_failure_policy', 'half_risk_liquidity_policy',
    'episode_activity_policy', 'liquidity_fade_policy',
])
def test_resealed_policy_mutation_rejected(policy):
    strategy = deepcopy(child.derive_strategy_fifty_two_configuration(source_fixture(), **APPROVAL)['payload']['strategy'])
    manifest = strategy['numbered_release']
    manifest[policy]['unreviewed_change'] = True
    reseal(manifest)
    with pytest.raises(ValueError, match='pinned policy'):
        child.verify_prepared_strategy_fifty_two_manifest(strategy)


@pytest.mark.parametrize('field,value', [
    ('source_revision_id', 'strategy-one-43:00000000-0000-0000-0000-000000000001'),
    ('source_payload_hash', 'a' * 64), ('publication_mode', 'live'),
    ('approved_code_commit', 'bad'), ('approval_reference', ''),
])
def test_resealed_manifest_authority_mutation_rejected(field, value):
    strategy = deepcopy(child.derive_strategy_fifty_two_configuration(source_fixture(), **APPROVAL)['payload']['strategy'])
    manifest = strategy['numbered_release']
    manifest[field] = value
    reseal(manifest)
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_fifty_two_manifest(strategy)


def test_rejects_mutable_parent_assignments():
    source = source_fixture()
    payload = deepcopy(source.payload)
    payload['assignments'] = [{'unreviewed': True}]
    with pytest.raises(ValueError, match='mutable assignments'):
        child.derive_strategy_fifty_two_configuration(replace(source, payload=payload), **APPROVAL)


def test_exact_published_parent_code_fingerprint_required():
    source = source_fixture()
    source.payload['strategy']['numbered_release']['approved_code_fingerprint'] = 'b' * 64
    reseal(source.payload['strategy']['numbered_release'])
    with pytest.raises(ValueError, match='published Strategy50 source approval'):
        child.derive_strategy_fifty_two_configuration(source, **APPROVAL)


def test_compiler_and_publisher_envelope_use_exact52_and_fail_closed(monkeypatch):
    from pipelines.strategy_one.strategy_fifty_two_configuration import compile_strategy_fifty_two_configuration
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    from src.backend import backtest_fixed_v4_certification as cert
    from src.backend.backtest_strategy_fifty_two_certification import certify_strategy_fifty_two_source
    calls = []
    def block(number):
        calls.append(number)
        raise ValueError('unreviewed source blocked')
    monkeypatch.setattr(cert, 'certify_numbered_fixed_v4_projection', block)
    with pytest.raises(ValueError, match='unreviewed source'):
        compile_strategy_fifty_two_configuration(source_fixture(), **APPROVAL)
    assert calls == [52]
    envelope = child.derive_strategy_fifty_two_configuration(source_fixture(), **APPROVAL)
    assert _verified_numbered_envelope(envelope)[0] == envelope['payload']
    from src.backend import backtest_strategy_fifty_two_certification as source_cert
    assert len(certify_strategy_fifty_two_source()) == 64
    with monkeypatch.context() as unsealed:
        unsealed.setattr(source_cert, 'STRATEGY52_SOURCE_AST', {})
        with pytest.raises(ValueError, match='review is not sealed'):
            certify_strategy_fifty_two_source()
