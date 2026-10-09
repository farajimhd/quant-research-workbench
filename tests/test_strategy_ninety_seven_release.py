"""Prepared declaration only; native source and financial approval stay closed."""
from copy import deepcopy
from hashlib import sha256

import pytest

from test_strategy_sixty_four_release import source_fixture
from test_strategy_fifty_release import APPROVAL
from src.trading_runtime import strategy_ninety_seven_release as child
from src.trading_runtime import strategy_sixty_five_release as baseline
from src.trading_runtime.strategy_ninety_seven_contract import strategy_ninety_seven_contract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, MAX_TEXT_LENGTH


def test_complete_parent_economics_preserved_and_only_qualification_changes():
    parent = source_fixture()
    before = deepcopy(parent.payload)
    prepared = child.derive_strategy_ninety_seven_configuration(parent, **APPROVAL)
    assert parent.payload == before
    for key in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert prepared['payload'][key] == before[key]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for key in before['strategy'].keys() - identity:
        assert prepared['payload']['strategy'][key] == before['strategy'][key]
    actual, original = child.policies(), baseline.policies()
    original['automatic_market_policy']['gate']['qualification_mode'] = 'first_eligible_above_vwap'
    assert actual == original
    assert child.verify_prepared_strategy_ninety_seven_manifest(prepared['payload']['strategy'])
    assert 0 < len(child.BEHAVIOR) <= MAX_TEXT_LENGTH
    assert encode_nodes(prepared['payload'])


@pytest.mark.parametrize('field,value', [
    ('qualification_mode', 'vwap_cross'), ('minimum_trade_rate_10s', 0.),
    ('admission_ttl_ms', 600000), ('maximum_spread_bps', 10000.),
])
def test_resealed_qualification_liquidity_or_expiry_changes_rejected(field, value):
    strategy = child.derive_strategy_ninety_seven_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest['automatic_market_policy']['gate'][field] = value
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):
        child.verify_prepared_strategy_ninety_seven_manifest(strategy)


def test_prepared_factory_declares_once_session_and_no_extra_trading_behaviors():
    contract = strategy_ninety_seven_contract()
    assert contract.automatic_market_policy['gate']['qualification_mode'] == 'first_eligible_above_vwap'
    assert contract.automatic_market_policy['geometry_binding_policy'] == {'version': 'ladder-wait-first-complete-geometry-v1'}
    assert contract.automatic_entry_policy.payload() == baseline.policies()['automatic_entry_policy']
    assert not any((contract.allows_adds, contract.allows_reentry, contract.allows_trailing, contract.allows_replacement))
    assert contract.acquisition_cutoff(19500000)
    assert contract.liquidation_due(19740000)
    assert contract.acquisition_cutoff(57000000)
    assert contract.liquidation_due(57300000)
    assert 'completed-first-eligible-above-vwap@1' in child.RULE_CONTRACTS
    assert 'completed-vwap-below-above-cross@1' not in child.RULE_CONTRACTS


def test_installed_catalog_still_requires_immutable_normalized_publication():
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    strategy = child.derive_strategy_ninety_seven_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    assert child.verify_strategy_ninety_seven_manifest(strategy)
    class UnpublishedReader:
        def execute(self, query):
            assert query.startswith('SELECT ')
            return ''
    with pytest.raises(RuntimeError, match='exactly one immutable typed configuration release'):
        certify_numbered_configuration(UnpublishedReader(), 97)
