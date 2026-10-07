"""Exact42 inheritance and adversarial prepared manifest checks, no DB admission."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from test_strategy_fifty_release import APPROVAL
from test_strategy_forty_two_release import source_fixture as prepared41_fixture
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_sixty_eight_release as child
from src.trading_runtime import strategy_forty_two_release as parent
from src.trading_runtime.journal_contract import canonical_json


def source_fixture():
    """Prepared test graph with the real parent approval, never a DB certificate."""
    approval = {**APPROVAL, 'approved_code_commit': child.PARENT_CODE_COMMIT,
                'approved_code_fingerprint': child.PARENT_CODE_FINGERPRINT}
    result = parent.derive_strategy_forty_two_configuration(prepared41_fixture(), **approval)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'prepared-test-only', result['payload'])


def test_single_rule_preserves_parent_economics_and_configuration():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_sixty_eight_configuration(source, **APPROVAL)
    assert source.payload == before
    previous, current = parent.release_contract(), child.release_contract()
    current.verify()
    assert current.rule_set_contracts == previous.rule_set_contracts + (child.CONFIRMED_ORIGINAL_RISK_POLICY.policy_id,)
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.HALF_RISK_LIQUIDITY_POLICY == parent.HALF_RISK_LIQUIDITY_POLICY
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][key] == before[key]
    identities = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for key in before['strategy'].keys() - identities:
        assert result['payload']['strategy'][key] == before['strategy'][key]
    assert 'early_original_risk_failure_policy' not in result['payload']['strategy']['numbered_release']
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert child.verify_prepared_strategy_sixty_eight_manifest(result['payload']['strategy'])


def test_actual_compiler_and_publication_envelope_preserve_complete42_payload():
    from pipelines.strategy_one.strategy_sixty_eight_configuration import compile_strategy_sixty_eight_configuration
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    source = source_fixture()
    before = deepcopy(source.payload)
    result = compile_strategy_sixty_eight_configuration(source,**APPROVAL)
    payload,_ = _verified_numbered_envelope(result)
    assert payload == result['payload']
    for key in before.keys() - {'strategy','strategy_profile','run_plan'}:
        assert payload[key] == before[key]
    identities = {'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for key in before['strategy'].keys() - identities:
        assert payload['strategy'][key] == before['strategy'][key]
    assert source.payload == before


def test_actual_publication_recomputes66_before_collision_and_never_writes(monkeypatch):
    """External certified-parent/catalog reads are synthetic; publisher/compiler are real."""
    from pipelines.strategy_one import configuration_publisher as publisher
    from pipelines.strategy_one import strategy_sixty_eight_configuration as compiler
    source = source_fixture()
    envelope = compiler.compile_strategy_sixty_eight_configuration(source, **APPROVAL)
    called = []
    actual = compiler.compile_strategy_sixty_eight_configuration
    def recorded(parent_source, **approval):
        called.append(parent_source)
        return actual(parent_source, **approval)
    monkeypatch.setattr(compiler, 'compile_strategy_sixty_eight_configuration', recorded)
    monkeypatch.setattr(publisher, 'certify_numbered_configuration',
                        lambda client, number: source if number == 42 else pytest.fail('foreign source'))
    monkeypatch.setattr(publisher, 'verify_tables', lambda client: None)
    monkeypatch.setattr(publisher, '_rows', lambda client, sql: [
        {'release_attempt_id': 'existing', 'payload_hash': 'a'*64}])
    monkeypatch.setattr(publisher, '_insert_rows', lambda *args: pytest.fail('collision wrote'))
    class Keeper:
        connected = True
        def create(self, *args, **kwargs): pass
        def delete(self, *args, **kwargs): pass
    with pytest.raises(publisher.PublicationStageError, match='^existing_release:') as failure:
        publisher.publish_configuration(object(), Keeper(), envelope)
    assert type(failure.value.__cause__) is RuntimeError
    assert 'different immutable release' in str(failure.value.__cause__)
    assert called == [source]


def test_actual_publication_rejects_resealed_economics_before_catalog_or_writes(monkeypatch):
    from pipelines.strategy_one import configuration_publisher as publisher
    from pipelines.strategy_one.strategy_sixty_eight_configuration import compile_strategy_sixty_eight_configuration
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
    source = source_fixture()
    envelope = compile_strategy_sixty_eight_configuration(source, **APPROVAL)
    envelope['payload']['cash'] = {'foreign': 1}
    nodes = encode_nodes(envelope['payload'])
    envelope.update(payload_hash=sha256(canonical_json(envelope['payload']).encode()).hexdigest(),
                    node_count=len(nodes), node_hash=node_hash(nodes))
    monkeypatch.setattr(publisher, 'certify_numbered_configuration', lambda *args: source)
    monkeypatch.setattr(publisher, 'verify_tables', lambda *args: pytest.fail('invalid inheritance reached catalog'))
    monkeypatch.setattr(publisher, '_insert_rows', lambda *args: pytest.fail('invalid inheritance wrote'))
    with pytest.raises(ValueError):
        publisher.publish_configuration(object(), object(), envelope)


@pytest.mark.parametrize('field,value', [
    ('source_payload_hash', 'a'*64), ('publication_mode', 'live'),
    ('approved_code_commit', 'bad'), ('approved_code_fingerprint', 'bad'),
])
def test_resealed_authority_mutations_rejected(field, value):
    strategy = child.derive_strategy_sixty_eight_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[field] = value
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_sixty_eight_manifest(strategy)


@pytest.mark.parametrize('policy', ['confirmed_original_risk_policy', 'half_risk_liquidity_policy'])
def test_resealed_policy_mutations_rejected(policy):
    strategy = child.derive_strategy_sixty_eight_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy]['foreign'] = True
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_sixty_eight_manifest(strategy)


@pytest.mark.parametrize('field,value', [('payload_hash', 'a'*64), ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_foreign_parent_rejected(field, value):
    with pytest.raises(ValueError):
        child.derive_strategy_sixty_eight_configuration(replace(source_fixture(), **{field:value}), **APPROVAL)


@pytest.mark.parametrize('field,value', [('approved_code_commit', 'a'*40), ('approved_code_fingerprint', 'a'*64)])
def test_resealed_parent_code_approval_rejected(field, value):
    source = source_fixture()
    payload = deepcopy(source.payload)
    manifest = payload['strategy']['numbered_release']
    manifest[field] = value
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='parent source approval'):
        child.derive_strategy_sixty_eight_configuration(replace(source, payload=payload), **APPROVAL)
