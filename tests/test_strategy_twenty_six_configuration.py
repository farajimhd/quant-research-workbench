"""The new release changes only the PM first-setup growth requirement."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest

from pipelines.strategy_one.strategy_twenty_five_configuration import compile_strategy_twenty_five_configuration
from pipelines.strategy_one.strategy_twenty_six_configuration import compile_strategy_twenty_six_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_twenty_six_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, FIRST_SETUP_GROWTH_POLICY,
    FIRST_PRICE_POLICY, RULES, verify_strategy_twenty_six_manifest,
)
from test_strategy_twenty_five_configuration import parent as twenty_fifth_parent


def parent():
    value = compile_strategy_twenty_five_configuration(twenty_fifth_parent(),
        approved_code_commit='d'*40, approved_code_fingerprint='e'*64,
        approval_reference='test-only-parent')
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH,value['node_hash'],value['source_candidate_id'],
        value['source_candidate_hash'],'test-only',value['payload'])


def configuration():
    return compile_strategy_twenty_six_configuration(parent(),
        approved_code_commit='d'*40,approved_code_fingerprint='e'*64,
        approval_reference='test-only-first-growth-10pct')


def test_exact_parent_preservation_and_truthful_changed_policy():
    source=parent()
    before=deepcopy(source.payload)
    result=compile_strategy_twenty_six_configuration(source,
        approved_code_commit='d'*40,approved_code_fingerprint='e'*64,
        approval_reference='test-only-first-growth-10pct')
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest=verify_strategy_twenty_six_manifest(result['payload']['strategy'])
    assert source.payload==before
    assert result['payload']['strategy']['parameters']==before['strategy']['parameters']
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:
        assert result['payload'][key]==before[key]
    previous=before['strategy']['numbered_release']
    for key in previous:
        if key.endswith('_policy') and key not in ('first_setup_growth_policy','first_price_break_policy'):
            assert manifest[key]==previous[key]
    assert manifest['first_setup_growth_policy']==FIRST_SETUP_GROWTH_POLICY
    assert manifest['first_setup_growth_policy']['fraction']==.10
    assert manifest['first_price_break_policy']==FIRST_PRICE_POLICY
    # Price geometry is unchanged; only the parent-momentum description changes.
    for key,value in previous['first_price_break_policy'].items():
        if key not in ('first_momentum','current_momentum','afterhours_policy'):
            assert manifest['first_price_break_policy'][key]==value
    from src.trading_runtime.strategy_twenty_five_release import RULES as parent_rules
    from src.trading_runtime.strategy_initial_momentum_growth import POLICY_ID as old
    from src.trading_runtime.strategy_initial_ten_percent import POLICY_ID as new
    assert RULES==tuple(rule for rule in parent_rules if rule!=old)+(new,)
    assert len(set(RULES))==len(RULES)
    from src.trading_runtime.strategy_registry import numbered_strategy_parent,numbered_strategy
    assert numbered_strategy_parent(26)==25
    assert numbered_strategy(26).canonical_payload()==manifest['contract']


@pytest.mark.parametrize('key,value',[
    ('first_setup_growth_policy',{'fraction':.50}),
    ('premarket_failure_policy',{'premarket_threshold':'half_original_risk'}),
    ('first_price_break_policy',{'comparison':'current_high > prior_high'}),
])
def test_resigned_policy_substitution_rejects(key,value):
    strategy=deepcopy(configuration()['payload']['strategy'])
    manifest=strategy['numbered_release']
    manifest[key].update(value)
    manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items()
        if k!='manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError,match='sealed contract'):
        verify_strategy_twenty_six_manifest(strategy)


def test_registered_executor_keeps_session_management_and_live_closed():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy,numbered_session_exit_reason
    from src.trading_runtime.strategy_registry import fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from test_fixed_numbered_registry import assignment
    previous,contract=numbered_fixed_strategy(25),numbered_fixed_strategy(26)
    for boundary in (100,19_499_900,19_500_000,19_740_000,43_200_100,57_000_000,57_300_000):
        for name in ('entry_allowed','acquisition_cutoff','liquidation_due'):
            assert getattr(contract,name)(boundary)==getattr(previous,name)(boundary)
    assert not contract.allows_adds and not contract.allows_target_escalation
    assert not contract.allows_completed_30s_trailing
    assert contract.caps_entry_at_reference_ask and contract.allows_followthrough_failure_exit
    assert numbered_session_exit_reason(26)=='strategy_twenty_six_session_exit'
    registration=fixed_strategy_executor('early-squeeze-strategy',26)
    assert registration.build([replace(assignment(),strategy_revision=26)],mode='backtest').contract==contract
    with pytest.raises(ValueError,match='Backtest-only'):
        registration.build([replace(assignment(),strategy_revision=26)],mode='live')
    assert len(certify_numbered_fixed_v4_projection(26))==64
