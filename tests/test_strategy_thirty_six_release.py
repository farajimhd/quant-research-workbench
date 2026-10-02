from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from src.trading_runtime import strategy_thirty_five_release as parent
from src.trading_runtime import strategy_thirty_six_release as child
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_registry import numbered_strategy
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from test_strategy_thirty_five_release import source_fixture as parent_source
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    result = parent.derive_strategy_thirty_five_configuration(parent_source(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def test_prepared_contract_inherits_every_parent_rule_and_adds_one_entry_rule():
    previous, release = parent.release_contract(), child.release_contract()
    release.verify()
    assert release.number == release.executor_revision == 36
    assert release.executor_strategy_id == previous.executor_strategy_id
    assert release.input_contracts == previous.input_contracts
    assert release.evaluation_interval == previous.evaluation_interval
    assert release.rule_set_contracts[:-1] == previous.rule_set_contracts
    assert release.rule_set_contracts[-1] == child.ENTRY_ACTIVITY_POLICY['policy_id']
    for name in parent.INHERITED_POLICIES:
        assert child.INHERITED_POLICIES[name] == parent.INHERITED_POLICIES[name]
    assert child.INHERITED_POLICIES['liquidity_fade_policy'] == parent.LIQUIDITY_FADE_POLICY
    assert child.INHERITED_POLICIES['profit_protection_policy'] == parent.PROFIT_PROTECTION_POLICY
    assert child.INHERITED_POLICIES['confirmed_ah_failure_policy'] == parent.CONFIRMED_AH_FAILURE_POLICY
    with pytest.raises(ValueError):
        replace(release, behavior_specification='foreign').verify()


def test_exact_parent_derivation_preserves_all_trading_configuration():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_thirty_six_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[name] == before[name]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identity:
        assert payload['strategy'][name] == before['strategy'][name]
    assert child.verify_prepared_strategy_thirty_six_manifest(payload['strategy'])
    assert result['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH
    assert result['payload_hash'] == sha256(canonical_json(payload).encode()).hexdigest()


@pytest.mark.parametrize('policy', [*child.INHERITED_POLICIES, 'entry_activity_policy'])
def test_resealed_policy_changes_cannot_change_prepared_approval(policy):
    strategy = child.derive_strategy_thirty_six_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'foreign': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                     if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_thirty_six_manifest(strategy)


@pytest.mark.parametrize('changes', [
    {'payload_hash': 'f' * 64}, {'attempt_id': '00000000-0000-0000-0000-000000000001'},
])
def test_foreign_parent_identity_rejects(changes):
    with pytest.raises(ValueError, match='exact pinned certified Strategy35'):
        child.derive_strategy_thirty_six_configuration(replace(source_fixture(), **changes), **APPROVAL)


def test_installed_release_matches_prepared_exact_parent_declaration():
    strategy = child.derive_strategy_thirty_six_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    assert child.verify_prepared_strategy_thirty_six_manifest(strategy)
    assert numbered_strategy(36) == child.release_contract()
    assert child.verify_strategy_thirty_six_manifest(strategy)
    with pytest.raises(ValueError):
        numbered_strategy(38)
