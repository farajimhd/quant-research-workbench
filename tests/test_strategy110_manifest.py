"""Unpublished whole-tree comparison with controlled certified-parent transport."""
from copy import deepcopy
from dataclasses import fields

import pytest

from tests.test_strategy108_full_manifest import manifest
from src.trading_runtime.strategy_one_hundred_nine_contract import strategy_one_hundred_nine_contract
from src.trading_runtime.strategy_one_hundred_ten_contract import strategy_one_hundred_ten_contract
from src.trading_runtime.strategy_one_hundred_nine_release import derive_strategy_one_hundred_nine_configuration
from src.trading_runtime.strategy_one_hundred_ten_release import (
    derive_strategy_one_hundred_ten_configuration,
    verify_prepared_strategy_one_hundred_ten_configuration,
)


def test_every_typed_economic_and_execution_field_is_inherited():
    prior = strategy_one_hundred_nine_contract()
    candidate = strategy_one_hundred_ten_contract()
    for field in fields(prior):
        if field.name not in {'strategy_number', 'release'}:
            assert getattr(candidate, field.name) == getattr(prior, field.name), field.name
    assert candidate.release.input_contracts == prior.release.input_contracts
    assert candidate.release.rule_set_contracts == prior.release.rule_set_contracts
    assert candidate.release.approved_digest != prior.release.approved_digest


def test_unapproved_complete_source_seal_remains_closed():
    from src.backend.backtest_fixed_structural_lot_certification_v29 import (
        REQUIRED_SOURCE_FILES, certify_fixed_structural_lot_source,
    )
    assert 'src/trading_runtime/numbered_session_exit.py' in REQUIRED_SOURCE_FILES
    with pytest.raises(ValueError, match='unapproved; admission remains closed'):
        certify_fixed_structural_lot_source()


def test_complete_inherited_tree_differs_only_in_declared_identity(manifest):
    parent, approval, _ = manifest
    prior = derive_strategy_one_hundred_nine_configuration(parent, **approval)
    candidate = derive_strategy_one_hundred_ten_configuration(parent, **approval)
    assert verify_prepared_strategy_one_hundred_ten_configuration(parent, candidate['payload']) == candidate
    normalized = deepcopy(candidate['payload'])
    previous = prior['payload']
    identity = {
        'strategy': ('strategy_number', 'revision', 'profile_id', 'profile_revision', 'name'),
        'strategy_profile': ('profile_id', 'revision', 'definition_revision', 'name', 'description'),
        'run_plan': ('profile_id', 'name', 'description'),
    }
    for section, names in identity.items():
        for name in names:
            normalized[section][name] = deepcopy(previous[section][name])
    for name in ('contract', 'approved_digest', 'manifest_hash'):
        normalized['strategy']['numbered_release'][name] = deepcopy(previous['strategy']['numbered_release'][name])
    assert normalized == previous
    assert candidate['source_candidate_id'] == prior['source_candidate_id']
    assert candidate['source_candidate_hash'] == prior['source_candidate_hash']


@pytest.mark.parametrize('change', ['currency', 'lot_count', 'source_binding', 'unknown_parameter'])
def test_changed_economics_or_source_binding_are_rejected(manifest, change):
    parent, approval, _ = manifest
    candidate = derive_strategy_one_hundred_ten_configuration(parent, **approval)
    payload = deepcopy(candidate['payload'])
    if change == 'currency':
        payload['accounts']['currency'] = 'CAD'
    elif change == 'lot_count':
        payload['strategy']['parameters']['fixed_structural_lot_policy']['count'] += 1
    elif change == 'source_binding':
        payload['strategy']['numbered_release']['source_payload_hash'] = '0' * 64
    else:
        payload['strategy']['parameters']['unreviewed_parameter'] = True
    with pytest.raises(ValueError, match='complete inherited tree'):
        verify_prepared_strategy_one_hundred_ten_configuration(parent, payload)
