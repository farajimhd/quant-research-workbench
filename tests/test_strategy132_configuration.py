from copy import deepcopy

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_one_hundred_thirty_two_configuration as child
from src.trading_runtime import strategy_one_hundred_thirty_one_configuration as parent
from tests.test_strategy131_configuration import source_fixture as parent_input
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    prepared = parent.derive_strategy_one_hundred_thirty_one_configuration(
        parent_input(), **{**APPROVAL,
            'approved_code_commit': child.CONTROL_CODE_COMMIT,
            'approved_code_fingerprint': child.CONTROL_CODE_FINGERPRINT})
    return CertifiedStrategyOneConfiguration(child.CONTROL_REVISION.split(':')[1],
        child.CONTROL_PAYLOAD_HASH, prepared['node_hash'], prepared['source_candidate_id'],
        prepared['source_candidate_hash'], 'prepared-test-only', prepared['payload'])


def test_quality_successor_changes_only_declared_entry_gate():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_one_hundred_thirty_two_configuration(source, **APPROVAL)
    assert source.payload == before
    policies = child.declared_policies()
    quality = policies.pop('participation_admission_policy')
    assert policies == parent.declared_policies()
    assert quality['minimum_score'] == 1.0
    assert quality['missing_score'] == 'reject'
    assert quality['cash'] == 'unchanged-portfolio-authority'
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][key] == before[key]
    child.verify_strategy_one_hundred_thirty_two_manifest(result['payload']['strategy'])
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(132)
    assert contract.participation_admission_policy.minimum_score == 1.0
    assert numbered_fixed_strategy(131).participation_admission_policy is None


def test_quality_successor_rejects_changed_cutoff():
    result = child.derive_strategy_one_hundred_thirty_two_configuration(source_fixture(), **APPROVAL)
    result['payload']['strategy']['numbered_release']['participation_admission_policy']['minimum_score'] = 0.0
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_one_hundred_thirty_two_manifest(result['payload']['strategy'])
