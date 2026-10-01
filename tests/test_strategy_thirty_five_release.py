"""Prepared exact-parent derivation, preserved trading authority and closed admission."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from src.trading_runtime import strategy_thirty_four_release as parent
from src.trading_runtime import strategy_thirty_five_release as child
from src.trading_runtime.journal_contract import canonical_json
from pipelines.strategy_one.strategy_thirty_five_configuration import compile_strategy_thirty_five_configuration
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    from test_strategy_thirty_four_release import typed_parent_fixture
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    result = parent.derive_strategy_thirty_four_configuration(typed_parent_fixture(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def test_declaration_inherits_parent_inputs_and_all_existing_exits():
    previous, release = parent.release_contract(), child.release_contract()
    release.verify()
    assert release.number == release.executor_revision == 35
    assert release.executor_strategy_id == previous.executor_strategy_id
    assert release.input_contracts == previous.input_contracts
    assert release.evaluation_interval == previous.evaluation_interval
    assert release.rule_set_contracts[:-1] == previous.rule_set_contracts
    assert release.rule_set_contracts[-1] == child.LIQUIDITY_FADE_POLICY['policy_id']
    for name in ('INHERITED_POLICIES', 'PROFIT_PROTECTION_POLICY', 'CONFIRMED_AH_FAILURE_POLICY'):
        assert getattr(child, name) == getattr(parent, name)
        assert getattr(child, name) is not getattr(parent, name)
    assert child.release_contract() == release
    with pytest.raises(ValueError):
        replace(release, behavior_specification='foreign').verify()


def test_compiler_preserves_trading_data_and_source_without_granting_installation():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = compile_strategy_thirty_five_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[name] == before[name]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identity:
        assert payload['strategy'][name] == before['strategy'][name]
    assert child.verify_prepared_strategy_thirty_five_manifest(payload['strategy'])
    assert result['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH
    with pytest.raises(ValueError):
        child.verify_strategy_thirty_five_manifest(payload['strategy'])


@pytest.mark.parametrize('policy', [*child.INHERITED_POLICIES, 'profit_protection_policy',
                                  'confirmed_ah_failure_policy', 'liquidity_fade_policy'])
def test_even_resealed_policy_mutations_reject(policy):
    strategy = compile_strategy_thirty_five_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'foreign': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_thirty_five_manifest(strategy)


@pytest.mark.parametrize('changes', [{'payload_hash': 'f'*64},
                                   {'attempt_id': '00000000-0000-0000-0000-000000000001'}])
def test_foreign_parent_identity_rejects(changes):
    with pytest.raises(ValueError, match='exact pinned certified Strategy 34'):
        compile_strategy_thirty_five_configuration(replace(source_fixture(), **changes), **APPROVAL)
