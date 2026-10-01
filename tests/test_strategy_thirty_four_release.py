"""Immutable declaration and installed selection checks; no connected publication."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import strategy_thirty_three_release as parent
from src.trading_runtime import strategy_thirty_four_release as child


def test_declaration_preserves_parent_inputs_and_policies_and_adds_only_exit_contract():
    previous = parent.release_contract()
    declared = child.release_contract()
    declared.verify()
    assert declared.number == declared.executor_revision == 34
    assert declared.executor_strategy_id == previous.executor_strategy_id
    assert declared.input_contracts == previous.input_contracts
    assert declared.evaluation_interval == previous.evaluation_interval
    assert declared.rule_set_contracts[:-1] == previous.rule_set_contracts
    assert declared.rule_set_contracts[-1] == child.CONFIRMED_AH_FAILURE_POLICY['policy_id']
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.PROFIT_PROTECTION_POLICY == parent.PROFIT_PROTECTION_POLICY
    assert child.release_contract() == declared


def test_exact_parent_identity_and_independent_policy_copy():
    assert child.PARENT_REVISION_ID == 'strategy-one-33:66f5cdae-3cbb-4fdd-af99-a72d22e77e6c'
    assert child.PARENT_PAYLOAD_HASH == '1dcf2e4d52de04e710880fc1be221ae68fafb4ed5a441691edd183e8c092815b'
    assert child.INHERITED_POLICIES is not parent.INHERITED_POLICIES
    assert child.PROFIT_PROTECTION_POLICY is not parent.PROFIT_PROTECTION_POLICY
    copied = deepcopy(child.PROFIT_PROTECTION_POLICY)
    copied['cutoff_admission_authority'] = 'foreign'
    assert parent.PROFIT_PROTECTION_POLICY['cutoff_admission_authority'] != 'foreign'


def test_altered_declaration_invalidates_approval_seal():
    with pytest.raises(ValueError):
        replace(child.release_contract(), behavior_specification='changed').verify()


def typed_parent_fixture():
    """Published identity on a typed fixture; no database certification claim."""
    from test_strategy_thirty_three_configuration import parent as source, APPROVAL
    from pipelines.strategy_one.strategy_thirty_three_configuration import compile_strategy_thirty_three_configuration
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    result = compile_strategy_thirty_three_configuration(source(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'],
    )


def test_compiler_preserves_parent_trading_data_and_does_not_mutate_source():
    from test_strategy_thirty_three_configuration import APPROVAL
    from pipelines.strategy_one.strategy_thirty_four_configuration import compile_strategy_thirty_four_configuration
    source = typed_parent_fixture()
    before = deepcopy(source.payload)
    result = compile_strategy_thirty_four_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[name] == before[name]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identity:
        assert payload['strategy'][name] == before['strategy'][name]
    child.verify_strategy_thirty_four_manifest(payload['strategy'])
    assert result['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH


def test_native_configuration_selection_and_envelope_use_installed34(monkeypatch):
    from test_strategy_thirty_three_configuration import APPROVAL
    from src.backend.backtest_strategy_one_configuration import (
        is_numbered_fixed_configuration, selected_numbered_revision,
    )
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    result = child.derive_strategy_thirty_four_configuration(typed_parent_fixture(), **APPROVAL)
    assert is_numbered_fixed_configuration(result['payload'])
    payload, nodes = _verified_numbered_envelope(result)
    assert nodes and payload == result['payload']
    assert child.verify_installed_strategy_thirty_four_release(
        result['payload']['strategy']['numbered_release']) == child.release_contract()
    from src.backend import backtest_strategy_one_configuration as configurations
    selected = replace(typed_parent_fixture(),
        attempt_id='00000000-0000-0000-0000-000000000034', payload=payload)
    calls = []
    def certified_fixture(client, number):
        calls.append(number)
        return selected
    monkeypatch.setattr(configurations, 'certify_numbered_configuration', certified_fixture)
    revision = selected_numbered_revision(client=object(),
        revision_id='strategy-one-34:00000000-0000-0000-0000-000000000034')
    assert revision == selected.revision() and calls == [34]


@pytest.mark.parametrize('policy', [*child.INHERITED_POLICIES, 'profit_protection_policy', 'confirmed_ah_failure_policy'])
def test_resealed_policy_mutation_rejected(policy):
    from hashlib import sha256
    from test_strategy_thirty_three_configuration import APPROVAL
    from src.trading_runtime.journal_contract import canonical_json
    strategy = child.derive_strategy_thirty_four_configuration(typed_parent_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'foreign': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_strategy_thirty_four_manifest(strategy)


def test_compiler_rejects_foreign_parent_hash():
    from test_strategy_thirty_three_configuration import APPROVAL
    with pytest.raises(ValueError, match='exact pinned certified Strategy 33'):
        child.derive_strategy_thirty_four_configuration(replace(typed_parent_fixture(), payload_hash='f'*64), **APPROVAL)
