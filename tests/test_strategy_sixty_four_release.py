"""Exact42 inheritance and adversarial prepared manifest checks, no DB admission."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from test_strategy_fifty_release import APPROVAL
from test_strategy_forty_two_release import source_fixture as prepared41_fixture
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_sixty_four_release as child
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
    result = child.derive_strategy_sixty_four_configuration(source, **APPROVAL)
    assert source.payload == before
    previous, current = parent.release_contract(), child.release_contract()
    current.verify()
    assert current.rule_set_contracts == previous.rule_set_contracts + (child.ENTRY_SPREAD_RISK_POLICY.policy_id,)
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.HALF_RISK_LIQUIDITY_POLICY == parent.HALF_RISK_LIQUIDITY_POLICY
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][key] == before[key]
    identities = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for key in before['strategy'].keys() - identities:
        assert result['payload']['strategy'][key] == before['strategy'][key]
    assert 'early_original_risk_failure_policy' not in result['payload']['strategy']['numbered_release']
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert child.verify_prepared_strategy_sixty_four_manifest(result['payload']['strategy'])


@pytest.mark.parametrize('field,value', [
    ('source_payload_hash', 'a'*64), ('publication_mode', 'live'),
    ('approved_code_commit', 'bad'), ('approved_code_fingerprint', 'bad'),
])
def test_resealed_authority_mutations_rejected(field, value):
    strategy = child.derive_strategy_sixty_four_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[field] = value
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_sixty_four_manifest(strategy)


@pytest.mark.parametrize('policy', ['entry_spread_risk_policy', 'half_risk_liquidity_policy', 'entry_spread_risk_quote_source'])
def test_resealed_policy_mutations_rejected(policy):
    strategy = child.derive_strategy_sixty_four_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy]['foreign'] = True
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_sixty_four_manifest(strategy)


@pytest.mark.parametrize('field,value', [('payload_hash', 'a'*64), ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_foreign_parent_rejected(field, value):
    with pytest.raises(ValueError):
        child.derive_strategy_sixty_four_configuration(replace(source_fixture(), **{field:value}), **APPROVAL)


@pytest.mark.parametrize('field,value', [('approved_code_commit', 'a'*40), ('approved_code_fingerprint', 'a'*64)])
def test_resealed_parent_code_approval_rejected(field, value):
    source = source_fixture()
    payload = deepcopy(source.payload)
    manifest = payload['strategy']['numbered_release']
    manifest[field] = value
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='parent source approval'):
        child.derive_strategy_sixty_four_configuration(replace(source, payload=payload), **APPROVAL)
