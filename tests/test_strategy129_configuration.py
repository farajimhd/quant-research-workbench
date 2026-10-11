from copy import deepcopy
from dataclasses import replace

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_one_hundred_twenty_nine_configuration as child
from src.trading_runtime import strategy_one_hundred_twenty_eight_configuration as parent
from tests.test_strategy128_configuration import source_fixture as parent_input
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    prepared = parent.derive_strategy_one_hundred_twenty_eight_configuration(
        parent_input(), **{**APPROVAL,
            'approved_code_commit': child.CONTROL_CODE_COMMIT,
            'approved_code_fingerprint': child.CONTROL_CODE_FINGERPRINT})
    return CertifiedStrategyOneConfiguration(child.CONTROL_REVISION.split(':')[1],
        child.CONTROL_PAYLOAD_HASH, prepared['node_hash'], prepared['source_candidate_id'],
        prepared['source_candidate_hash'], 'prepared-test-only', prepared['payload'])


def test_reader_isolation_successor_preserves_all_trading_policies_and_other_configuration():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_one_hundred_twenty_nine_configuration(source, **APPROVAL)
    assert source.payload == before
    policies = child.declared_policies()
    reader = policies['bounded_market_read_policy']
    assert policies == parent.declared_policies()
    assert reader['transport_failure'] == 'terminal-without-retry'
    assert reader['window_ms'] == 300000
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][key] == before[key]
    assert result['payload']['strategy']['strategy_number'] == 129
    old = before['strategy']['numbered_release']
    new = result['payload']['strategy']['numbered_release']
    for key in parent.manifest_policies():
        assert new[key] == old[key]
    child.verify_strategy_one_hundred_twenty_nine_manifest(result['payload']['strategy'])


def test_reader_isolation_successor_rejects_foreign_parent_and_changed_policy():
    source = source_fixture()
    with pytest.raises(ValueError, match='exact certified'):
        child.derive_strategy_one_hundred_twenty_nine_configuration(
            replace(source, payload_hash='f' * 64), **APPROVAL)
    result = child.derive_strategy_one_hundred_twenty_nine_configuration(source, **APPROVAL)
    result['payload']['strategy']['numbered_release']['publication_mode'] = 'live'
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_one_hundred_twenty_nine_manifest(result['payload']['strategy'])
