"""Prepared native graph tests, never a database certificate or source approval."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import pytest

from test_strategy_thirty_three_configuration import APPROVAL
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_one_hundred_fifteen_configuration as child
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import decode_nodes, encode_nodes, node_hash


def source_fixture():
    # Prepared graph only; this fixture does not attest native database publication.
    from test_strategy114_configuration import source_fixture as source113
    from src.trading_runtime import strategy_one_hundred_fourteen_configuration as parent_configuration
    result = parent_configuration.derive_strategy_one_hundred_fourteen_configuration(
        source113(), **{**APPROVAL, 'approved_code_commit': child.CONTROL_CODE_COMMIT,
            'approved_code_fingerprint': child.CONTROL_CODE_FINGERPRINT})
    return CertifiedStrategyOneConfiguration(child.CONTROL_REVISION.split(':')[1],
        child.CONTROL_PAYLOAD_HASH, result['node_hash'], result['source_candidate_id'],
        result['source_candidate_hash'], 'prepared-test-only', result['payload'])


def reseal(manifest):
    manifest['manifest_hash'] = sha256(canonical_json(
        {k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()


def test_native_tree_roundtrip_preserves_control_economics_and_lifecycle():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_one_hundred_fifteen_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[key] == before[key]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for key in before['strategy'].keys() - identity:
        assert payload['strategy'][key] == before['strategy'][key]
    assert payload['strategy_profile']['lifecycle'] == before['strategy_profile']['lifecycle']
    rows = encode_nodes(json.loads(canonical_json(payload)))
    assert len(rows) == result['node_count']
    assert node_hash(rows) == result['node_hash']
    decoded = decode_nodes(rows)
    assert canonical_json(decoded) == canonical_json(payload)
    assert child.verify_prepared_strategy_one_hundred_fifteen_manifest(decoded['strategy'])
    assert result['payload_hash'] == sha256(canonical_json(payload).encode()).hexdigest()


@pytest.mark.parametrize('field,bad', [
    ('attempt_id', '00000000-0000-0000-0000-000000000001'), ('payload_hash', 'a'*64)])
def test_foreign_control_identity_rejected(field, bad):
    with pytest.raises(ValueError):
        child.derive_strategy_one_hundred_fifteen_configuration(
            replace(source_fixture(), **{field: bad}), **APPROVAL)


@pytest.mark.parametrize('field,bad', [
    ('approved_code_commit', 'a'*40), ('approved_code_fingerprint', 'a'*64)])
def test_resealed_foreign_parent_source_rejected(field, bad):
    source = source_fixture()
    source.payload['strategy']['numbered_release'][field] = bad
    reseal(source.payload['strategy']['numbered_release'])
    with pytest.raises(ValueError, match='parent source approval'):
        child.derive_strategy_one_hundred_fifteen_configuration(source, **APPROVAL)


@pytest.mark.parametrize('field,bad', [
    ('source_revision_id', 'foreign'), ('source_payload_hash', 'a'*64),
    ('publication_mode', 'live'), ('approved_code_commit', 'invalid'),
    ('approved_code_fingerprint', 'invalid'), ('approval_reference', ' '),
    ('approved_digest', 'b'*64)])
def test_resealed_foreign_manifest_authority_rejected(field, bad):
    strategy = child.derive_strategy_one_hundred_fifteen_configuration(
        source_fixture(), **APPROVAL)['payload']['strategy']
    strategy['numbered_release'][field] = bad
    reseal(strategy['numbered_release'])
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_one_hundred_fifteen_manifest(strategy)


@pytest.mark.parametrize('key', tuple(child.manifest_policies()))
def test_each_inherited_or_new_policy_remains_exact_even_after_resealing(key):
    strategy = child.derive_strategy_one_hundred_fifteen_configuration(
        source_fixture(), **APPROVAL)['payload']['strategy']
    strategy['numbered_release'][key]['foreign'] = True
    reseal(strategy['numbered_release'])
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_one_hundred_fifteen_manifest(strategy)


def test_boolean_numeric_alias_and_mutable_assignment_fail_closed():
    strategy = child.derive_strategy_one_hundred_fifteen_configuration(
        source_fixture(), **APPROVAL)['payload']['strategy']
    strategy['numbered_release']['consecutive_price_confirmed_original_risk_policy']['price_policy']['premarket_fraction'] = [True, 2]
    reseal(strategy['numbered_release'])
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_one_hundred_fifteen_manifest(strategy)
    source = source_fixture()
    source.payload['assignments'] = [{'foreign': True}]
    with pytest.raises(ValueError, match='mutable assignments'):
        child.derive_strategy_one_hundred_fifteen_configuration(source, **APPROVAL)
