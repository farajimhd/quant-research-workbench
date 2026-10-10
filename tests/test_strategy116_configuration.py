"""Prepared graphs and unchanged financial rules; no native source approval."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import strategy_one_hundred_sixteen_configuration as child
from src.trading_runtime import strategy_one_hundred_fifteen_configuration as parent
from src.trading_runtime.strategy_one_hundred_sixteen_contract import strategy_one_hundred_sixteen_contract
from src.trading_runtime.strategy_one_hundred_fifteen_contract import strategy_one_hundred_fifteen_contract
from src.trading_runtime.original_risk_verification_reuse import POLICY_KEY, RULE
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from test_strategy115_configuration import source_fixture as parent_input
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    prepared = parent.derive_strategy_one_hundred_fifteen_configuration(parent_input(),
        **{**APPROVAL, 'approved_code_commit': child.CONTROL_CODE_COMMIT,
           'approved_code_fingerprint': child.CONTROL_CODE_FINGERPRINT})
    # Like existing prepared-parent fixtures, this carries the published identity
    # to exercise graph derivation. It is not a database/source certificate.
    return CertifiedStrategyOneConfiguration(child.CONTROL_REVISION.split(':')[1],
        child.CONTROL_PAYLOAD_HASH, prepared['node_hash'], prepared['source_candidate_id'],
        prepared['source_candidate_hash'], 'prepared-test-only', prepared['payload'])


def test_only_reuse_policy_and_release_identity_change():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_one_hundred_sixteen_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert payload[key] == before[key]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for key in before['strategy'].keys() - identity:
        assert payload['strategy'][key] == before['strategy'][key]
    old = before['strategy']['numbered_release']
    new = payload['strategy']['numbered_release']
    for key in parent.manifest_policies():
        assert new[key] == old[key]
    assert new[POLICY_KEY]['max_requests'] == 96
    assert new['contract']['rule_set_contracts'] == old['contract']['rule_set_contracts'] + [RULE]
    assert payload['strategy_profile']['lifecycle'] == before['strategy_profile']['lifecycle']
    assert new['publication_mode'] == 'backtest_only'
    assert child.verify_prepared_strategy_one_hundred_sixteen_manifest(payload['strategy']) == new


def test_typed_contract_retains_financial_policies():
    old, new = strategy_one_hundred_fifteen_contract(), strategy_one_hundred_sixteen_contract()
    assert new.strategy_number == 116
    assert new.original_risk_verification_reuse_policy.max_bytes == 1048576
    assert old.original_risk_verification_reuse_policy is None
    assert new.confirmed_original_risk_policy == old.confirmed_original_risk_policy
    assert new.entry_spread_risk_policy == old.entry_spread_risk_policy


@pytest.mark.parametrize('changes', [dict(payload_hash='f'*64), dict(attempt_id='00000000-0000-4000-8000-000000000000')])
def test_foreign_parent_identity_rejected(changes):
    with pytest.raises(ValueError, match='exact certified Strategy115'):
        child.derive_strategy_one_hundred_sixteen_configuration(replace(source_fixture(), **changes), **APPROVAL)


def test_forged_reuse_overflow_behavior_rejected():
    result = child.derive_strategy_one_hundred_sixteen_configuration(source_fixture(), **APPROVAL)
    result['payload']['strategy']['numbered_release'][POLICY_KEY]['overflow'] = 'skip_verification'
    with pytest.raises(ValueError, match='policy or source approval'):
        child.verify_prepared_strategy_one_hundred_sixteen_manifest(result['payload']['strategy'])
