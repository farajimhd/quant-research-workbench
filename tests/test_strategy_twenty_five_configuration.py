"""Strategy25 changes only first-minute premarket quarter-risk failure."""
from copy import deepcopy
from hashlib import sha256

import pytest

from pipelines.strategy_one.strategy_twenty_four_configuration import compile_strategy_twenty_four_configuration
from pipelines.strategy_one.strategy_twenty_five_configuration import compile_strategy_twenty_five_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_twenty_five_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, FIRST_PRICE_POLICY, RULES,
    verify_strategy_twenty_five_manifest,
)
from test_strategy_twenty_four_configuration import parent as twenty_fourth_parent


def parent():
    value = compile_strategy_twenty_four_configuration(twenty_fourth_parent(),
        approved_code_commit='d' * 40, approved_code_fingerprint='e' * 64,
        approval_reference='test-only-parent')
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH, value['node_hash'], value['source_candidate_id'],
        value['source_candidate_hash'], 'test-only', value['payload'])


def test_twenty_five_preserves_exact_parent_fields_and_policies():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_strategy_twenty_five_configuration(source,
        approved_code_commit='d' * 40, approved_code_fingerprint='e' * 64,
        approval_reference='first-price-research')
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_twenty_five_manifest(result['payload']['strategy'])
    assert source.payload == before
    assert result['payload']['strategy']['parameters'] == before['strategy']['parameters']
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][key] == before[key]
    previous = before['strategy']['numbered_release']
    for key in previous:
        if key.endswith('_policy'):
            assert manifest[key] == previous[key]
    assert manifest['source_revision_id'] == PARENT_REVISION_ID
    assert manifest['source_payload_hash'] == PARENT_PAYLOAD_HASH
    assert manifest['first_price_break_policy'] == FIRST_PRICE_POLICY
    from src.trading_runtime.strategy_twenty_four_release import RULES as parent_rules
    from src.trading_runtime.strategy_premarket_quarter_risk_failure import POLICY_ID
    assert RULES == (*parent_rules, POLICY_ID) and len(set(RULES)) == len(RULES)
    from src.trading_runtime.strategy_registry import numbered_strategy_parent, numbered_strategy
    assert numbered_strategy_parent(25) == 24
    assert numbered_strategy(25).canonical_payload() == manifest['contract']
    changed = deepcopy(result['payload']['strategy'])
    changed['numbered_release']['first_price_break_policy']['session_scope'] = 'all_sessions'
    changed['numbered_release']['manifest_hash'] = sha256(canonical_json({
        k: v for k, v in changed['numbered_release'].items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='sealed contract'):
        verify_strategy_twenty_five_manifest(changed)


def test_twenty_five_retains_parent_session_management_contract():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    parent_contract, contract = numbered_fixed_strategy(24), numbered_fixed_strategy(25)
    for boundary in (100, 19_499_900, 19_500_000, 19_740_000, 43_200_100, 57_000_000, 57_300_000):
        for name in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
            assert getattr(contract, name)(boundary) == getattr(parent_contract, name)(boundary)
    assert contract.allows_followthrough_failure_exit
    assert not contract.allows_adds and not contract.allows_target_escalation
    assert not contract.allows_completed_30s_trailing
    assert contract.caps_entry_at_reference_ask
    assert numbered_session_exit_reason(25) == 'strategy_twenty_five_session_exit'
    with pytest.raises(ValueError, match='installed'):
        numbered_fixed_strategy(28)


def test_twenty_five_registered_executor_is_backtest_only_and_has_full_source_proof():
    from dataclasses import replace
    from test_fixed_numbered_registry import assignment
    from src.trading_runtime.strategy_registry import fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    registration = fixed_strategy_executor('early-squeeze-strategy', 25)
    assert registration.build([replace(assignment(), strategy_revision=25)], mode='backtest').contract.strategy_number == 25
    with pytest.raises(ValueError, match='Backtest-only'):
        registration.build([replace(assignment(), strategy_revision=25)], mode='live')
    assert len(certify_numbered_fixed_v4_projection(25)) == 64


def test_twenty_five_rejects_resigned_half_risk_policy_substitution():
    from src.trading_runtime.strategy_twenty_five_release import PREMARKET_FAILURE_POLICY
    result = compile_strategy_twenty_five_configuration(parent(),
        approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
        approval_reference='test-only-quarter-risk')
    strategy = deepcopy(result['payload']['strategy'])
    assert strategy['numbered_release']['premarket_failure_policy']==PREMARKET_FAILURE_POLICY
    strategy['numbered_release']['premarket_failure_policy']['premarket_threshold']='half_original_risk'
    manifest = strategy['numbered_release']
    manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError,match='sealed contract'):
        verify_strategy_twenty_five_manifest(strategy)
