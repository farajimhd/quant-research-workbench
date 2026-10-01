"""Exact parent preservation and immutable profit-policy configuration."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from pipelines.strategy_one.strategy_thirty_configuration import compile_strategy_thirty_configuration
from pipelines.strategy_one.strategy_thirty_one_configuration import compile_strategy_thirty_one_configuration
from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration, is_numbered_fixed_configuration,
)
from src.trading_runtime.strategy_thirty_one_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, PROFIT_PROTECTION_POLICY,
    INHERITED_POLICIES, verify_strategy_thirty_one_manifest,
)
from src.trading_runtime.journal_contract import canonical_json
from test_strategy_thirty_configuration import parent as thirty_parent

APPROVAL = dict(approved_code_commit='d'*40, approved_code_fingerprint='e'*64,
                approval_reference='test-only-profit-protection')


def parent():
    result = compile_strategy_thirty_configuration(thirty_parent(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH, result['node_hash'], result['source_candidate_id'],
        result['source_candidate_hash'], 'test-only', result['payload'])


def test_exact_parent_inheritance_and_normalized_publication_envelope():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_strategy_thirty_one_configuration(source, **APPROVAL)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    assert source.payload == before
    payload = result['payload']
    assert is_numbered_fixed_configuration(payload)
    assert payload['strategy']['parameters'] == before['strategy']['parameters']
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[name] == before[name]
    manifest = verify_strategy_thirty_one_manifest(payload['strategy'])
    assert manifest['profit_protection_policy'] == PROFIT_PROTECTION_POLICY
    for name in INHERITED_POLICIES:
        assert manifest[name] == before['strategy']['numbered_release'][name]
    assert result['source_candidate_id'] == f'strategy-thirty-one-from:{PARENT_REVISION_ID}'
    assert payload['strategy_profile']['lifecycle']['trading_behavior']['eligible_sessions'] == ['premarket', 'afterhours']


@pytest.mark.parametrize('field,value', [('payload_hash', 'f'*64),
                                       ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_foreign_parent_rejected(field, value):
    with pytest.raises(ValueError, match='exact pinned certified Strategy 30'):
        compile_strategy_thirty_one_configuration(replace(parent(), **{field: value}), **APPROVAL)


@pytest.mark.parametrize('policy', [*INHERITED_POLICIES, 'profit_protection_policy'])
def test_changed_policy_rejected_even_with_recomputed_manifest_seal(policy):
    strategy = compile_strategy_thirty_one_configuration(parent(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'unapproved': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
        if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        verify_strategy_thirty_one_manifest(strategy)


@pytest.mark.parametrize('field,value', [('approved_code_commit', 'x'*40),
    ('approved_code_fingerprint', 'short'), ('approval_reference', ''), ('approval_reference', ' '*3)])
def test_invalid_code_approval_rejected(field, value):
    with pytest.raises(ValueError):
        compile_strategy_thirty_one_configuration(parent(), **{**APPROVAL, field: value})
