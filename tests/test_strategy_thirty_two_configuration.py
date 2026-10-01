"""Exact published parent and unchanged trading semantics for the reader repair."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from pipelines.strategy_one.strategy_thirty_one_configuration import compile_strategy_thirty_one_configuration
from pipelines.strategy_one.strategy_thirty_two_configuration import compile_strategy_thirty_two_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration, is_numbered_fixed_configuration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_thirty_two_release import (
    INHERITED_POLICIES, PARENT_PAYLOAD_HASH, PARENT_REVISION_ID,
    PROFIT_PROTECTION_POLICY, release_contract, verify_strategy_thirty_two_manifest,
)
from src.trading_runtime.strategy_thirty_one_release import release_contract as parent_release_contract
from test_strategy_thirty_one_configuration import APPROVAL, parent as thirty_parent


def parent():
    result = compile_strategy_thirty_one_configuration(thirty_parent(), **APPROVAL)
    # This is a typed fixture with the published identity, not a database readback.
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH, result['node_hash'], result['source_candidate_id'],
        result['source_candidate_hash'], 'test-only', result['payload'])


def test_exact_parent_and_normalized_envelope_preserve_trading_content():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_strategy_thirty_two_configuration(source, **APPROVAL)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    assert source.payload == before
    payload = result['payload']
    assert is_numbered_fixed_configuration(payload)
    assert payload['strategy']['parameters'] == before['strategy']['parameters']
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[name] == before[name]
    changed_identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - changed_identity:
        assert payload['strategy'][name] == before['strategy'][name]
    manifest = verify_strategy_thirty_two_manifest(payload['strategy'])
    for name in INHERITED_POLICIES:
        assert manifest[name] == before['strategy']['numbered_release'][name]
    old_profit = before['strategy']['numbered_release']['profit_protection_policy']
    assert {k: v for k, v in manifest['profit_protection_policy'].items()
        if k != 'confirmation_read_authority'} == old_profit
    assert manifest['profit_protection_policy'] == PROFIT_PROTECTION_POLICY
    assert manifest['source_revision_id'] == PARENT_REVISION_ID
    assert manifest['source_payload_hash'] == PARENT_PAYLOAD_HASH
    assert result['source_candidate_id'] == f'strategy-thirty-two-from:{PARENT_REVISION_ID}'
    assert payload['strategy_profile']['lifecycle']['trading_behavior']['eligible_sessions'] == ['premarket', 'afterhours']


@pytest.mark.parametrize('field,value', [('payload_hash', 'f'*64),
    ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_foreign_parent_rejected(field, value):
    with pytest.raises(ValueError, match='exact pinned certified Strategy 31'):
        compile_strategy_thirty_two_configuration(replace(parent(), **{field: value}), **APPROVAL)


@pytest.mark.parametrize('policy', [*INHERITED_POLICIES, 'profit_protection_policy'])
def test_resealed_policy_mutation_rejected(policy):
    strategy = compile_strategy_thirty_two_configuration(parent(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'unapproved': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
        if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        verify_strategy_thirty_two_manifest(strategy)


def test_resealed_legacy_confirmation_reader_rejected():
    strategy = compile_strategy_thirty_two_configuration(parent(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest['profit_protection_policy']['confirmation_read_authority'] = 'trading_journal_writer'
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
        if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        verify_strategy_thirty_two_manifest(strategy)


def test_registry_preserves_parent_capabilities_and_blocks_unintegrated_execution():
    from src.trading_runtime.strategy_registry import fixed_strategy_executor, numbered_strategy_parent
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    release, old = release_contract(), parent_release_contract()
    assert release.number == release.executor_revision == 32
    assert release.input_contracts == old.input_contracts
    assert release.rule_set_contracts == old.rule_set_contracts
    assert release.evaluation_interval == old.evaluation_interval == '100ms'
    release.verify()
    contract = fixed_strategy_executor(release.executor_strategy_id, 32).contract_factory()
    previous = numbered_fixed_strategy(31)
    assert contract.strategy_number == 32 and numbered_strategy_parent(32) == 31
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                 'allows_target_escalation', 'caps_entry_at_reference_ask', 'allows_followthrough_failure_exit'):
        assert getattr(contract, name) == getattr(previous, name)
    for boundary in (0, 100, 19500000, 19740000, 19800000, 43200000, 43200100,
                     57000000, 57300000, 57600000):
        for name in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
            assert getattr(contract, name)(boundary) == getattr(previous, name)(boundary)
        assert contract.activation_allowed(boundary, boundary) == previous.activation_allowed(boundary, boundary)
    assert numbered_session_exit_reason(32) == 'strategy_thirty_two_session_exit'
    with pytest.raises(ValueError, match='native profit-route integration is not yet certified'):
        certify_numbered_fixed_v4_projection(32)
    with pytest.raises(ValueError):
        numbered_fixed_strategy(33)
